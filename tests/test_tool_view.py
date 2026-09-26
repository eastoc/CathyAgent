"""ToolView 白/黑名单视图测试。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.contracts import ToolInvocation, ToolTaskRecord  # noqa: E402
from cathy.plugins import TaskRegistry, ToolScheduler  # noqa: E402
from cathy.plugins.base import ToolPlugin  # noqa: E402
from cathy.plugins.manifest import Execution, PluginManifest, ToolSpec  # noqa: E402
from cathy.plugins.registry import PluginRegistry, ToolView  # noqa: E402
from cathy.session.store import AsyncSessionStore, SessionStore  # noqa: E402


class _EchoPlugin(ToolPlugin):
    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        return f"{tool_name}:{params.get('msg', '')}"


class _ControlledAsyncPlugin(ToolPlugin):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        raise AssertionError("调度器不应调用同步 execute")

    async def aexecute(self, tool_name: str, params: dict[str, Any]) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(float(params.get("delay", 0.02)))
            return f"{tool_name}:ok"
        finally:
            self.active -= 1


def _make_manifest(plugin_name: str, tool_names: list[str]) -> PluginManifest:
    return PluginManifest(
        name=plugin_name,
        version="0.0.1",
        description="test",
        tools=[
            ToolSpec(
                name=t,
                description=f"echo {t}",
                input_schema={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                    "additionalProperties": False,
                },
            )
            for t in tool_names
        ],
        permissions={},
        execution=Execution(runtime="python", entrypoint="<internal>:_EchoPlugin"),
        metadata={"trust_level": "builtin"},
        source_dir=Path("."),
    )


class ToolViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = PluginRegistry(plugins_dirs=[])
        self.reg.register_internal_plugin(
            _make_manifest("p_a", ["alpha", "beta"]),
            _EchoPlugin(),
        )
        self.reg.register_internal_plugin(
            _make_manifest("p_b", ["gamma"]),
            _EchoPlugin(),
        )

    def test_register_internal_plugin_dedupes_tool_name(self) -> None:
        with self.assertRaises(Exception):
            self.reg.register_internal_plugin(
                _make_manifest("p_dup", ["alpha"]), _EchoPlugin()
            )

    def test_view_allowed_only(self) -> None:
        view = ToolView(self.reg, allowed=["alpha", "gamma"])
        names = sorted(tool.name for tool in view.model_tools())
        self.assertEqual(names, ["alpha", "gamma"])
        self.assertEqual(view.call("alpha", {"msg": "hi"}), "alpha:hi")
        self.assertIn("不在", view.call("beta", {"msg": "x"}))

    def test_view_blocked_overrides_allowed(self) -> None:
        view = ToolView(self.reg, allowed=["alpha", "beta"], blocked=["beta"])
        names = sorted(tool.name for tool in view.model_tools())
        self.assertEqual(names, ["alpha"])
        self.assertIn("不在", view.call("beta", {"msg": "x"}))

    def test_view_default_inherits_all(self) -> None:
        view = ToolView(self.reg)
        names = sorted(tool.name for tool in view.model_tools())
        self.assertEqual(names, ["alpha", "beta", "gamma"])

    def test_view_invalid_args_propagate(self) -> None:
        view = ToolView(self.reg)
        out = view.call("alpha", {})  # 缺 msg → schema 校验失败
        self.assertTrue(out.startswith("[ToolError:alpha]"))

    def test_view_hides_blocked_descriptor(self) -> None:
        view = ToolView(self.reg, blocked=["beta"])
        self.assertIsNone(view.get_tool_descriptor("beta"))
        self.assertIsNotNone(view.get_tool_descriptor("alpha"))


def _scheduler_registry(
    plugin: _ControlledAsyncPlugin,
    specs: list[ToolSpec],
) -> PluginRegistry:
    registry = PluginRegistry(plugins_dirs=[])
    registry.register_internal_plugin(
        PluginManifest(
            name="controlled",
            version="0.0.1",
            description="controlled async tools",
            tools=specs,
            permissions={},
            execution=Execution(
                runtime="python",
                entrypoint="<internal>:Controlled",
            ),
            metadata={"trust_level": "builtin"},
            source_dir=Path(__file__).parent,
        ),
        plugin,
    )
    return registry


def _scheduler_spec(name: str, **kwargs: Any) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        input_schema={
            "type": "object",
            "properties": {"delay": {"type": "number"}},
            "additionalProperties": False,
        },
        **kwargs,
    )


class ToolSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_background_tool_does_not_silently_run_inline(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(
                plugin,
                [_scheduler_spec("later", execution_mode="background")],
            )
        )

        result = await scheduler.execute(
            ToolInvocation("c-background", "later", {})
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "background_not_available")
        self.assertEqual(plugin.max_active, 0)

    async def test_independent_tools_run_concurrently(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(
                plugin,
                [_scheduler_spec("first"), _scheduler_spec("second")],
            )
        )

        results = await scheduler.execute_many(
            [
                ToolInvocation("c1", "first", {"delay": 0.03}),
                ToolInvocation("c2", "second", {"delay": 0.03}),
            ]
        )

        self.assertEqual(plugin.max_active, 2)
        self.assertEqual([result.status for result in results], ["succeeded"] * 2)
        self.assertEqual([result.call_id for result in results], ["c1", "c2"])

    async def test_shared_concurrency_key_serializes_tools(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(
                plugin,
                [
                    _scheduler_spec("move", concurrency_key="robot:arm-01"),
                    _scheduler_spec("grip", concurrency_key="robot:arm-01"),
                ],
            )
        )

        await scheduler.execute_many(
            [
                ToolInvocation("c1", "move", {"delay": 0.02}),
                ToolInvocation("c2", "grip", {"delay": 0.02}),
            ]
        )

        self.assertEqual(plugin.max_active, 1)

    async def test_timeout_returns_typed_result(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(
                plugin,
                [_scheduler_spec("slow", timeout_seconds=0.01)],
            )
        )

        result = await scheduler.execute(
            ToolInvocation("c-timeout", "slow", {"delay": 0.1})
        )

        self.assertEqual(result.status, "timed_out")
        self.assertEqual(result.error_code, "timeout")
        self.assertIn("TIMEOUT", result.text)
        self.assertEqual(plugin.active, 0)

    async def test_invalid_arguments_do_not_enter_plugin(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(plugin, [_scheduler_spec("validate")])
        )

        result = await scheduler.execute(
            ToolInvocation("c-invalid", "validate", {"unknown": True})
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "invalid_arguments")
        self.assertEqual(plugin.max_active, 0)


class TaskRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_background_task_uses_async_store(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(
                plugin,
                [_scheduler_spec("async_store", execution_mode="background")],
            )
        )
        with tempfile.TemporaryDirectory() as td:
            store = await AsyncSessionStore.open(Path(td) / "sessions.db")
            session = await store.acreate("session-async-store")
            registry = TaskRegistry(scheduler=scheduler, store=store)

            queued = await registry.submit(
                ToolInvocation("call-async", "async_store", {"delay": 0.01}),
                run_id="run-async",
                session_id=session.id,
                provider_response_id="resp-async",
                latest_response_id="resp-async",
            )
            completed = await registry.wait(queued.task_id)
            turn_state, tool_message = await registry.abuild_model_continuation(
                queued.task_id
            )
            await registry.shutdown()
            await store.aclose()

        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(turn_state.value, "resp-async")
        self.assertEqual(tool_message["tool_call_id"], "call-async")

    async def test_background_task_persists_success_and_provider_call_id(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(
                plugin,
                [_scheduler_spec("analyze", execution_mode="background")],
            )
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "sessions.db"
            with SessionStore(db_path) as store:
                session = store.create("session-bg")
                registry = TaskRegistry(scheduler=scheduler, store=store)
                queued = await registry.submit(
                    ToolInvocation("call-bg", "analyze", {"delay": 0.01}),
                    run_id="run-bg",
                    session_id=session.id,
                    provider="openai",
                    provider_call_id="call-bg",
                    provider_response_id="resp-1",
                )
                completed = await registry.wait(queued.task_id)
                turn_state, tool_message = registry.build_model_continuation(
                    queued.task_id
                )

            with SessionStore(db_path) as reopened:
                persisted = reopened.load_tool_task(queued.task_id)

        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(completed.provider_call_id, "call-bg")
        self.assertEqual(completed.latest_response_id, "resp-1")
        self.assertEqual(turn_state.value, "resp-1")
        self.assertEqual(tool_message["tool_call_id"], "call-bg")
        self.assertEqual(tool_message["role"], "tool")
        assert persisted is not None
        self.assertEqual(persisted.status, "succeeded")
        assert persisted.result is not None
        self.assertEqual(persisted.result.text, "analyze:ok")

    async def test_cancel_background_task_persists_terminal_state(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(
                plugin,
                [_scheduler_spec("slow_bg", execution_mode="background")],
            )
        )
        with tempfile.TemporaryDirectory() as td:
            with SessionStore(Path(td) / "sessions.db") as store:
                session = store.create("session-cancel")
                registry = TaskRegistry(scheduler=scheduler, store=store)
                queued = await registry.submit(
                    ToolInvocation("call-cancel", "slow_bg", {"delay": 1}),
                    run_id="run-cancel",
                    session_id=session.id,
                )
                await asyncio.sleep(0)
                cancelled = await registry.cancel(queued.task_id)

        self.assertEqual(cancelled.status, "cancelled")
        assert cancelled.result is not None
        self.assertEqual(cancelled.result.error_code, "cancelled")
        self.assertEqual(plugin.active, 0)

    async def test_interrupted_task_is_marked_orphaned_without_replay(self) -> None:
        plugin = _ControlledAsyncPlugin()
        scheduler = ToolScheduler(
            _scheduler_registry(
                plugin,
                [_scheduler_spec("recover", execution_mode="background")],
            )
        )
        with tempfile.TemporaryDirectory() as td:
            with SessionStore(Path(td) / "sessions.db") as store:
                session = store.create("session-orphan")
                record = ToolTaskRecord.queued(
                    task_id="task-orphan",
                    run_id="run-orphan",
                    session_id=session.id,
                    invocation=ToolInvocation("call-orphan", "recover", {}),
                )
                store.create_tool_task(record)
                registry = TaskRegistry(scheduler=scheduler, store=store)
                orphaned = registry.mark_interrupted_tasks_orphaned()

        self.assertEqual([task.status for task in orphaned], ["orphaned"])
        self.assertEqual(plugin.max_active, 0)


if __name__ == "__main__":
    unittest.main()
