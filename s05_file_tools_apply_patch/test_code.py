import json
import tempfile
import unittest
from pathlib import Path

from code import (
    Event,
    EventReducer,
    FunctionCall,
    IdGenerator,
    ScriptedStreamingModel,
    Thread,
    ToolError,
    Workspace,
    default_router,
    parse_patch,
)


class PatchParserTests(unittest.TestCase):
    def test_parses_add_update_and_delete(self) -> None:
        plan = parse_patch(
            "*** Begin Patch\n"
            "*** Add File: new.txt\n"
            "+new\n"
            "*** Update File: old.txt\n"
            "@@\n"
            "-before\n"
            "+after\n"
            "*** Delete File: gone.txt\n"
            "*** End Patch"
        )

        self.assertEqual(
            [(change.operation, change.path) for change in plan.changes],
            [("add", "new.txt"), ("update", "old.txt"), ("delete", "gone.txt")],
        )

    def test_rejects_invalid_boundaries_and_empty_patch(self) -> None:
        with self.assertRaisesRegex(ToolError, "must start"):
            parse_patch("*** Add File: x\n+x\n*** End Patch")
        with self.assertRaisesRegex(ToolError, "at least one"):
            parse_patch("*** Begin Patch\n*** End Patch")


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_read_file_returns_structured_bounded_result(self) -> None:
        (self.root / "hello.txt").write_text("hello\n")

        result = self.workspace.read_text("hello.txt", max_chars=10)

        self.assertEqual(result.content, "hello\n")
        self.assertEqual(result.chars, 6)
        with self.assertRaisesRegex(ToolError, "limit of 5"):
            self.workspace.read_text("hello.txt", max_chars=5)

    def test_paths_cannot_escape_workspace(self) -> None:
        with self.assertRaisesRegex(ToolError, "escapes workspace"):
            self.workspace.resolve("../outside.txt")
        with self.assertRaisesRegex(ToolError, "absolute paths"):
            self.workspace.resolve("/tmp/outside.txt")

    def test_apply_patch_commits_multiple_verified_changes(self) -> None:
        (self.root / "old.txt").write_text("before\n")
        (self.root / "gone.txt").write_text("delete me\n")

        result = self.workspace.apply_patch(
            "*** Begin Patch\n"
            "*** Add File: nested/new.txt\n"
            "+created\n"
            "*** Update File: old.txt\n"
            "@@\n"
            "-before\n"
            "+after\n"
            "*** Delete File: gone.txt\n"
            "*** End Patch"
        )

        self.assertEqual((self.root / "nested/new.txt").read_text(), "created\n")
        self.assertEqual((self.root / "old.txt").read_text(), "after\n")
        self.assertFalse((self.root / "gone.txt").exists())
        self.assertEqual(
            [(item["operation"], item["path"]) for item in result.changes],
            [
                ("add", "nested/new.txt"),
                ("update", "old.txt"),
                ("delete", "gone.txt"),
            ],
        )

    def test_missing_context_rejects_patch_without_modifying_file(self) -> None:
        target = self.root / "old.txt"
        target.write_text("before\n")

        with self.assertRaisesRegex(ToolError, "occurs 0 times"):
            self.workspace.apply_patch(
                "*** Begin Patch\n"
                "*** Update File: old.txt\n"
                "@@\n"
                "-missing\n"
                "+after\n"
                "*** End Patch"
            )

        self.assertEqual(target.read_text(), "before\n")

    def test_ambiguous_context_is_rejected(self) -> None:
        target = self.root / "old.txt"
        target.write_text("same\nsame\n")

        with self.assertRaisesRegex(ToolError, "occurs 2 times"):
            self.workspace.apply_patch(
                "*** Begin Patch\n"
                "*** Update File: old.txt\n"
                "@@\n"
                "-same\n"
                "+changed\n"
                "*** End Patch"
            )

    def test_complete_plan_is_verified_before_any_commit(self) -> None:
        with self.assertRaisesRegex(ToolError, "is not a file"):
            self.workspace.apply_patch(
                "*** Begin Patch\n"
                "*** Add File: would-be-created.txt\n"
                "+created\n"
                "*** Update File: missing.txt\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch"
            )

        self.assertFalse((self.root / "would-be-created.txt").exists())

    def test_duplicate_target_is_rejected(self) -> None:
        (self.root / "same.txt").write_text("old\n")
        with self.assertRaisesRegex(ToolError, "more than once"):
            self.workspace.apply_patch(
                "*** Begin Patch\n"
                "*** Update File: same.txt\n"
                "@@\n"
                "-old\n"
                "+middle\n"
                "*** Update File: same.txt\n"
                "@@\n"
                "-middle\n"
                "+new\n"
                "*** End Patch"
            )


class ThreadFileRuntimeTests(unittest.TestCase):
    def test_full_turn_reads_then_applies_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "greeting.txt").write_text("hello\n")
            ids = IdGenerator()
            reducer = EventReducer()
            events: list[Event] = []
            model = ScriptedStreamingModel(ids)

            def consume(event: Event) -> None:
                events.append(event)
                reducer.apply(event)

            result = Thread(
                model,
                default_router(Workspace(root)),
                ids=ids,
                event_sink=consume,
            ).run_turn("update greeting")
            view = reducer.turns[result.id]

            self.assertEqual(model.sample_count, 3)
            self.assertEqual(model.seen_tool_names, ["read_file", "apply_patch"])
            self.assertIn("Patch committed", view.final_response or "")
            self.assertEqual((root / "greeting.txt").read_text(), "hello, structured patch\n")
            self.assertEqual((root / "notes.txt").read_text(), "created by apply_patch\n")
            calls = [
                event.item.name
                for event in events
                if event.method == "item/completed"
                and isinstance(event.item, FunctionCall)
            ]
            self.assertEqual(calls, ["read_file", "apply_patch"])
            outputs = [
                json.loads(item.output)
                for item in view.completed
                if type(item).__name__ == "FunctionCallOutput"
            ]
            self.assertEqual(outputs[0]["path"], "greeting.txt")
            self.assertEqual(len(outputs[1]["changes"]), 2)


if __name__ == "__main__":
    unittest.main()
