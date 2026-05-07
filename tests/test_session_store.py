"""SessionStore 持久化测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.session.models import Message  # noqa: E402
from cathy.session.store import SessionStore  # noqa: E402


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
            store.append_message(s.id, Message(role="user", content="hi"))
            store.append_message(
                s.id,
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_current_datetime", "arguments": "{}"},
                        }
                    ],
                ),
            )
            store.append_message(
                s.id,
                Message(
                    role="tool",
                    content="ISO: 2026-05-07T17:00:00+08:00",
                    tool_call_id="call_1",
                    name="get_current_datetime",
                ),
            )
            store.append_message(s.id, Message(role="assistant", content="今天是 2026-05-07。"))

        # 重连读取，验证字段、顺序、tool_calls 完整 roundtrip
        with SessionStore(self.db_path) as store2:
            loaded = store2.load(s.id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            roles = [m.role for m in loaded.messages]
            self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
            self.assertEqual(loaded.messages[1].tool_calls[0]["id"], "call_1")  # type: ignore[index]
            self.assertEqual(loaded.messages[2].tool_call_id, "call_1")
            self.assertEqual(loaded.messages[2].name, "get_current_datetime")
            self.assertEqual(loaded.messages[3].content, "今天是 2026-05-07。")

    def test_list_sessions_orders_by_recent(self) -> None:
        with SessionStore(self.db_path) as store:
            s1 = store.create("aaa")
            s2 = store.create("bbb")
            store.append_message(s1.id, Message(role="user", content="late update"))
            rows = store.list_sessions()
            ids = [r["id"] for r in rows]
            self.assertEqual(ids[0], "aaa")  # s1 最近更新
            self.assertIn("bbb", ids)

    def test_load_unknown_returns_none(self) -> None:
        with SessionStore(self.db_path) as store:
            self.assertIsNone(store.load("nope"))


if __name__ == "__main__":
    unittest.main()
