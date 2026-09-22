"""A fully offline stand-in for :class:`hoard_link.Link`, for tests.

Built from the same plain dataclasses the real Link returns, so a test's
expectations (``.reason``, ``.state``, ``ChatResult.text`` ...) match
production shapes exactly, without any of Link's network probing.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from babels_hoard.hoard_link import CAPABILITIES, ChatResult, Resolution, Usage


def unresolved(capability: str, reason: str = "not configured in this test") -> Resolution:
    return Resolution(
        capability=capability, provider=None, url=None, model=None, api=None,
        state="unavailable", reason=f"{capability} {reason}", details={"reasons": [reason]},
    )


def resolved(capability: str, *, provider: str = "test", model: str = "test-model", reason: str | None = None) -> Resolution:
    return Resolution(
        capability=capability, provider=provider, url="http://127.0.0.1:9/v1/chat/completions",
        model=model, api="openai", state="resolved",
        reason=reason or f"{capability} -> {provider} ({model}), test configuration", details={"source": "test"},
    )


class FakeLink:
    """Duck-compatible with the subset of Link the app calls."""

    def __init__(
        self,
        *,
        resolutions: Optional[dict[str, Resolution]] = None,
        chat_result: Optional[ChatResult] = None,
        chat_error: Optional[BaseException] = None,
        embed_vectors: Optional[list[list[float]]] = None,
    ):
        self._resolutions = resolutions or {}
        self._chat_result = chat_result or ChatResult(
            text="Use httpx.Client.get(url) [test:entry.one]", model="test-model", provider="test",
            usage=Usage(), elapsed_ms=1.0,
        )
        self._chat_error = chat_error
        self._embed_vectors = embed_vectors
        self.chat_calls: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
        self.embed_calls: list[list[str]] = []
        self.closed = False

    async def resolve(self, capability: str) -> Resolution:
        return self._resolutions.get(capability, unresolved(capability))

    async def status(self) -> dict[str, Any]:
        return {cap: (await self.resolve(cap)).to_dict() for cap in CAPABILITIES}

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        self.chat_calls.append((messages, kwargs))
        if self._chat_error is not None:
            raise self._chat_error
        return self._chat_result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(texts)
        if self._embed_vectors is not None:
            return self._embed_vectors
        return [[1.0, 0.0] for _ in texts]

    async def wait_idle(self, capability: str, max_wait_s: float = 30.0) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True


def factory_of(*links: FakeLink) -> Callable[[], FakeLink]:
    """A link_factory that hands out ``links`` in order, then repeats the last."""

    state = {"i": 0}

    def _next() -> FakeLink:
        i = min(state["i"], len(links) - 1)
        state["i"] += 1
        return links[i]

    return _next
