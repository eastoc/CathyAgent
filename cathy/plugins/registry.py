"""PluginRegistry：插件发现 / 加载 / 调度。

对外暴露的接口与 Phase 0 的 ToolRegistry 鸭子兼容：
    - openai_schemas() -> list[dict]
    - call(name, args) -> str
所以 Agent 主循环（agent.py）无需任何改动即可切换。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .base import PluginError, ToolPlugin
from .manifest import LoadedPlugin, PluginManifest, ToolSpec, parse_manifest


@dataclass(frozen=True)
class ToolDescriptor:
    """暴露给 UI / 审计层的 tool 描述。"""

    name: str
    description: str
    plugin: str
    trust_level: str


class PluginRegistry:
    def __init__(
        self,
        plugins_dirs: Iterable[Path],
        plugin_configs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._plugins_dirs = [Path(p) for p in plugins_dirs]
        self._plugin_configs = plugin_configs or {}
        self._loaded: dict[str, LoadedPlugin] = {}
        self._tool_index: dict[str, str] = {}  # tool_name -> plugin_name

    def discover_and_load(self) -> list[str]:
        """扫描 plugins_dirs 下的子目录，逐个加载并初始化。返回加载成功的插件名列表。"""
        loaded_names: list[str] = []
        for root in self._plugins_dirs:
            if not root.exists() or not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                manifest_path = child / "plugin.yaml"
                if not manifest_path.exists():
                    continue
                try:
                    self._load_plugin_dir(manifest_path)
                    loaded_names.append(child.name)
                except PluginError as exc:
                    print(f"[plugin][skip] {child.name}: {exc}")
        return loaded_names

    def _load_plugin_dir(self, manifest_path: Path) -> None:
        manifest = parse_manifest(manifest_path)

        if manifest.name in self._loaded:
            raise PluginError(f"插件名重复: {manifest.name}")
        for tool in manifest.tools:
            if tool.name in self._tool_index:
                raise PluginError(
                    f"工具名重复: {tool.name}（已由 {self._tool_index[tool.name]} 提供）"
                )

        instance = _instantiate_plugin(manifest)

        cfg = self._plugin_configs.get(manifest.name, {})
        try:
            instance.initialize(cfg)
        except Exception as exc:
            raise PluginError(f"{manifest.name}.initialize 失败: {exc}") from exc

        loaded = LoadedPlugin(manifest=manifest, instance=instance)
        self._loaded[manifest.name] = loaded
        for tool in manifest.tools:
            self._tool_index[tool.name] = manifest.name

    # -------- 主循环消费的接口（鸭子兼容旧 ToolRegistry）-------- #

    def openai_schemas(self) -> list[dict]:
        schemas: list[dict] = []
        for plugin_name, loaded in self._loaded.items():
            for tool in loaded.manifest.tools:
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        },
                    }
                )
        return schemas

    def call(self, tool_name: str, params: dict[str, Any]) -> str:
        plugin_name = self._tool_index.get(tool_name)
        if plugin_name is None:
            return f"[ToolError] 未知工具: {tool_name}"

        loaded = self._loaded[plugin_name]
        tool_spec = loaded.tool_index[tool_name]

        # 入参 schema 校验：失败时不进入插件，结构化错误回传给模型
        try:
            Draft202012Validator(tool_spec.input_schema).validate(params)
        except ValidationError as exc:
            path = ".".join(str(x) for x in exc.absolute_path) or "<root>"
            return f"[ToolError:{tool_name}] 参数不合法 @ {path}: {exc.message}"

        try:
            return loaded.instance.execute(tool_name, params)
        except PluginError as exc:
            return f"[ToolError:{tool_name}] {exc}"
        except TypeError as exc:
            return f"[ToolError:{tool_name}] 参数不合法: {exc}"
        except Exception as exc:
            return f"[ToolError:{tool_name}] {type(exc).__name__}: {exc}"

    # -------- 观测 / 调试接口 -------- #

    def list_tools(self) -> list[ToolDescriptor]:
        out: list[ToolDescriptor] = []
        for plugin_name, loaded in self._loaded.items():
            for tool in loaded.manifest.tools:
                out.append(
                    ToolDescriptor(
                        name=tool.name,
                        description=tool.description,
                        plugin=plugin_name,
                        trust_level=loaded.manifest.trust_level,
                    )
                )
        return out

    def get_manifest(self, plugin_name: str) -> PluginManifest | None:
        loaded = self._loaded.get(plugin_name)
        return loaded.manifest if loaded else None

    def shutdown(self) -> None:
        for loaded in self._loaded.values():
            try:
                loaded.instance.shutdown()
            except Exception as exc:
                print(f"[plugin][shutdown] {loaded.manifest.name}: {exc}")

    # -------- 运行时插件注入（不走磁盘扫描） -------- #

    def register_internal_plugin(
        self,
        manifest: PluginManifest,
        instance: ToolPlugin,
        *,
        config: dict[str, Any] | None = None,
        skip_initialize: bool = False,
    ) -> None:
        """注册一个内存里构造好的插件。

        用于需要持有运行时资源（LLM、Registry 自身引用等）的插件，例如 task 与 skills。
        - 默认会调用 instance.initialize(config or {})；若构造前已自行初始化可设 skip_initialize=True。
        """
        if manifest.name in self._loaded:
            raise PluginError(f"插件名重复: {manifest.name}")
        for tool in manifest.tools:
            if tool.name in self._tool_index:
                raise PluginError(
                    f"工具名重复: {tool.name}（已由 {self._tool_index[tool.name]} 提供）"
                )

        if not skip_initialize:
            try:
                instance.initialize(config or {})
            except Exception as exc:
                raise PluginError(f"{manifest.name}.initialize 失败: {exc}") from exc

        loaded = LoadedPlugin(manifest=manifest, instance=instance)
        self._loaded[manifest.name] = loaded
        for tool in manifest.tools:
            self._tool_index[tool.name] = manifest.name


class ToolView:
    """PluginRegistry 的子集视图（鸭子兼容）。

    用于 subagent / Skill：限制可见 / 可调用工具，不影响父 registry。
    """

    def __init__(
        self,
        registry: "PluginRegistry",
        *,
        allowed: Iterable[str] | None = None,
        blocked: Iterable[str] | None = None,
    ) -> None:
        self._registry = registry
        self._allowed: set[str] | None = set(allowed) if allowed is not None else None
        self._blocked: set[str] = set(blocked or [])

    def _is_visible(self, name: str) -> bool:
        if name in self._blocked:
            return False
        if self._allowed is not None and name not in self._allowed:
            return False
        return True

    def openai_schemas(self) -> list[dict]:
        return [s for s in self._registry.openai_schemas() if self._is_visible(s["function"]["name"])]

    def call(self, tool_name: str, params: dict[str, Any]) -> str:
        if not self._is_visible(tool_name):
            return f"[ToolError:{tool_name}] 此工具不在 subagent 白名单内"
        return self._registry.call(tool_name, params)

    def list_tools(self) -> list[ToolDescriptor]:
        return [d for d in self._registry.list_tools() if self._is_visible(d.name)]


def _instantiate_plugin(manifest: PluginManifest) -> ToolPlugin:
    """按 execution.entrypoint = 'main:ClassName' 加载并实例化。"""
    if manifest.execution.runtime != "python":
        raise PluginError(
            f"暂不支持的 runtime: {manifest.execution.runtime}（仅 python）"
        )

    entry = manifest.execution.entrypoint
    if ":" not in entry:
        raise PluginError(f"entrypoint 格式应为 'module:Class'，得到 {entry!r}")
    module_name, class_name = entry.split(":", 1)

    module_file = manifest.source_dir / f"{module_name}.py"
    if not module_file.exists():
        raise PluginError(f"找不到入口文件: {module_file}")

    spec = importlib.util.spec_from_file_location(
        f"_cathy_plugins.{manifest.name}.{module_name}",
        module_file,
    )
    if spec is None or spec.loader is None:
        raise PluginError(f"无法构建模块 spec: {module_file}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cls = getattr(module, class_name, None)
    if cls is None:
        raise PluginError(f"模块 {module_file} 中找不到类 {class_name}")
    if not isinstance(cls, type) or not issubclass(cls, ToolPlugin):
        raise PluginError(f"{class_name} 必须是 ToolPlugin 的子类")

    return cls()
