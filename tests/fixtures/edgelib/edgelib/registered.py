"""Namespace filled by passing __name__ to a registration call (like ssl's enum _convert_)."""
import logging

from ._helpers import register

logger = logging.getLogger(__name__)
register(__name__, ["ALPHA", "BETA"])


def plain():
    ...
