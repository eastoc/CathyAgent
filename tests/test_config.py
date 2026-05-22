import os
import tempfile
import unittest
from pathlib import Path

import yaml

from config.config import (
    WORKSPACE_ROOT_PLACEHOLDER,
    _LLM_META_KEYS,
    get_llm,
    get_llm_provider,
    get_workspace_root,
    get_workspace_plugin_config,
    load_config,
    resolve_config_paths,
)


class LlmConfigTest(unittest.TestCase):
    def test_load_llm_from_sub_config_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            llm_dir = root / "llm"
            llm_dir.mkdir()
            (llm_dir / "deepseek.yaml").write_text(
                yaml.safe_dump({"model": "deepseek-chat", "api_key": "ds-key"}),
                encoding="utf-8",
            )
            (llm_dir / "openai.yaml").write_text(
                yaml.safe_dump({"model": "gpt-4o-mini", "api_key": "oa-key"}),
                encoding="utf-8",
            )
            cfg_path = root / "config.yaml"
            cfg_path.write_text(
                yaml.safe_dump(
                    {
                        "LLM": {
                            "provider": "openai",
                            "temperature": 0.5,
                            "max_tokens": 2048,
                            "roles": ["planner"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            cfg = load_config(cfg_path)
            self.assertEqual(get_llm_provider(cfg), "openai")
            llm = get_llm(cfg)
            self.assertEqual(llm["name"], "openai")
            self.assertEqual(llm["model"], "gpt-4o-mini")
            self.assertEqual(llm["temperature"], 0.5)
            self.assertEqual(llm["max_tokens"], 2048)
            self.assertEqual(llm["roles"], ["planner"])

    def test_get_llm_by_provider(self) -> None:
        cfg = {
            "LLM": {
                "provider": "openai",
                "deepseek": {
                    "api_key": "ds-key",
                    "api_base": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                },
                "openai": {
                    "api_key": "oa-key",
                    "api_base": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                },
            }
        }
        llm = get_llm(cfg)
        self.assertEqual(llm["name"], "openai")
        self.assertEqual(llm["model"], "gpt-4o-mini")
        self.assertEqual(get_llm_provider(cfg), "openai")

    def test_get_llm_override_name(self) -> None:
        cfg = {
            "LLM": {
                "provider": "deepseek",
                "deepseek": {"model": "deepseek-chat"},
                "qwen": {"model": "qwen-plus"},
            }
        }
        llm = get_llm(cfg, name="qwen")
        self.assertEqual(llm["name"], "qwen")
        self.assertEqual(llm["model"], "qwen-plus")

    def test_get_llm_by_role(self) -> None:
        cfg = {
            "LLM": {
                "provider": "deepseek",
                "deepseek": {"model": "ds", "roles": ["executor"]},
                "openai": {"model": "gpt", "roles": ["planner"]},
            }
        }
        llm = get_llm(cfg, role="planner")
        self.assertEqual(llm["name"], "openai")

    def test_legacy_llm_api_list(self) -> None:
        cfg = {
            "LLM_API": [
                {"name": "deepseek", "model": "deepseek-chat", "roles": ["planner"]},
            ]
        }
        llm = get_llm(cfg, name="deepseek")
        self.assertEqual(llm["model"], "deepseek-chat")

    def test_load_config_expands_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            llm_dir = root / "llm"
            llm_dir.mkdir()
            (llm_dir / "qwen.yaml").write_text(
                yaml.safe_dump({"api_key": "${QWEN_API_KEY}", "model": "qwen-plus"}),
                encoding="utf-8",
            )
            cfg_path = root / "config.yaml"
            cfg_path.write_text(
                yaml.safe_dump({"LLM": {"provider": "qwen"}}),
                encoding="utf-8",
            )
            os.environ["QWEN_API_KEY"] = "test-qwen-key"
            try:
                cfg = load_config(cfg_path)
            finally:
                os.environ.pop("QWEN_API_KEY", None)
            self.assertEqual(cfg["LLM"]["qwen"]["api_key"], "test-qwen-key")

    def test_project_default_config_has_llm_providers(self) -> None:
        cfg = load_config()
        providers = {
            name
            for name, entry in cfg.get("LLM", {}).items()
            if name not in _LLM_META_KEYS and isinstance(entry, dict)
        }
        self.assertIn("deepseek", providers)
        self.assertIn("openai", providers)
        self.assertIn("qwen", providers)
        llm = get_llm(cfg)
        llm_section = cfg["LLM"]
        self.assertEqual(llm["temperature"], llm_section["temperature"])
        self.assertEqual(llm["max_tokens"], llm_section["max_tokens"])
        self.assertEqual(llm["roles"], llm_section["roles"])


class WorkspaceConfigTest(unittest.TestCase):
    def test_get_workspace_root_resolves_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {"SANDBOX": {"workspace_root": "data/ws"}}
            ws = get_workspace_root(cfg, project_root=root)
            self.assertEqual(ws, (root / "data/ws").resolve())
            self.assertTrue(ws.is_dir())

    def test_resolve_config_paths_substitutes_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {
                "SANDBOX": {"workspace_root": "cad_root"},
                "MCP": {
                    "mcp_servers": {
                        "fs": {
                            "args": [
                                "-y",
                                "@modelcontextprotocol/server-filesystem",
                                WORKSPACE_ROOT_PLACEHOLDER,
                            ]
                        }
                    }
                },
            }
            resolved = resolve_config_paths(cfg, project_root=root)
            expected = str((root / "cad_root").resolve())
            self.assertEqual(resolved["SANDBOX"]["workspace_root"], expected)
            self.assertEqual(resolved["MCP"]["mcp_servers"]["fs"]["args"][-1], expected)

    def test_load_config_expands_workspace_root_in_mcp_fs(self) -> None:
        cfg = load_config()
        ws = get_workspace_root(cfg)
        fs_args = cfg["MCP"]["mcp_servers"]["fs"]["args"]
        self.assertEqual(fs_args[-1], str(ws))
        self.assertNotIn(WORKSPACE_ROOT_PLACEHOLDER, fs_args[-1])

    def test_workspace_plugin_config_shared_by_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {"SANDBOX": {"workspace_root": "shared_ws"}}
            plugin_cfg = get_workspace_plugin_config(cfg, project_root=root)
            expected = str((root / "shared_ws").resolve())
            self.assertEqual(plugin_cfg["workspace_root"], expected)


if __name__ == "__main__":
    unittest.main()
