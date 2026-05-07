"""plugin.yaml 解析与 manifest 校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .base import PluginError

# 我们对 plugin.yaml 自身做一个粗校验，避免在 Registry 里散写各种 if。
_MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "version", "tools", "execution"],
    "properties": {
        "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
        "version": {"type": "string"},
        "description": {"type": "string"},
        "tools": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "description", "input_schema"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "description": {"type": "string"},
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                },
            },
        },
        "permissions": {"type": "object"},
        "execution": {
            "type": "object",
            "required": ["runtime", "entrypoint"],
            "properties": {
                "runtime": {"type": "string", "enum": ["python"]},
                "entrypoint": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1},
                "sandbox": {"type": "boolean"},
            },
        },
        "metadata": {
            "type": "object",
            "properties": {
                "trust_level": {
                    "type": "string",
                    "enum": ["builtin", "verified", "untrusted"],
                },
            },
        },
    },
}


@dataclass(frozen=True)
class ToolSpec:
    """单个 tool 的元数据。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class Execution:
    runtime: str
    entrypoint: str
    timeout_seconds: int = 30
    sandbox: bool = False


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    description: str
    tools: list[ToolSpec]
    permissions: dict[str, Any]
    execution: Execution
    metadata: dict[str, Any]
    source_dir: Path

    @property
    def trust_level(self) -> str:
        return str(self.metadata.get("trust_level", "untrusted"))


def parse_manifest(manifest_path: Path) -> PluginManifest:
    """读取并校验 plugin.yaml，构造 PluginManifest。"""
    if not manifest_path.exists():
        raise PluginError(f"plugin.yaml 不存在: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # 先校验 manifest 自身格式
    validator = Draft202012Validator(_MANIFEST_SCHEMA)
    errors = sorted(validator.iter_errors(raw), key=lambda e: e.absolute_path)
    if errors:
        msgs = "; ".join(f"{list(e.absolute_path) or '<root>'}: {e.message}" for e in errors)
        raise PluginError(f"manifest 格式错误 ({manifest_path}): {msgs}")

    # 再校验每个 tool 的 input_schema 自身是合法 JSON Schema
    tools_raw = raw["tools"]
    for tool in tools_raw:
        try:
            Draft202012Validator.check_schema(tool["input_schema"])
        except SchemaError as e:
            raise PluginError(
                f"插件 {raw['name']} 的工具 {tool['name']} input_schema 非法: {e.message}"
            ) from e

    tools = [
        ToolSpec(
            name=t["name"],
            description=t["description"],
            input_schema=t["input_schema"],
            output_schema=t.get("output_schema"),
        )
        for t in tools_raw
    ]

    exec_raw = raw["execution"]
    execution = Execution(
        runtime=exec_raw["runtime"],
        entrypoint=exec_raw["entrypoint"],
        timeout_seconds=int(exec_raw.get("timeout_seconds", 30)),
        sandbox=bool(exec_raw.get("sandbox", False)),
    )

    return PluginManifest(
        name=raw["name"],
        version=str(raw["version"]),
        description=str(raw.get("description", "")),
        tools=tools,
        permissions=dict(raw.get("permissions") or {}),
        execution=execution,
        metadata=dict(raw.get("metadata") or {}),
        source_dir=manifest_path.parent,
    )


@dataclass
class LoadedPlugin:
    """注册表内部使用：manifest + 实例化的 ToolPlugin。"""

    manifest: PluginManifest
    instance: Any  # 真实类型为 ToolPlugin，但避免循环导入用 Any
    tool_index: dict[str, ToolSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_index:
            self.tool_index = {t.name: t for t in self.manifest.tools}
