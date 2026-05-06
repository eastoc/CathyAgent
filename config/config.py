"""配置加载：YAML + ${ENV} 占位符 + .env 自动注入。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"
DEFAULT_ENV_PATH = CONFIG_DIR / ".env"


def _load_dotenv(path: Path) -> None:
    """轻量 .env 加载器，避免引入 python-dotenv 依赖。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: Path | str | None = None) -> dict:
    """加载配置；自动注入 .env，并展开 ${ENV} 占位符。"""
    _load_dotenv(DEFAULT_ENV_PATH)
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return _expand_env(raw)


def get_llm(
    cfg: dict,
    *,
    name: str | None = None,
    role: str | None = None,
) -> dict:
    """按 name 或 role 在 LLM_API 列表中筛选一条配置。"""
    entries = cfg.get("LLM_API") or []
    for entry in entries:
        if name and entry.get("name") != name:
            continue
        if role and role not in (entry.get("roles") or []):
            continue
        return entry
    raise ValueError(f"未找到匹配的 LLM 配置 (name={name}, role={role})")
