"""fakelib 1.x — test fixture for Babel's Hoard.

Mimics a library that changes its API between major versions, the way
pandas removed ``DataFrame.append`` in 2.x.
"""

__version__ = "1.0.0"


class Table:
    """A minimal table type."""

    def __init__(self, rows=None):
        self.rows = rows or []

    def append(self, item, ignore_index=False):
        """Append one row to the table in place.

        Parameters:
            item: The row to add.
            ignore_index: Reset the row index after appending.
        """
        self.rows.append(item)
        return self

    def size(self):
        """Return the number of rows."""
        return len(self.rows)
