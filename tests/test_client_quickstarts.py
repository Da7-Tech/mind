"""Verify all documented client launch definitions with one golden transcript."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import mind as M

ROOT = Path(__file__).resolve().parent.parent


class ClientQuickstartTests(unittest.TestCase):
    def test_three_client_definitions_complete_golden_transcript(self):
        manifest = json.loads(
            (ROOT / "docs" / "clients.json").read_text("utf-8"))
        self.assertEqual(
            set(manifest["clients"]),
            {"codex", "claude-code", "gemini-cli"},
        )
        self.assertEqual(
            manifest["protocol_version"], M.MCP_PROTOCOL_VERSION)
        transcript = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": M.MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "quickstart-test",
                        "version": "1",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            },
        ]
        payload = "".join(
            json.dumps(message) + "\n" for message in transcript)
        for name, client in manifest["clients"].items():
            with self.subTest(client=name), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copy2(ROOT / "mind.py", root / "mind.py")
                result = subprocess.run(
                    [sys.executable] + client["args"],
                    cwd=root,
                    input=payload,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(
                    result.returncode, 0,
                    result.stdout + result.stderr)
                responses = [
                    json.loads(line)
                    for line in result.stdout.splitlines()
                ]
                self.assertEqual([item["id"] for item in responses], [1, 2])
                self.assertEqual(
                    responses[0]["result"]["protocolVersion"],
                    M.MCP_PROTOCOL_VERSION,
                )
                self.assertEqual(
                    len(responses[1]["result"]["tools"]), 17)


if __name__ == "__main__":
    unittest.main()
