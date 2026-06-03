"""Integration test simulating an MCP client to answer validation checklist."""

import json
import unittest
from unittest.mock import patch

from writing_context_rtfm.server import process_message


class TestMCPClientValidation(unittest.TestCase):
    def test_checklist(self):
        # 1. Can the MCP client list get_writing_context_pack?
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        res_list = json.loads(process_message(json.dumps(req_list)))
        self.assertEqual(res_list["id"], 1)
        tools = res_list["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("get_writing_context_pack", tool_names)

        # 2. Can it call the tool with a valid task?
        with (
            patch("writing_context_rtfm.server.RTFMAdapter") as MockAdapter,
            patch("writing_context_rtfm.server.ExtensionStore") as MockStore,
        ):
            mock_adapter = MockAdapter.return_value
            mock_adapter.search.return_value = []
            mock_store = MockStore.return_value
            mock_store.get_cached_pack.return_value = None

            req_call_valid = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "get_writing_context_pack",
                    "arguments": {"task": "Write intro"},
                },
            }
            res_valid = json.loads(process_message(json.dumps(req_call_valid)))
            self.assertNotIn("isError", res_valid.get("result", {}))
            self.assertIn("content", res_valid["result"])

            # 3. Does the response match the context-pack schema?
            # 6. Does it avoid reading or returning full files?
            payload = json.loads(res_valid["result"]["content"][0]["text"])
            self.assertEqual(payload["task"], "Write intro")
            self.assertIn("source_spans", payload)
            # The schema ensures only 'source_spans' are returned, not full file contents natively.

        # 4. Does it fail clearly on invalid input?
        req_call_invalid = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_writing_context_pack",
                "arguments": {
                    # Missing task
                    "target": "section_1"
                },
            },
        }
        res_invalid = json.loads(process_message(json.dumps(req_call_invalid)))
        self.assertTrue(res_invalid["result"].get("isError"))
        self.assertIn(
            "Missing required argument: task", res_invalid["result"]["content"][0]["text"]
        )

        # 5. Does refresh_index work?
        with (
            patch("writing_context_rtfm.server.RTFMAdapter") as MockAdapter,
            patch("writing_context_rtfm.server.ExtensionStore") as MockStore,
        ):
            mock_adapter = MockAdapter.return_value
            mock_adapter.sync.return_value = True

            req_refresh = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "refresh_index", "arguments": {"project_root": "."}},
            }
            res_refresh = json.loads(process_message(json.dumps(req_refresh)))
            self.assertNotIn("isError", res_refresh.get("result", {}))

            refresh_payload = json.loads(res_refresh["result"]["content"][0]["text"])
            self.assertEqual(refresh_payload["status"], "ok")

    def test_initialize_root_uri(self):
        from pathlib import Path

        import writing_context_rtfm.server as server

        orig_root = server.WORKSPACE_ROOT
        orig_cache = server._RUNTIME_CACHE
        try:
            req_init = {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "initialize",
                "params": {"rootUri": "file:///path/to/some/project"},
            }
            res_init = json.loads(process_message(json.dumps(req_init)))
            self.assertEqual(res_init["id"], 10)
            self.assertEqual(server.WORKSPACE_ROOT, Path("/path/to/some/project").resolve())
            self.assertIsNone(server._RUNTIME_CACHE)

            req_escaped = {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "initialize",
                "params": {"rootUri": "file:///path/to/my%20cool%20project"},
            }
            process_message(json.dumps(req_escaped))
            self.assertEqual(server.WORKSPACE_ROOT, Path("/path/to/my cool project").resolve())
        finally:
            server.WORKSPACE_ROOT = orig_root
            server._RUNTIME_CACHE = orig_cache


if __name__ == "__main__":
    unittest.main()
