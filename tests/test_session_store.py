"""SessionStore 持久化测试。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.session.models import Message  # noqa: E402
from cathy.session.store import AsyncSessionStore, SessionStore  # noqa: E402
from cathy.contracts import (  # noqa: E402
    AttachmentRef,
    ImageBlock,
    TextBlock,
    ToolInvocation,
    ToolResult,
    ToolTaskRecord,
)
from cathy.contracts.content import text_content  # noqa: E402


class SessionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "sessions.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_and_load_empty_session(self) -> None:
        with SessionStore(self.db_path) as store:
            s = store.create(metadata={"label": "demo"})
            self.assertTrue(s.id)
            self.assertEqual(s.metadata, {"label": "demo"})
            self.assertEqual(s.messages, [])

        # 重连应仍能拿到该会话
        with SessionStore(self.db_path) as store2:
            loaded = store2.load(s.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.id, s.id)  # type: ignore[union-attr]
            self.assertEqual(loaded.metadata.get("label"), "demo")  # type: ignore[union-attr]

    def test_message_rejects_legacy_string_content(self) -> None:
        with self.assertRaisesRegex(TypeError, "ContentBlock"):
            Message(role="user", content="legacy string")

    def test_get_or_create_with_unknown_id_creates(self) -> None:
        with SessionStore(self.db_path) as store:
            s = store.get_or_create("manual-id-1")
            self.assertEqual(s.id, "manual-id-1")
            self.assertEqual(len(s.messages), 0)

            s2 = store.get_or_create("manual-id-1")
            self.assertEqual(s2.id, s.id)  # 已存在则复用

    def test_message_roundtrip_including_tool_calls(self) -> None:
        with SessionStore(self.db_path) as store:
            s = store.create()
            store.append_message(s.id, Message(role="user", content=text_content("hi")))
            store.append_message(
                s.id,
                Message(
                    role="assistant",
                    content=(),
                    tool_calls=[
                        {
                            "id": "call_1",
                            "name": "get_current_datetime",
                            "arguments": {},
                            "raw_arguments": "{}",
                        }
                    ],
                    reasoning="需要先查询时间",
                ),
            )
            store.append_message(
                s.id,
                Message(
                    role="tool",
                    content=text_content("ISO: 2026-05-07T17:00:00+08:00"),
                    tool_call_id="call_1",
                    name="get_current_datetime",
                ),
            )
            store.append_message(
                s.id,
                Message(role="assistant", content=text_content("今天是 2026-05-07。")),
            )

        # 重连读取，验证字段、顺序、tool_calls 完整 roundtrip
        with SessionStore(self.db_path) as store2:
            loaded = store2.load(s.id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            roles = [m.role for m in loaded.messages]
            self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
            self.assertEqual(loaded.messages[1].tool_calls[0]["id"], "call_1")  # type: ignore[index]
            self.assertEqual(loaded.messages[1].reasoning, "需要先查询时间")
            self.assertEqual(loaded.messages[2].tool_call_id, "call_1")
            self.assertEqual(loaded.messages[2].name, "get_current_datetime")
            self.assertEqual(loaded.messages[3].text, "今天是 2026-05-07。")

    def test_list_sessions_orders_by_recent(self) -> None:
        with SessionStore(self.db_path) as store:
            s1 = store.create("aaa")
            s2 = store.create("bbb")
            store.append_message(
                s1.id,
                Message(role="user", content=text_content("late update")),
            )
            rows = store.list_sessions()
            ids = [r["id"] for r in rows]
            self.assertEqual(ids[0], "aaa")  # s1 最近更新
            self.assertIn("bbb", ids)

    def test_multimodal_message_roundtrip(self) -> None:
        ref = AttachmentRef(
            artifact_id="sha256:" + "a" * 64,
            mime_type="image/png",
            sha256="a" * 64,
            size_bytes=42,
            filename="front.png",
            width=640,
            height=480,
        )
        with SessionStore(self.db_path) as store:
            session = store.create()
            store.append_message(
                session.id,
                Message(
                    role="user",
                    content=(TextBlock("观察"), ImageBlock(ref, detail="high")),
                    metadata={"episode_id": "episode-1", "step": 7},
                ),
            )

        with SessionStore(self.db_path) as store:
            loaded = store.load(session.id)

        assert loaded is not None
        self.assertEqual(loaded.messages[0].text, "观察")
        self.assertEqual(loaded.messages[0].content[1], ImageBlock(ref, detail="high"))
        self.assertEqual(loaded.messages[0].metadata["episode_id"], "episode-1")
        model_content = loaded.messages[0].to_model_dict()["content"]
        self.assertIsInstance(model_content, list)
        self.assertEqual(model_content[1]["type"], "image")

    def test_load_unknown_returns_none(self) -> None:
        with SessionStore(self.db_path) as store:
            self.assertIsNone(store.load("nope"))

    def test_schema_uses_content_json_as_single_source(self) -> None:
        with SessionStore(self.db_path) as store:
            session = store.create("fresh")
            store.append_message(
                session.id,
                Message(
                    role="assistant",
                    content=text_content("唯一内容"),
                    reasoning="保留的推理状态",
                ),
            )
            loaded = store.load(session.id)

        assert loaded is not None
        self.assertEqual(loaded.messages[0].reasoning, "保留的推理状态")
        self.assertEqual(loaded.messages[0].text, "唯一内容")

        conn = sqlite3.connect(str(self.db_path))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        conn.close()
        self.assertIn("content_json", columns)
        self.assertIn("metadata_json", columns)
        self.assertNotIn("content", columns)

    def test_background_tool_task_roundtrip_preserves_provider_ids(self) -> None:
        with SessionStore(self.db_path) as store:
            session = store.create("session-task")
            queued = ToolTaskRecord.queued(
                task_id="task-1",
                run_id="run-1",
                session_id=session.id,
                invocation=ToolInvocation(
                    call_id="call-1",
                    name="analyze_video",
                    arguments={"video_id": "v1"},
                ),
                provider="openai",
                provider_call_id="call-1",
                provider_response_id="resp-1",
                latest_response_id="resp-2",
            )
            store.create_tool_task(queued)
            completed = queued.with_updates(
                status="succeeded",
                result=ToolResult.succeeded(
                    call_id="call-1",
                    tool_name="analyze_video",
                    content="分析完成",
                    metadata={"frames": 10},
                ),
                finished_at=queued.created_at + 1,
            )
            store.update_tool_task(completed)

        with SessionStore(self.db_path) as store:
            loaded = store.load_tool_task("task-1")
            by_run = store.list_tool_tasks(run_id="run-1")

        assert loaded is not None
        self.assertEqual(loaded.provider_call_id, "call-1")
        self.assertEqual(loaded.provider_response_id, "resp-1")
        self.assertEqual(loaded.latest_response_id, "resp-2")
        self.assertEqual(loaded.status, "succeeded")
        assert loaded.result is not None
        self.assertEqual(loaded.result.text, "分析完成")
        self.assertEqual(loaded.result.metadata["frames"], 10)
        self.assertEqual([task.task_id for task in by_run], ["task-1"])


class AsyncSessionStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_store_roundtrip_and_ordered_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "async-sessions.db"
            store = await AsyncSessionStore.open(db_path)
            session = await store.acreate("async-session")

            await store.aappend_message(
                session.id,
                Message(role="user", content=text_content("第一条")),
            )
            await store.aappend_message(
                session.id,
                Message(role="assistant", content=text_content("第二条")),
            )
            loaded = await store.aload(session.id)
            rows = await store.alist_sessions()
            await store.aclose()

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual([message.text for message in loaded.messages], ["第一条", "第二条"])
        self.assertEqual(rows[0]["id"], "async-session")
        self.assertEqual(rows[0]["msg_count"], 2)


if __name__ == "__main__":
    unittest.main()
