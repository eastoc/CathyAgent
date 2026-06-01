"""配置加载：YAML + ${ENV} 占位符 + .env 自动注入。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")
_LLM_META_KEYS = frozenset({"provider", "config_dir", "temperature", "max_tokens", "roles"})
_LLM_DEFAULT_KEYS = ("temperature", "max_tokens", "roles")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"
DEFAULT_ENV_PATH = CONFIG_DIR / ".env"
DEFAULT_LLM_CONFIG_DIR = CONFIG_DIR / "llm"


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


def _read_yaml_file(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"LLM 子配置必须是 YAML 映射: {path}")
    return payload


def _resolve_llm_config_dir(cfg_dir: Path, llm_section: dict) -> Path:
    config_dir = llm_section.get("config_dir", "llm")
    llm_dir = Path(str(config_dir))
    if not llm_dir.is_absolute():
        llm_dir = cfg_dir / llm_dir
    return llm_dir


def _llm_shared_defaults(llm_section: dict) -> dict[str, Any]:
    return {
        key: llm_section[key]
        for key in _LLM_DEFAULT_KEYS
        if key in llm_section
    }


def _apply_llm_defaults(entry: dict, defaults: dict[str, Any]) -> dict:
    if not defaults:
        return dict(entry)
    return {**defaults, **entry}


def _merge_llm_provider_files(raw: dict, cfg_dir: Path) -> dict:
    """把 config/llm/*.yaml 合并进 LLM 段；通用参数从 config.yaml 注入各供应商。"""
    llm_section = raw.get("LLM")
    if not isinstance(llm_section, dict):
        return raw

    defaults = _llm_shared_defaults(llm_section)
    merged: dict[str, Any] = {
        key: value for key, value in llm_section.items() if key in _LLM_META_KEYS
    }

    llm_dir = _resolve_llm_config_dir(cfg_dir, llm_section)
    if llm_dir.is_dir():
        for provider_path in sorted(llm_dir.glob("*.yaml")):
            merged[provider_path.stem] = _apply_llm_defaults(
                _read_yaml_file(provider_path),
                defaults,
            )

    for key, value in llm_section.items():
        if key in _LLM_META_KEYS:
            continue
        if isinstance(value, str):
            ref_path = Path(value)
            if not ref_path.is_absolute():
                ref_path = cfg_dir / ref_path
            merged[key] = _apply_llm_defaults(_read_yaml_file(ref_path), defaults)
        elif isinstance(value, dict):
            base = _apply_llm_defaults(dict(merged.get(key) or {}), defaults)
            base.update(value)
            merged[key] = base

    raw["LLM"] = merged
    return raw


def load_config(path: Path | str | None = None) -> dict:
    """加载配置；自动注入 .env，合并 LLM 子配置，并展开 ${ENV} 占位符。"""
    _load_dotenv(DEFAULT_ENV_PATH)
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    cfg_dir = cfg_path.parent
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw = _merge_llm_provider_files(raw, cfg_dir)
    return _expand_env(raw)


def _llm_provider_entries(cfg: dict) -> dict[str, dict]:
    # 返回所有 LLM provider条目的字典（过滤掉元信息键，只保留dict类型）。
    llm_section = cfg.get("LLM")
    if not isinstance(llm_section, dict):
        return {}
    return {
        str(name): dict(entry)
        for name, entry in llm_section.items()
        if name not in _LLM_META_KEYS and isinstance(entry, dict)
    }


def get_llm_provider(cfg: dict) -> str:
    """返回当前选中的 LLM 供应商名（LLM.provider）。"""
    llm_section = cfg.get("LLM")
    if isinstance(llm_section, dict):
        provider = llm_section.get("provider")
        if provider:
            return str(provider)
    legacy = cfg.get("LLM_PROVIDER")
    if legacy:
        return str(legacy)
    entries = cfg.get("LLM_API") or []
    if isinstance(entries, list) and entries:
        first = entries[0]
        if isinstance(first, dict) and first.get("name"):
            return str(first["name"])
    raise ValueError("未配置 LLM 供应商：请在 config.yaml 的 LLM.provider 中指定")


def get_llm(
    cfg: dict,
    *,
    name: str | None = None,
    role: str | None = None,
    provider: str | None = None,
) -> dict:
    """按 provider / name / role 返回一条 LLM 配置。

    推荐 schema::

        # config/config.yaml
        LLM:
          provider: deepseek
          temperature: 0.7
          max_tokens: 4096
          roles: [planner, executor]

        # config/llm/deepseek.yaml
        api_key: ${DEEPSEEK_API_KEY}
        api_base: https://api.deepseek.com
        model: deepseek-v4-pro

    兼容旧 schema ``LLM_API: [{ name, ... }, ...]``。
    """
    providers = _llm_provider_entries(cfg)
    if providers:
        if role and not name and not provider:
            for provider_name, entry in providers.items():
                if role in (entry.get("roles") or []):
                    resolved = dict(entry)
                    resolved.setdefault("name", provider_name)
                    return resolved
            available = ", ".join(sorted(providers))
            raise ValueError(
                f"未找到承担 role={role!r} 的 LLM 配置；已定义供应商: {available}"
            )

        selected = provider or name or get_llm_provider(cfg)
        entry = providers.get(selected)
        if entry is None:
            available = ", ".join(sorted(providers))
            raise ValueError(
                f"未知 LLM 供应商 {selected!r}；可选: {available}（当前 provider={get_llm_provider(cfg)!r}）"
            )
        if role and role not in (entry.get("roles") or []):
            raise ValueError(
                f"LLM 供应商 {selected!r} 未声明 role={role!r}；"
                f"roles={entry.get('roles') or []}"
            )
        resolved = dict(entry)
        resolved.setdefault("name", selected)
        return resolved

    entries = cfg.get("LLM_API") or []
    selected = provider or name
    if not selected:
        if not role:
            try:
                selected = get_llm_provider(cfg)
            except ValueError:
                selected = None
    for entry in entries:
        if selected and entry.get("name") != selected:
            continue
        if role and role not in (entry.get("roles") or []):
            continue
        return entry
    raise ValueError(f"未找到匹配的 LLM 配置 (name={name}, role={role}, provider={provider})")
