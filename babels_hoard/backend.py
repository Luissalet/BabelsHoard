"""Shared model backend (Hoard Link) integration.

Babel's Hoard uses two capabilities from the shared backend: ``llm`` (to
answer "Ask the docs" questions) and ``embeddings`` (to semantically
re-rank search hits before answering). Everything else in the app works
with no model connected at all.

This module owns the ``backend.json`` file (manual overrides, read/written
without ever handing the Faustus token back to a caller), the one shared
:class:`~hoard_link.Link` per app, and the "Ask the docs" feature itself,
which is the only place this app calls a model.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Optional

from .hoard_link import BackendError, Link, LinkConfig, Unavailable

USED_CAPABILITIES = ("llm", "embeddings")

# Entries fed to the model are capped hard: the consumer may be a modest
# local model with a small context window.
_MAX_CANDIDATES = 50
_MAX_CONTEXT_ENTRIES = 6
_MAX_SIGNATURE_CHARS = 200
_MAX_SUMMARY_CHARS = 220

_CITATION_RE = re.compile(r"\[([^\[\]\s]{1,200})\]")


def config_path(data_dir: Path) -> Path:
    return Path(data_dir) / "backend.json"


def config_error(data_dir: Path) -> Optional[str]:
    """Why ``backend.json`` cannot be used, or ``None`` when it is fine.

    A hand-edited file can be broken (bad JSON, a malformed ``command``);
    the app must still start, so :func:`build_link` falls back to
    auto-detection and the Settings screen shows this sentence.
    """
    try:
        LinkConfig.load(config_path(data_dir), env={}, app="check")
    except ValueError as exc:
        return str(exc)
    return None


def build_link(data_dir: Path, app: str = "babel") -> Link:
    """Create the app's one :class:`Link`, from ``backend.json`` + env.

    An unusable ``backend.json`` never stops the app from starting: the
    Link then uses only the environment and auto-detection.
    """
    try:
        config = LinkConfig.load(config_path(data_dir), env=os.environ, app=app)
    except ValueError:
        config = LinkConfig.load(None, env=os.environ, app=app)
    return Link(config)


def _read_raw(data_dir: Path) -> dict[str, Any]:
    path = config_path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _drop_empty(value: Any) -> Any:
    """Remove ``""`` values (a cleared form field) and the empty sections
    they leave behind, so clearing a field really removes the override."""
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        item = _drop_empty(item)
        if item == "" or (isinstance(item, dict) and not item):
            continue
        out[key] = item
    return out


def save_overrides(data_dir: Path, patch: dict[str, Any]) -> None:
    """Merge ``patch`` into ``backend.json`` (created if missing).

    An empty string removes that key instead of saving it: ``faustus.token``
    of ``""`` forgets the stored token, a cleared URL/model field removes
    that override. The merged file is validated with the same loader the
    Link uses *before* it replaces the old one, so a bad value raises
    ``ValueError`` and leaves the working configuration untouched.
    """
    merged = _drop_empty(_deep_merge(_read_raw(data_dir), patch))
    path = config_path(data_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        LinkConfig.load(tmp, env={}, app="check")
    except ValueError as exc:
        tmp.unlink()
        raise ValueError(str(exc).replace(str(tmp), path.name)) from exc
    os.replace(tmp, path)


def config_for_ui(data_dir: Path) -> dict[str, Any]:
    """The manual-override config for the Settings screen.

    The Faustus token is never sent to the browser: only whether one is
    set (``token_set``).
    """
    raw = _read_raw(data_dir)
    faustus = dict(raw.get("faustus") or {})
    token_set = bool(faustus.pop("token", None))
    faustus["token_set"] = token_set
    caps = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    return {
        "only_resident": raw.get("only_resident", True),
        "faustus": faustus,
        "comfy": raw.get("comfy") or {},
        "capabilities": {cap: caps.get(cap, {}) for cap in USED_CAPABILITIES},
    }


async def backend_status(link: Link, data_dir: Optional[Path] = None) -> dict[str, Any]:
    full = await link.status()
    return {
        "used": list(USED_CAPABILITIES),
        "capabilities": {cap: full[cap] for cap in USED_CAPABILITIES if cap in full},
        "all_capabilities": full,
        "config_error": config_error(data_dir) if data_dir is not None else None,
    }


# ------------------------------------------------------------- ask the docs
def _candidate_text(hit: dict[str, Any]) -> str:
    parts = [hit.get("qualname") or "", hit.get("signature") or "", hit.get("summary") or ""]
    return " - ".join(p for p in parts if p)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


_RRF_K = 60


def _fuse(candidates: list[dict[str, Any]], similarities: list[float]) -> list[dict[str, Any]]:
    """Hybrid order: reciprocal-rank fusion of the lexical (FTS) order and
    the embedding-similarity order.

    Neither signal alone decides: an exact identifier match that a small
    embedding model scores poorly still stays near the top, and a hit
    phrased differently from the question can climb past lexical noise.
    """
    by_similarity = sorted(range(len(candidates)), key=lambda i: similarities[i], reverse=True)
    semantic_rank = {i: rank for rank, i in enumerate(by_similarity)}
    fused = sorted(
        range(len(candidates)),
        key=lambda i: 1.0 / (_RRF_K + i) + 1.0 / (_RRF_K + semantic_rank[i]),
        reverse=True,
    )
    return [candidates[i] for i in fused]


def _context_block(hits: list[dict[str, Any]]) -> str:
    lines = []
    for h in hits:
        sig = (h.get("signature") or "")[:_MAX_SIGNATURE_CHARS]
        summary = (h.get("summary") or "")[:_MAX_SUMMARY_CHARS]
        lib = h.get("library") or "?"
        lines.append(
            f"id: {h['id']}\n{h['qualname']} ({h['kind']}, {lib})\n"
            + (f"signature: {sig}\n" if sig else "")
            + (f"summary: {summary}\n" if summary else "")
        )
    return "\n".join(lines)


ASK_SYSTEM_PROMPT = (
    "You answer a developer's question about installed library APIs using ONLY the "
    "numbered excerpts below, which come from Babel's Hoard's index of what is "
    "actually installed. Every claim you make must be followed by the id of the "
    "excerpt it comes from, in square brackets, e.g. [python:httpx.Client.get]. "
    "If the excerpts do not answer the question, say so plainly instead of guessing "
    "from general knowledge. Be concise."
)


async def ask_the_docs(
    conn,
    link: Link,
    *,
    question: str,
    search_fn,
    library: Optional[str] = None,
    ecosystem: Optional[str] = None,
    kind: Optional[str] = None,
    env: Optional[str] = None,
) -> dict[str, Any]:
    """"Ask the docs": search hits + the question, answered by the ``llm``
    capability with citations; re-ranked by ``embeddings`` when it resolves.

    ``search_fn`` is ``search.search`` (injected so this module never
    imports FastAPI or forces a particular search implementation).
    """
    question = question.strip()
    if not question:
        return {"available": False, "reason": "Type a question first."}

    llm_res = await link.resolve("llm")
    if not llm_res.resolved:
        return {"available": False, "reason": llm_res.reason}

    found = search_fn(conn, question, library=library, ecosystem=ecosystem, kind=kind, env=env, limit=_MAX_CANDIDATES)
    candidates: list[dict[str, Any]] = found.get("results") or []
    if not candidates:
        return {
            "available": True,
            "answered": False,
            "message": "No indexed entries matched this query, so there is nothing to ask about yet.",
            "did_you_mean": found.get("did_you_mean") or [],
        }

    semantic_rerank = False
    embed_res = await link.resolve("embeddings")
    if embed_res.resolved:
        try:
            texts = [question] + [_candidate_text(h) for h in candidates]
            vectors = await link.embed(texts)
            if len(vectors) == len(texts):
                candidates = _fuse(candidates, [_cosine(vectors[0], v) for v in vectors[1:]])
                semantic_rerank = True
        except Exception:  # noqa: BLE001 - fall back to lexical order silently
            semantic_rerank = False

    context_hits = candidates[:_MAX_CONTEXT_ENTRIES]
    prompt = (
        f"Question: {question}\n\nExcerpts:\n{_context_block(context_hits)}\n\n"
        "Answer the question, citing excerpt ids in [brackets]."
    )
    try:
        result = await link.chat(
            [
                {"role": "system", "content": ASK_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.2,
            capability="llm",
        )
    except (Unavailable, BackendError) as exc:
        # Resolved a moment ago but the call itself failed (server went
        # away, a stale explicit URL, ...): a soft failure, not a 500 - the
        # entries themselves are still useful to show.
        return {
            "available": True,
            "answered": False,
            "message": f"The model call failed: {exc}",
            "entries": context_hits,
            "semantic_rerank": semantic_rerank,
        }
    valid_ids = {h["id"] for h in context_hits}
    cited = [m for m in dict.fromkeys(_CITATION_RE.findall(result.text)) if m in valid_ids]
    return {
        "available": True,
        "answered": True,
        "question": question,
        "answer": result.text,
        "cited": cited,
        "entries": context_hits,
        "semantic_rerank": semantic_rerank,
        "model": result.model,
        "provider": result.provider,
    }
