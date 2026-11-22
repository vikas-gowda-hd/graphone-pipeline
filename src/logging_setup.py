"""Clean, consistent logging for console + file."""
import logging
import sys

from .config import LOG_DIR


def setup_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_DIR / "pipeline.log")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
