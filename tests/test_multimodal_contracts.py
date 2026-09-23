"""多模态内容契约与本地附件存储测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.artifacts import ArtifactStore, LocalArtifactStore  # noqa: E402
from cathy.contracts import AgentRequest, ImageBlock, JsonBlock, TextBlock  # noqa: E402
from cathy.contracts.content import (  # noqa: E402
    deserialize_content_blocks,
    serialize_content_blocks,
)


class MultimodalContractsTest(unittest.TestCase):
    def test_content_blocks_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LocalArtifactStore(td)
            ref = store.put_bytes(
                b"fake-png",
                mime_type="image/png",
                filename="front.png",
                width=640,
                height=480,
            )
            blocks = (
                TextBlock("抓取红色方块"),
                ImageBlock(
                    ref,
                    detail="high",
                    source="front_camera",
                    captured_at_ns=123,
                    frame_id="frame-7",
                ),
                JsonBlock({"joint_positions": [0.1, 0.2]}, label="robot_state"),
            )

            restored = deserialize_content_blocks(serialize_content_blocks(blocks))

        self.assertEqual(restored, blocks)

    def test_local_store_is_content_addressed_and_resolves_data_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LocalArtifactStore(td)
            first = store.put_bytes(b"same", mime_type="image/png")
            second = store.put_bytes(b"same", mime_type="image/png")

            self.assertIsInstance(store, ArtifactStore)
            self.assertEqual(first.artifact_id, second.artifact_id)
            self.assertEqual(store.read_bytes(first), b"same")
            self.assertTrue(store.data_url(first).startswith("data:image/png;base64,"))

    def test_agent_request_rewrite_preserves_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LocalArtifactStore(td)
            ref = store.put_bytes(b"image", mime_type="image/jpeg")
            request = AgentRequest(
                content=(TextBlock("原指令"), ImageBlock(ref)),
                metadata={"task_id": "pick-1"},
            )

            rewritten = request.with_text("新指令")

        self.assertEqual(rewritten.text, "新指令")
        self.assertEqual(rewritten.attachment_ids, (ref.artifact_id,))
        self.assertEqual(rewritten.metadata["task_id"], "pick-1")


if __name__ == "__main__":
    unittest.main()
