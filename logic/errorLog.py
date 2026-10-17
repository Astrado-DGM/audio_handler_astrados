"""Registro de errores en disco (logs/astrados.log, rotativo).

Captura tambien las excepciones no manejadas del hilo principal, de los hilos
de audio y de los callbacks de Tk, que en la version instalada (sin consola)
de otro modo se perderian.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "astrados.log"
_log_path: Path | None = None


def setup_logging(log_dir: Path) -> Path:
    global _log_path
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    _log_path = log_dir / LOG_NAME

    handler = RotatingFileHandler(_log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for existing in list(root.handlers):
        if isinstance(existing, RotatingFileHandler):
            root.removeHandler(existing)
    root.addHandler(handler)

    def log_uncaught(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("astrados").critical("Error no manejado", exc_info=(exc_type, exc_value, exc_traceback))

    def log_thread_exception(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("astrados.hilos").error(
            "Error en el hilo %s", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_uncaught
    threading.excepthook = log_thread_exception
    return _log_path


def log_path() -> Path | None:
    return _log_path


def read_log(max_lines: int = 400) -> str:
    if _log_path is None or not _log_path.exists():
        return ""
    lines = _log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def clear_log() -> None:
    if _log_path is None:
        return
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.flush()
            handler.stream.seek(0)
            handler.stream.truncate()


class RateLimitedLogger:
    """Para callbacks de audio: no escribir a disco en cada bloque (underflows repetidos)."""

    def __init__(self, logger: logging.Logger, interval_seconds: float = 10.0) -> None:
        self.logger = logger
        self.interval = interval_seconds
        self._last: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def warning(self, key: str, message: str) -> None:
        now = time.monotonic()
        if now - self._last.get(key, -1e9) < self.interval:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return
        suppressed = self._suppressed.pop(key, 0)
        suffix = f" (+{suppressed} repetidos)" if suppressed else ""
        self._last[key] = now
        self.logger.warning("%s%s", message, suffix)
