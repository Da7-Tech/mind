"""Golden protocol tests for the same-file MCP stdio server."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mind as M


class TestMCPServer(unittest.TestCase):
    def test_lifecycle_requires_initialized_notification(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = M.MCPServer(tmp)
            initialized = server.handle({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": M.MCP_PROTOCOL_VERSION},
            })
            early = server.handle({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            })
            notification = server.handle({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            })
            listed = server.handle({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/list",
            })

        self.assertEqual(
            initialized["result"]["protocolVersion"],
            M.MCP_PROTOCOL_VERSION,
        )
        self.assertEqual(early["error"]["code"], -32002)
        self.assertIsNone(notification)
        names = {
            tool["name"] for tool in listed["result"]["tools"]}
        self.assertTrue({
            "remember", "recall", "confirm", "correct", "link",
            "why", "entity", "dream", "status", "context",
            "suggest_user", "doctor", "growth", "forget", "unlink",
            "redact", "purge",
        }.issubset(names))

    def test_cancellation_notification_and_eof_exit_cleanly(self):
        server = M.MCPServer()
        server.initialized = True

        class ForbiddenFallback:
            def __iter__(self):
                raise AssertionError("explicit empty stdin was ignored")

        self.assertIsNone(server.handle({
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 42, "reason": "client closed"},
        }))
        with tempfile.SpooledTemporaryFile(mode="w+") as output, \
                mock.patch.object(M.sys, "stdin", ForbiddenFallback()):
            self.assertEqual(
                server.run_stdio(stdin=[], stdout=output), 0)
            output.seek(0)
            self.assertEqual(output.read(), "")

    def test_context_and_growth_tools_return_json_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = M.MCPServer(tmp)
            server.initialized = True
            remembered = server.handle({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "remember",
                    "arguments": {
                        "text": "mcp context database is postgres",
                        "automatic": False,
                    },
                },
            })
            context = server.handle({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "context", "arguments": {}},
            })
            growth = server.handle({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "growth",
                    "arguments": {"days": 30},
                },
            })

        self.assertFalse(remembered["result"]["isError"])
        context_text = context["result"]["content"][0]["text"]
        growth_text = growth["result"]["content"][0]["text"]
        self.assertEqual(
            json.loads(context_text)["format"], 1)
        self.assertEqual(
            json.loads(growth_text)["days"], 30)

    def test_remember_defaults_to_explicit_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = M.MCPServer(tmp)
            server.initialized = True
            response = server.handle({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "remember",
                    "arguments": {
                        "text": "working on a temporary benchmark today",
                    },
                },
            })
            graph = M.Hippocampus(
                Path(tmp) / M.MIND_DIR / M.GRAPH_FILE)

        self.assertFalse(response["result"]["isError"])
        self.assertIn(
            M.Hippocampus._id(
                "working on a temporary benchmark today"),
            graph.nodes,
        )
        schema = {
            tool["name"]: tool for tool in M.MCPServer.tools()
        }["remember"]["inputSchema"]
        self.assertFalse(
            schema["properties"]["automatic"]["default"])

    def test_stdio_transcript_contains_only_json_rpc_messages(self):
        script = Path(M.__file__).resolve()
        transcript = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": M.MCP_PROTOCOL_VERSION},
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
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "remember",
                    "arguments": {"text": "mcp stores a durable fact"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "recall",
                    "arguments": {"query": "what does mcp store"},
                },
            },
        ]
        payload = "".join(
            json.dumps(message) + "\n" for message in transcript)
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(script), "mcp"],
                cwd=tmp,
                input=payload,
                capture_output=True,
                text=True,
                timeout=20,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [
            json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses],
                         [1, 2, 3, 4])
        self.assertIn("tools", responses[1]["result"])
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertIn(
            "mcp stores a durable fact",
            responses[3]["result"]["content"][0]["text"],
        )

    def test_parse_and_unknown_method_errors_are_json_rpc_errors(self):
        server = M.MCPServer()
        with tempfile.SpooledTemporaryFile(mode="w+") as parse_output:
            server.run_stdio(
                stdin=["{broken\n"],
                stdout=parse_output,
            )
            parse_output.seek(0)
            parsed = json.loads(parse_output.read())
        unknown = server.handle({
            "jsonrpc": "2.0",
            "id": 9,
            "method": "unknown/method",
        })

        self.assertEqual(parsed["error"]["code"], -32700)
        self.assertEqual(unknown["error"]["code"], -32002)


if __name__ == "__main__":
    unittest.main()
