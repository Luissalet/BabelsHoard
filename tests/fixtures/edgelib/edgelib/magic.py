"""Creates names through globals()."""

for _n in ("alpha", "beta"):
    globals()[_n] = _n


def visible():
    ...
