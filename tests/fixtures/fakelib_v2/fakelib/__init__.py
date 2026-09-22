"""fakelib 2.x — test fixture for Babel's Hoard.

``Table.append`` was removed in favour of the module-level ``concat``
function, the way pandas moved from ``DataFrame.append`` to ``pd.concat``.
"""

__version__ = "2.0.0"


class Table:
    """A minimal table type."""

    def __init__(self, rows=None):
        self.rows = rows or []

    def size(self):
        """Return the number of rows."""
        return len(self.rows)


def concat(tables, ignore_index=False):
    """Concatenate several tables into one new table.

    Parameters:
        tables: The tables to concatenate, in order.
        ignore_index: Reset the row index in the result.
    """
    rows = []
    for t in tables:
        rows.extend(t.rows)
    return Table(rows)
