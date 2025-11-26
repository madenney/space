import logging
import sys
from pathlib import Path


def setup_logger(run_dir: Path, to_file: bool = True) -> logging.Logger:
    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    class _NoEmpty(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
            return bool(record.getMessage().strip())

    file_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    stream_fmt = logging.Formatter("%(message)s")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(stream_fmt)
    filt = _NoEmpty()
    logger.addFilter(filt)
    stream_handler.addFilter(filt)
    if to_file:
        file_handler = logging.FileHandler(run_dir / "run.log")
        file_handler.setFormatter(file_fmt)
        file_handler.addFilter(filt)
        logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def log_status(logger: logging.Logger, msg: str, overwrite: bool = False) -> None:
    """Log a status message; optionally overwrite the previous line in the console."""
    if overwrite:
        sys.stdout.write("\r" + msg)
        sys.stdout.flush()
    else:
        logger.info(msg)
