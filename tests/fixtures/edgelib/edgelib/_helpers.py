import sys


def install_names():
    """Adds names to the calling package at import time (like anyio's lazy importer)."""
    module = sys.modules[sys._getframe(1).f_globals["__name__"]]
    module.injected = 1
    return True


def register(module_name, names):
    for n in names:
        setattr(sys.modules[module_name], n, n)
