"""edgelib - a fixture package exercising every "can we be sure?" case the
code checker must get right. Never imported by the tests: Babel reads it
statically."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Self, overload

from ._impl import Engine as Engine
from . import lazy, magic, starry, compiled_star

__all__ = [
    "Engine", "Point", "Session", "Dynamic", "Settable", "Factory", "Meta", "Decorated",
    "overloaded", "decorated", "renamed", "connect", "fetch", "old_function", "lazy",
    "magic", "starry", "compiled_star", "Builder", "Opener", "NotSelf", "Callable", "Child",
]


@dataclass
class Point:
    x: int
    y: int = 0


class Session:
    """A context manager whose __enter__ returns itself."""

    def __init__(self, url: str, *, timeout: float = 5.0):
        self.url = url

    def open(self):
        self.opened_later = True

    def send(self, data: bytes, /, retries: int = 0) -> "Response":
        ...

    @staticmethod
    def version(fmt: str) -> str:
        ...

    @classmethod
    def from_env(cls, name: str) -> Session:
        ...

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc):
        return False


class Response:
    status: int

    def json(self) -> dict:
        ...


class Opener:
    def __enter__(self):
        return self


class NotSelf:
    """__enter__ returns something else (like tempfile.TemporaryDirectory)."""

    def __enter__(self) -> str:
        return "name"


class Dynamic:
    def known(self): ...

    def __getattr__(self, name):
        return 1


class Settable:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class Factory:
    def __new__(cls, kind: str):
        return object.__new__(cls)

    def __init__(self, other_signature: int):
        ...


class _MetaType(type):
    def __call__(cls, *args, **kwargs):
        ...


class Meta(metaclass=_MetaType):
    def __init__(self, a: int):
        ...


class Child(Unknown):  # noqa: F821 - base cannot be resolved statically
    def own(self): ...


def _wrapper(fn):
    return fn


class Decorated:
    @_wrapper
    def method(self, a): ...


class Builder:
    def step(self, n: int) -> Self:
        ...

    def build(self) -> Point:
        ...


class Callable:
    def __call__(self, value: int) -> int:
        ...


@overload
def overloaded(a: int) -> int: ...
@overload
def overloaded(a: str, *, sep: str = ",") -> str: ...
def overloaded(*args, **kwargs):
    ...


@_wrapper
def decorated(a, b):
    ...


def _renaming(old, new):
    def deco(fn):
        return fn
    return deco


@_renaming("old", "new")
def renamed(new=None):
    ...


def connect(host: str, port: int = 80) -> Session:
    """Connect.

    Args:
        host: The host name.
        port: The port.
    """


async def fetch(url: str) -> Response:
    ...


def old_function():
    """Old."""


try:
    from warnings import deprecated as _deprecated
except ImportError:  # pragma: no cover
    _deprecated = None
