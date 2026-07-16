"""Explicit user-global tier and project-to-user non-leakage."""
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import mind as M


class TestUserTier(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mind-user-tier-")
        self.base = Path(self._tmp.name)
        self.user_home = self.base / "user-memory"
        self.previous = os.environ.get("MIND_USER_HOME")
        os.environ["MIND_USER_HOME"] = str(self.user_home)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("MIND_USER_HOME", None)
        else:
            os.environ["MIND_USER_HOME"] = self.previous
        self._tmp.cleanup()

    def _project(self, name):
        root = self.base / name
        root.mkdir()
        mind = M.Mind(root)
        with redirect_stdout(StringIO()):
            mind.init()
        return root, mind

    def test_explicit_user_memory_recalls_across_projects(self):
        _, first = self._project("project-a")
        preference = "preferred formatter is ruff format"
        with redirect_stdout(StringIO()):
            first.remember(
                preference,
                metadata={
                    "scope": "user",
                    "type": "procedural",
                    "authority": "user",
                },
            )
        second_root, second = self._project("project-b")
        output = StringIO()

        with redirect_stdout(output):
            second.recall("what formatter is preferred")

        text = output.getvalue()
        self.assertIn(preference, text)
        self.assertIn("user/", text)
        self.assertIn("id user:", text)
        self.assertNotIn(
            M.Hippocampus._id(preference),
            M.Hippocampus(
                second_root / M.MIND_DIR / M.GRAPH_FILE).nodes,
        )
        self.assertNotIn(
            preference,
            (second_root / "AGENTS.md").read_text("utf-8"),
            "user-global facts must not leak into committed project rules",
        )

    def test_project_capture_never_promotes_to_user_tier(self):
        _, project = self._project("project")
        text = "project runtime is python three fourteen"

        with redirect_stdout(StringIO()):
            project.capture(text)

        user_graph = self.user_home / M.GRAPH_FILE
        self.assertFalse(
            user_graph.exists()
            and M.Hippocampus._id(text)
            in M.Hippocampus(user_graph).nodes)

    def test_user_tier_confirmation_uses_prefixed_id(self):
        _, project = self._project("project")
        text = "preferred terminal theme is high contrast"
        with redirect_stdout(StringIO()):
            project.remember(text, metadata={"scope": "user"})
        node_id = M.Hippocampus._id(text)

        with redirect_stdout(StringIO()):
            project.confirm(["user:" + node_id])

        user = M.Hippocampus(self.user_home / M.GRAPH_FILE)
        self.assertEqual(user.nodes[node_id]["access_count"], 1)

    def test_promotion_suggestions_never_copy_and_exclude_identity(self):
        _, project = self._project("project")
        candidate = "preferred formatter command is ruff format"
        identity = "my name is Example Person"
        with redirect_stdout(StringIO()):
            project.remember(
                candidate,
                metadata={"type": "procedural", "source_trust": "user"},
            )
            project.remember(identity)
            project.confirm([M.Hippocampus._id(candidate)])
            project.confirm([M.Hippocampus._id(identity)])
            result = project.suggest_user(as_json=True)

        texts = [item["text"] for item in result["suggestions"]]
        self.assertIn(candidate, texts)
        self.assertNotIn(identity, texts)
        self.assertEqual(result["copied"], 0)
        self.assertFalse(
            (self.user_home / M.GRAPH_FILE).exists(),
            "suggestions must not create user memory",
        )


if __name__ == "__main__":
    unittest.main()
