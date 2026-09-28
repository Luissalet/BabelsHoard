"""Read-only checks for simple JSON translation catalogues."""
from __future__ import annotations

import json
import re
from pathlib import Path

_SIMPLE_PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_.-]*)\}(?!\})")
_LOCALE = re.compile(r"[A-Za-z0-9_-]+")
_MAX_FILES = 40
_MAX_BYTES = 2_000_000


def _messages(value: object, prefix: str = "") -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Each catalogue must be a JSON object of nested string messages.")
    result: dict[str, str] = {}
    for key, child in value.items():
        if not isinstance(key, str) or not key or "." in key:
            raise ValueError("Catalogue keys must be nonempty strings without dots.")
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            result.update(_messages(child, path))
        elif isinstance(child, str):
            result[path] = child
        else:
            raise ValueError(f"{path}: expected a string or nested object.")
    return result


def _placeholders(message: str) -> set[str] | None:
    names = set(_SIMPLE_PLACEHOLDER.findall(message))
    remainder = _SIMPLE_PLACEHOLDER.sub("", message)
    return None if "{" in remainder or "}" in remainder else names


def check_locales(path: str | Path, source_locale: str = "en", offset: int = 0, limit: int = 50) -> dict:
    if not _LOCALE.fullmatch(source_locale):
        raise ValueError("source_locale must be a locale name such as en or en-US.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Catalogue directory does not exist.")
    files = sorted(root.glob("*.json"))
    if len(files) > _MAX_FILES:
        raise ValueError(f"At most {_MAX_FILES} JSON locale files are supported.")
    source_file = root / f"{source_locale}.json"
    if source_file not in files:
        raise ValueError(f"Source catalogue {source_locale}.json was not found.")
    if len(files) < 2:
        raise ValueError("At least one target locale catalogue is needed.")
    catalogues: dict[str, dict[str, str]] = {}
    for file in files:
        if file.stat().st_size > _MAX_BYTES:
            raise ValueError(f"{file.name} exceeds the 2 MB catalogue limit.")
        try:
            catalogues[file.stem] = _messages(json.loads(file.read_text(encoding="utf-8-sig")))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError(f"{file.name} is not valid UTF-8 JSON: {exc}") from exc
    source = catalogues[source_locale]
    findings = []
    unchecked = 0
    targets = sorted(locale for locale in catalogues if locale != source_locale)
    for locale in targets:
        target = catalogues[locale]
        for key in sorted(source.keys() - target.keys()):
            findings.append({"locale": locale, "key": key, "kind": "missing_key"})
        for key in sorted(target.keys() - source.keys()):
            findings.append({"locale": locale, "key": key, "kind": "extra_key"})
        for key in sorted(source.keys() & target.keys()):
            expected = _placeholders(source[key])
            actual = _placeholders(target[key])
            if expected is None or actual is None:
                unchecked += 1
            elif expected != actual:
                findings.append({"locale": locale, "key": key, "kind": "placeholder_mismatch",
                                 "expected": sorted(expected), "actual": sorted(actual)})
    findings.sort(key=lambda row: (row["locale"], row["key"], row["kind"]))
    total = len(findings)
    return {"path": str(root), "source_locale": source_locale, "locales": targets,
            "source_keys": len(source), "total": total, "unchecked_messages": unchecked,
            "offset": offset, "findings": findings[offset:offset + limit],
            "truncated": offset + limit < total, "has_more": offset + limit < total,
            "next_offset": offset + limit if offset + limit < total else None}
