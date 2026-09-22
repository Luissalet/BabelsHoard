"""Class attributes added after the class body (like datetime.timezone.utc)."""


class Color:
    RED = 1


Color.BLUE = 2
setattr(Color, "GREEN", 3)


class Flexible:
    pass


for _name in ("a", "b"):
    setattr(Flexible, _name, 0)
