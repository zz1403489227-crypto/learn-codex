import tempfile
import unittest
from pathlib import Path
from typing import Mapping

from code import (
    ApprovalAborted,
    ApprovalOrchestrator,
    ApprovalRequest,
    ApprovalRequirement,
    ApprovalStore,
    ApplyPatchHandler,
    Event,
    EventReducer,
    FunctionCall,
    IdGenerator,
    JsonType,
    ReadFileHandler,
    ReviewDecision,
    ScriptedStreamingModel,
    Thread,
    ToolError,
    ToolSpec,
    Workspace,
    default_router,
)


PATCH = (
    "*** Begin Patch\n"
    "*** Update File: greeting.txt\n"
    "@@\n"
    "-hello\n"
    "+hello, approved\n"
    "*** End Patch"
)


class RecordingHandler:
    spec = ToolSpec("record", "record an action", {"value": str}, ("value",))

    def __init__(
        self,
        requirement: ApprovalRequirement,
        keys: tuple[str, ...] = ("record",),
    ) -> None:
        self.requirement = requirement
        self.keys = keys
        self.executions = 0

    def approval_requirement(
        self, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequirement:
        return self.requirement

    def approval_request(
        self, call_id: str, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequest:
        return ApprovalRequest(call_id, "record", "record value", self.keys)

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        self.executions += 1
        return str(arguments["value"])


class ApprovalOrchestratorTests(unittest.TestCase):
    def test_skip_executes_without_prompt(self) -> None:
        prompts: list[ApprovalRequest] = []
        handler = RecordingHandler(ApprovalRequirement.SKIP)
        orchestrator = ApprovalOrchestrator(
            lambda request: prompts.append(request) or ReviewDecision.DENIED
        )

        result = orchestrator.run(handler, "call-1", {"value": "ok"}, turn_id="turn-1")

        self.assertEqual(result, "ok")
        self.assertEqual(handler.executions, 1)
        self.assertEqual(prompts, [])

    def test_approval_request_precedes_execution(self) -> None:
        timeline: list[str] = []
        handler = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL)

        def decide(_request: ApprovalRequest) -> ReviewDecision:
            timeline.append("decision")
            self.assertEqual(handler.executions, 0)
            return ReviewDecision.APPROVED

        orchestrator = ApprovalOrchestrator(
            decide,
            event_sink=lambda event: timeline.append(event.method),
        )
        orchestrator.run(handler, "call-1", {"value": "ok"}, turn_id="turn-1")
        timeline.append("executed")

        self.assertEqual(
            timeline,
            ["approval/requested", "decision", "approval/resolved", "executed"],
        )
        self.assertEqual(handler.executions, 1)

    def test_denied_rejects_without_execution(self) -> None:
        handler = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL)
        orchestrator = ApprovalOrchestrator(lambda _request: ReviewDecision.DENIED)

        with self.assertRaisesRegex(ToolError, "rejected by user"):
            orchestrator.run(handler, "call-1", {"value": "no"}, turn_id="turn-1")

        self.assertEqual(handler.executions, 0)

    def test_abort_is_distinct_from_denial(self) -> None:
        handler = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL)
        orchestrator = ApprovalOrchestrator(lambda _request: ReviewDecision.ABORT)

        with self.assertRaises(ApprovalAborted):
            orchestrator.run(handler, "call-1", {"value": "stop"}, turn_id="turn-1")

        self.assertEqual(handler.executions, 0)

    def test_forbidden_rejects_without_prompt(self) -> None:
        prompts = 0
        handler = RecordingHandler(ApprovalRequirement.FORBIDDEN)

        def decide(_request: ApprovalRequest) -> ReviewDecision:
            nonlocal prompts
            prompts += 1
            return ReviewDecision.APPROVED

        with self.assertRaisesRegex(ToolError, "forbidden by policy"):
            ApprovalOrchestrator(decide).run(
                handler, "call-1", {"value": "blocked"}, turn_id="turn-1"
            )

        self.assertEqual(prompts, 0)
        self.assertEqual(handler.executions, 0)

    def test_approved_for_session_caches_exact_keys(self) -> None:
        prompts = 0
        store = ApprovalStore()

        def decide(_request: ApprovalRequest) -> ReviewDecision:
            nonlocal prompts
            prompts += 1
            return ReviewDecision.APPROVED_FOR_SESSION

        orchestrator = ApprovalOrchestrator(decide, store=store)
        first = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL, ("a", "b"))
        subset = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL, ("a",))
        new_key = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL, ("c",))

        orchestrator.run(first, "call-1", {"value": "one"}, turn_id="turn-1")
        orchestrator.run(subset, "call-2", {"value": "two"}, turn_id="turn-2")
        orchestrator.run(new_key, "call-3", {"value": "three"}, turn_id="turn-3")

        self.assertEqual(prompts, 2)
        self.assertEqual((first.executions, subset.executions, new_key.executions), (1, 1, 1))


class ApprovalFileToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        (self.root / "greeting.txt").write_text("hello\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_read_file_skips_approval(self) -> None:
        handler = ReadFileHandler(self.workspace)
        prompts: list[ApprovalRequest] = []
        output = ApprovalOrchestrator(
            lambda request: prompts.append(request) or ReviewDecision.DENIED
        ).run(handler, "read-1", {"path": "greeting.txt"}, turn_id="turn-1")

        self.assertIn("hello", output)
        self.assertEqual(prompts, [])

    def test_patch_request_lists_target_before_mutation(self) -> None:
        handler = ApplyPatchHandler(self.workspace)
        seen: list[ApprovalRequest] = []

        def deny(request: ApprovalRequest) -> ReviewDecision:
            seen.append(request)
            self.assertEqual((self.root / "greeting.txt").read_text(), "hello\n")
            return ReviewDecision.DENIED

        with self.assertRaises(ToolError):
            ApprovalOrchestrator(deny).run(
                handler, "patch-1", {"patch": PATCH}, turn_id="turn-1"
            )

        self.assertEqual(seen[0].keys, ("apply_patch:greeting.txt",))
        self.assertIn("greeting.txt", seen[0].summary)
        self.assertEqual((self.root / "greeting.txt").read_text(), "hello\n")

    def test_invalid_patch_fails_validation_without_prompt(self) -> None:
        handler = ApplyPatchHandler(self.workspace)
        prompts: list[ApprovalRequest] = []
        invalid = PATCH.replace("-hello", "-missing")

        with self.assertRaisesRegex(ToolError, "occurs 0 times"):
            ApprovalOrchestrator(
                lambda request: prompts.append(request) or ReviewDecision.APPROVED
            ).run(handler, "patch-1", {"patch": invalid}, turn_id="turn-1")

        self.assertEqual(prompts, [])


class ThreadApprovalTests(unittest.TestCase):
    def test_full_turn_pauses_for_patch_approval_then_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "greeting.txt").write_text("hello\n", encoding="utf-8")
            ids = IdGenerator()
            reducer = EventReducer()
            events: list[Event] = []
            prompts: list[ApprovalRequest] = []

            def consume(event: Event) -> None:
                events.append(event)
                reducer.apply(event)

            def approve(request: ApprovalRequest) -> ReviewDecision:
                prompts.append(request)
                return ReviewDecision.APPROVED_FOR_SESSION

            router = default_router(
                Workspace(root), decider=approve, event_sink=consume
            )
            result = Thread(
                ScriptedStreamingModel(ids), router, ids=ids, event_sink=consume
            ).run_turn("update greeting")
            view = reducer.turns[result.id]

            self.assertEqual(view.status, "completed")
            self.assertEqual(len(prompts), 1)
            self.assertEqual(view.pending_approvals, {})
            self.assertEqual(
                view.approval_decisions,
                [(prompts[0].approval_id, ReviewDecision.APPROVED_FOR_SESSION)],
            )
            self.assertEqual(
                (root / "greeting.txt").read_text(encoding="utf-8"),
                "hello, structured patch\n",
            )
            methods = [event.method for event in events]
            self.assertLess(
                methods.index("approval/requested"),
                methods.index("approval/resolved"),
            )

    def test_abort_stops_turn_without_modifying_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "greeting.txt").write_text("hello\n", encoding="utf-8")
            ids = IdGenerator()
            reducer = EventReducer()

            def consume(event: Event) -> None:
                reducer.apply(event)

            router = default_router(
                Workspace(root),
                decider=lambda _request: ReviewDecision.ABORT,
                event_sink=consume,
            )
            result = Thread(
                ScriptedStreamingModel(ids), router, ids=ids, event_sink=consume
            ).run_turn("update greeting")

            self.assertEqual(reducer.turns[result.id].status, "aborted")
            self.assertEqual(result.final_response, "")
            self.assertEqual((root / "greeting.txt").read_text(), "hello\n")

    def test_denial_returns_tool_error_and_turn_can_continue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "greeting.txt").write_text("hello\n", encoding="utf-8")
            ids = IdGenerator()
            reducer = EventReducer()

            def consume(event: Event) -> None:
                reducer.apply(event)

            router = default_router(
                Workspace(root),
                decider=lambda _request: ReviewDecision.DENIED,
                event_sink=consume,
            )
            result = Thread(
                ScriptedStreamingModel(ids), router, ids=ids, event_sink=consume
            ).run_turn("update greeting")

            view = reducer.turns[result.id]
            self.assertEqual(view.status, "completed")
            self.assertIn("not executed", view.final_response or "")
            self.assertEqual((root / "greeting.txt").read_text(), "hello\n")


if __name__ == "__main__":
    unittest.main()
