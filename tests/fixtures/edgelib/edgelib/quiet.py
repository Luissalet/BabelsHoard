"""Only benign module-level calls: stays fully static."""
import logging

logger = logging.getLogger(__name__)


def plain():
    ...
