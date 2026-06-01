from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

DEFAULT_LOGGER_NAME = "cathy"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "log"
LOG_FORMAT = "%(asctime)s [%(thread)d] - %(filename)s[line:%(lineno)d] - %(levelname)s: %(message)s"

_CONFIGURED = False


class _ExactLevelFilter(logging.Filter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == self.level


def _coerce_level(log_level: int | str) -> int:
    if isinstance(log_level, int):
        return log_level
    level = logging.getLevelName(str(log_level).upper())
    return level if isinstance(level, int) else logging.INFO


def _rotating_file_handler(path: Path, level: int, formatter: logging.Formatter) -> TimedRotatingFileHandler:
    handler = TimedRotatingFileHandler(
        filename=path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def configure_logging(
    log_level: int | str = logging.INFO,
    *,
    log_dir: str | Path | None = None,
    console: bool = True,
    force: bool = False,
) -> logging.Logger:
    """配置 CathyAgent 运行日志。

    - console: 输出到 stderr，不污染 stdout 上的 REPL 对话。
    - all.log: 记录通过当前 level 的全部日志。
    - debug/info/warning/error/critical.log: 仅记录对应级别日志。
    """
    global _CONFIGURED
    logger = logging.getLogger(DEFAULT_LOGGER_NAME)
    if _CONFIGURED and not force:
        return logger

    level = _coerce_level(log_level)
    target_dir = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    all_handler = _rotating_file_handler(target_dir / "all.log", level, formatter)
    logger.addHandler(all_handler)

    level_files = {
        logging.DEBUG: "debug.log",
        logging.INFO: "info.log",
        logging.WARNING: "warning.log",
        logging.ERROR: "error.log",
        logging.CRITICAL: "critical.log",
    }
    for exact_level, filename in level_files.items():
        handler = _rotating_file_handler(target_dir / filename, exact_level, formatter)
        handler.addFilter(_ExactLevelFilter(exact_level))
        logger.addHandler(handler)

    _CONFIGURED = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取统一命名空间下的 logger。"""
    if not name:
        return logging.getLogger(DEFAULT_LOGGER_NAME)
    if name == DEFAULT_LOGGER_NAME or name.startswith(f"{DEFAULT_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{DEFAULT_LOGGER_NAME}.{name}")
