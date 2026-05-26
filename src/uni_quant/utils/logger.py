"""Structured logging via loguru with sensible defaults."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger as _logger

_configured = False


def _configure(log_dir: Path | None = None, level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    _logger.remove()
    _logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "{message}"
        ),
    )
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        _logger.add(
            log_dir / "uni_quant_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="30 days",
            level=level,
            enqueue=True,
            backtrace=True,
            diagnose=False,
        )
    _configured = True


def get_logger(name: str | None = None, *, log_dir: Path | None = None, level: str = "INFO"):
    _configure(log_dir=log_dir, level=level)
    return _logger.bind(name=name) if name else _logger
