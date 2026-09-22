"""Star-imports a compiled module Babel cannot read (like os from posix)."""
from _edgelib_speedups import *  # noqa: F401,F403


def pure():
    ...
