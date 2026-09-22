"""A PEP 562 lazy module: names beyond the static ones come from __getattr__."""

__all__ = ["static_name"]


def static_name():
    ...


def __getattr__(name):
    raise AttributeError(name)
