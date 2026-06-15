import tempfile
import unittest
from pathlib import Path
from typing import Mapping

from code import (
    AccessMode,
    AgentsMdLoader,
    AGENTS_MD_SEPARATOR,
    ApprovalAborted,
    ApprovalOrchestrator,
    ApprovalRequest,
    ApprovalRequirement,
    ApprovalStore,
    ApplyPatchHandler,
    ConfigError,
    ConfigLayer,
    ConfigLayerStack,
    ConfigRequirements,
    ConfigSource,
    ExecPolicy,
    Event,
    EventReducer,
    FunctionCall,
    HookEngine,
    HookEventName,
    HookRegistration,
    HookResult,
    IdGenerator,
    InstructionEntry,
    JsonType,
    FileSystemRule,
    NetworkProbeHandler,
    PermissionHookDecision,
    PermissionProfile,
    PolicyDecision,
    PrefixRule,
    ReadFileHandler,
    ReviewDecision,
    SandboxDenied,
    SimulatedExecHandler,
    LoadedProjectInstructions,
    ScriptedStreamingModel,
    Thread,
    ToolError,
    ToolRegistry,
    ToolRouter,
    ToolSpec,
    TrustLevel,
    Workspace,
    WorkspaceSandbox,
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


class ConfigLayerStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.requirements = ConfigRequirements(
            allowed_permission_profiles=(":read-only", ":workspace"),
            default_permission_profile=":read-only",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_recursive_merge_and_origins_follow_precedence(self) -> None:
        stack = ConfigLayerStack(
            [
                ConfigLayer(
                    "system",
                    ConfigSource.SYSTEM,
                    {"model": "system", "features": {"shell": False, "memory": True}},
                ),
                ConfigLayer(
                    "user",
                    ConfigSource.USER,
                    {"model": "user", "features": {"shell": True}},
                ),
                ConfigLayer(
                    "session",
                    ConfigSource.SESSION,
                    {"model": "session"},
                ),
            ],
            trust_level=TrustLevel.UNKNOWN,
            requirements=self.requirements,
        )

        effective = stack.effective_config()
        origins = stack.origins()

        self.assertEqual(effective["model"], "session")
        self.assertEqual(effective["features"], {"shell": True, "memory": True})
        self.assertEqual(origins["model"], "session")
        self.assertEqual(origins["features.shell"], "user")
        self.assertEqual(origins["features.memory"], "system")

    def test_layers_must_be_in_increasing_precedence(self) -> None:
        with self.assertRaisesRegex(ConfigError, "increasing precedence"):
            ConfigLayerStack(
                [
                    ConfigLayer("session", ConfigSource.SESSION, {}),
                    ConfigLayer("user", ConfigSource.USER, {}),
                ],
                trust_level=TrustLevel.UNKNOWN,
                requirements=self.requirements,
            )

    def test_stack_snapshots_input_layer_values(self) -> None:
        values = {"model": "before"}
        stack = ConfigLayerStack(
            [ConfigLayer("user", ConfigSource.USER, values)],
            trust_level=TrustLevel.UNKNOWN,
            requirements=self.requirements,
        )

        values["model"] = "after"

        self.assertEqual(stack.effective_config()["model"], "before")

    def test_scalar_overlay_replaces_nested_values_and_origins(self) -> None:
        stack = ConfigLayerStack(
            [
                ConfigLayer(
                    "system",
                    ConfigSource.SYSTEM,
                    {"features": {"shell": True, "memory": True}},
                ),
                ConfigLayer("user", ConfigSource.USER, {"features": False}),
            ],
            trust_level=TrustLevel.UNKNOWN,
            requirements=self.requirements,
        )

        self.assertEqual(stack.effective_config()["features"], False)
        self.assertEqual(stack.origins(), {"features": "user"})

    def test_unknown_and_untrusted_project_layers_are_visible_but_disabled(self) -> None:
        for trust in (TrustLevel.UNKNOWN, TrustLevel.UNTRUSTED):
            stack = ConfigLayerStack(
                [
                    ConfigLayer("user", ConfigSource.USER, {"model": "user"}),
                    ConfigLayer("project", ConfigSource.PROJECT, {"model": "project"}),
                ],
                trust_level=trust,
                requirements=self.requirements,
            )

            self.assertEqual(stack.effective_config()["model"], "user")
            self.assertFalse(stack.layers[1].enabled)
            self.assertIn(trust.value, stack.layers[1].disabled_reason or "")

    def test_trusted_project_still_cannot_set_denylisted_keys(self) -> None:
        stack = ConfigLayerStack(
            [
                ConfigLayer("user", ConfigSource.USER, {"model_provider": "user"}),
                ConfigLayer(
                    "project",
                    ConfigSource.PROJECT,
                    {"model_provider": "project", "approval_policy": "never"},
                ),
            ],
            trust_level=TrustLevel.TRUSTED,
            requirements=self.requirements,
        )

        self.assertEqual(stack.effective_config()["model_provider"], "user")
        self.assertEqual(stack.effective_config()["approval_policy"], "never")
        self.assertIn("model_provider", stack.warnings[0])

    def test_requirements_fall_back_from_disallowed_profile(self) -> None:
        stack = ConfigLayerStack(
            [
                ConfigLayer(
                    "user",
                    ConfigSource.USER,
                    {"default_permissions": ":danger-full-access"},
                )
            ],
            trust_level=TrustLevel.TRUSTED,
            requirements=self.requirements,
        )

        resolved = stack.resolve(self.root)

        self.assertEqual(resolved.active_permission_profile, ":read-only")
        self.assertEqual(resolved.permission_profile.name, "read-only")
        self.assertIn("disallowed by requirements", resolved.warnings[0])

    def test_requirements_preserve_allowed_profile(self) -> None:
        stack = ConfigLayerStack(
            [
                ConfigLayer(
                    "user", ConfigSource.USER, {"default_permissions": ":workspace"}
                )
            ],
            trust_level=TrustLevel.TRUSTED,
            requirements=self.requirements,
        )

        self.assertEqual(stack.resolve(self.root).active_permission_profile, ":workspace")

    def test_trust_default_and_approval_policy_are_independent(self) -> None:
        trusted = ConfigLayerStack(
            [], trust_level=TrustLevel.TRUSTED, requirements=self.requirements
        ).resolve(self.root)
        untrusted = ConfigLayerStack(
            [], trust_level=TrustLevel.UNTRUSTED, requirements=self.requirements
        ).resolve(self.root)
        unknown = ConfigLayerStack(
            [], trust_level=TrustLevel.UNKNOWN, requirements=self.requirements
        ).resolve(self.root)

        self.assertEqual(trusted.active_permission_profile, ":workspace")
        self.assertEqual(untrusted.active_permission_profile, ":workspace")
        self.assertEqual(untrusted.approval_policy, "unless-trusted")
        self.assertEqual(unknown.active_permission_profile, ":read-only")

    def test_config_lock_detects_resolved_drift(self) -> None:
        resolved = ConfigLayerStack(
            [ConfigLayer("user", ConfigSource.USER, {"model": "gpt-a"})],
            trust_level=TrustLevel.UNKNOWN,
            requirements=self.requirements,
        ).resolve(self.root)

        resolved.lock.verify(resolved.values)
        drifted = dict(resolved.values)
        drifted["model"] = "gpt-b"
        with self.assertRaisesRegex(ConfigError, "drifted"):
            resolved.lock.verify(drifted)

    def test_requirements_default_must_be_allowed(self) -> None:
        with self.assertRaisesRegex(ValueError, "default must be in allowed"):
            ConfigRequirements((":workspace",), ":read-only")

    def test_resolved_profile_drives_workspace_sandbox(self) -> None:
        (self.root / "greeting.txt").write_text("hello\n", encoding="utf-8")
        resolved = ConfigLayerStack(
            [
                ConfigLayer(
                    "project",
                    ConfigSource.PROJECT,
                    {"default_permissions": ":danger-full-access"},
                )
            ],
            trust_level=TrustLevel.TRUSTED,
            requirements=self.requirements,
        ).resolve(self.root)
        workspace = Workspace(self.root, resolved.permission_profile)

        with self.assertRaises(SandboxDenied):
            workspace.apply_patch(PATCH)
        self.assertEqual((self.root / "greeting.txt").read_text(), "hello\n")


class ExecPolicyTests(unittest.TestCase):
    def test_strictest_matching_prefix_rule_wins(self) -> None:
        policy = ExecPolicy(
            [
                PrefixRule(("git",), PolicyDecision.PROMPT),
                PrefixRule(
                    ("git", "push"),
                    PolicyDecision.FORBIDDEN,
                    "Use a reviewed release workflow.",
                ),
                PrefixRule(("git", "status"), PolicyDecision.ALLOW),
            ],
            fallback=PolicyDecision.PROMPT,
        )

        evaluation = policy.evaluate(["git", "push", "origin", "main"])

        self.assertEqual(evaluation.decision, PolicyDecision.FORBIDDEN)
        self.assertEqual(len(evaluation.matched_rules), 2)
        self.assertFalse(evaluation.used_fallback)

    def test_unmatched_command_uses_explicit_fallback(self) -> None:
        evaluation = ExecPolicy(fallback=PolicyDecision.PROMPT).evaluate(["python3"])

        self.assertEqual(evaluation.decision, PolicyDecision.PROMPT)
        self.assertEqual(evaluation.matched_rules, ())
        self.assertTrue(evaluation.used_fallback)

    def test_simulated_exec_maps_policy_to_approval_requirement(self) -> None:
        handler = SimulatedExecHandler(
            ExecPolicy(
                [
                    PrefixRule(("echo",), PolicyDecision.ALLOW),
                    PrefixRule(("git",), PolicyDecision.PROMPT),
                    PrefixRule(("rm",), PolicyDecision.FORBIDDEN),
                ]
            )
        )

        self.assertEqual(
            handler.approval_requirement({"command": ["echo", "ok"]}),
            ApprovalRequirement.SKIP,
        )
        self.assertEqual(
            handler.approval_requirement({"command": ["git", "status"]}),
            ApprovalRequirement.NEEDS_APPROVAL,
        )
        self.assertEqual(
            handler.approval_requirement({"command": ["rm", "-rf", "build"]}),
            ApprovalRequirement.FORBIDDEN,
        )


class HookEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_pre_hook_rewrites_before_exec_policy_evaluation(self) -> None:
        exec_handler = SimulatedExecHandler(
            ExecPolicy(
                [
                    PrefixRule(("echo",), PolicyDecision.ALLOW),
                    PrefixRule(("git",), PolicyDecision.FORBIDDEN),
                ]
            )
        )
        hooks = HookEngine(
            [
                HookRegistration(
                    "rewrite-git-to-echo",
                    HookEventName.PRE_TOOL_USE,
                    lambda _context: HookResult(
                        updated_arguments={"command": ["echo", "reviewed"]}
                    ),
                    tool_name="exec_command",
                )
            ]
        )
        prompts: list[ApprovalRequest] = []
        router = ToolRouter(
            ToolRegistry([exec_handler]),
            ApprovalOrchestrator(
                lambda request: prompts.append(request) or ReviewDecision.DENIED,
                hooks=hooks,
            ),
            hooks,
        )

        result = router.dispatch(
            FunctionCall("item-1", "call-1", "exec_command", {"command": ["git", "push"]})
        )

        self.assertTrue(result.success)
        self.assertEqual(exec_handler.executions, [("echo", "reviewed")])
        self.assertEqual(prompts, [])

    def test_pre_hook_block_stops_before_approval_and_handler(self) -> None:
        exec_handler = SimulatedExecHandler(ExecPolicy(fallback=PolicyDecision.PROMPT))
        hooks = HookEngine(
            [
                HookRegistration(
                    "block-exec",
                    HookEventName.PRE_TOOL_USE,
                    lambda _context: HookResult(block_reason="command violates local policy"),
                    tool_name="exec_command",
                )
            ]
        )
        prompts: list[ApprovalRequest] = []
        router = ToolRouter(
            ToolRegistry([exec_handler]),
            ApprovalOrchestrator(
                lambda request: prompts.append(request) or ReviewDecision.APPROVED,
                hooks=hooks,
            ),
            hooks,
        )

        result = router.dispatch(
            FunctionCall("item-1", "call-1", "exec_command", {"command": ["git", "status"]})
        )

        self.assertFalse(result.success)
        self.assertIn("blocked by pre-tool-use hook", result.output)
        self.assertEqual(exec_handler.executions, [])
        self.assertEqual(prompts, [])

    def test_pre_hook_rewrite_is_validated_again(self) -> None:
        exec_handler = SimulatedExecHandler(ExecPolicy(fallback=PolicyDecision.ALLOW))
        hooks = HookEngine(
            [
                HookRegistration(
                    "invalid-rewrite",
                    HookEventName.PRE_TOOL_USE,
                    lambda _context: HookResult(updated_arguments={"command": "echo unsafe"}),
                    tool_name="exec_command",
                )
            ]
        )
        router = ToolRouter(
            ToolRegistry([exec_handler]),
            ApprovalOrchestrator(lambda _request: ReviewDecision.APPROVED, hooks=hooks),
            hooks,
        )

        result = router.dispatch(
            FunctionCall("item-1", "call-1", "exec_command", {"command": ["echo", "safe"]})
        )

        self.assertFalse(result.success)
        self.assertIn("must be list", result.output)
        self.assertEqual(exec_handler.executions, [])

    def test_permission_allow_bypasses_user_prompt(self) -> None:
        handler = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL)
        hooks = HookEngine(
            [
                HookRegistration(
                    "managed-allow",
                    HookEventName.PERMISSION_REQUEST,
                    lambda _context: HookResult(
                        permission_decision=PermissionHookDecision.ALLOW
                    ),
                    tool_name="record",
                )
            ]
        )
        prompts: list[ApprovalRequest] = []
        orchestrator = ApprovalOrchestrator(
            lambda request: prompts.append(request) or ReviewDecision.DENIED,
            hooks=hooks,
        )

        result = orchestrator.run(
            handler, "call-1", {"value": "ok"}, turn_id="turn-1"
        )

        self.assertEqual(result, "ok")
        self.assertEqual(handler.executions, 1)
        self.assertEqual(prompts, [])

    def test_permission_deny_wins_and_skips_user_prompt(self) -> None:
        handler = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL)
        hooks = HookEngine(
            [
                HookRegistration(
                    "allow",
                    HookEventName.PERMISSION_REQUEST,
                    lambda _context: HookResult(
                        permission_decision=PermissionHookDecision.ALLOW
                    ),
                    tool_name="record",
                ),
                HookRegistration(
                    "deny",
                    HookEventName.PERMISSION_REQUEST,
                    lambda _context: HookResult(
                        permission_decision=PermissionHookDecision.DENY,
                        block_reason="managed hook denied action",
                    ),
                    tool_name="record",
                ),
            ]
        )
        prompts: list[ApprovalRequest] = []

        with self.assertRaisesRegex(ToolError, "managed hook denied action"):
            ApprovalOrchestrator(
                lambda request: prompts.append(request) or ReviewDecision.APPROVED,
                hooks=hooks,
            ).run(handler, "call-1", {"value": "no"}, turn_id="turn-1")

        self.assertEqual(handler.executions, 0)
        self.assertEqual(prompts, [])

    def test_permission_no_decision_falls_back_to_user(self) -> None:
        handler = RecordingHandler(ApprovalRequirement.NEEDS_APPROVAL)
        hooks = HookEngine(
            [
                HookRegistration(
                    "audit-only",
                    HookEventName.PERMISSION_REQUEST,
                    lambda _context: HookResult(),
                    tool_name="record",
                )
            ]
        )
        prompts: list[ApprovalRequest] = []

        ApprovalOrchestrator(
            lambda request: prompts.append(request) or ReviewDecision.APPROVED,
            hooks=hooks,
        ).run(handler, "call-1", {"value": "ok"}, turn_id="turn-1")

        self.assertEqual(len(prompts), 1)
        self.assertEqual(handler.executions, 1)

    def test_post_hook_runs_only_after_success_and_can_replace_visible_output(self) -> None:
        contexts = []
        hooks = HookEngine(
            [
                HookRegistration(
                    "summarize",
                    HookEventName.POST_TOOL_USE,
                    lambda context: contexts.append(context)
                    or HookResult(feedback="checked by post hook"),
                    tool_name="record",
                )
            ]
        )
        handler = RecordingHandler(ApprovalRequirement.SKIP)
        router = ToolRouter(
            ToolRegistry([handler]),
            ApprovalOrchestrator(lambda _request: ReviewDecision.APPROVED, hooks=hooks),
            hooks,
        )

        success = router.dispatch(
            FunctionCall("item-1", "call-1", "record", {"value": "raw"})
        )
        failure = router.dispatch(
            FunctionCall("item-2", "call-2", "unknown", {"value": "raw"})
        )

        self.assertEqual(success.output, "checked by post hook")
        self.assertFalse(failure.success)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].output, "raw")

    def test_hook_failure_is_visible_but_fails_open(self) -> None:
        events: list[Event] = []

        def fail(_context):
            raise RuntimeError("hook unavailable")

        hooks = HookEngine(
            [
                HookRegistration(
                    "broken-audit",
                    HookEventName.PRE_TOOL_USE,
                    fail,
                    tool_name="record",
                )
            ],
            event_sink=events.append,
        )
        handler = RecordingHandler(ApprovalRequirement.SKIP)
        router = ToolRouter(
            ToolRegistry([handler]),
            ApprovalOrchestrator(lambda _request: ReviewDecision.APPROVED, hooks=hooks),
            hooks,
        )

        result = router.dispatch(
            FunctionCall("item-1", "call-1", "record", {"value": "ok"}),
            turn_id="turn-1",
        )

        self.assertTrue(result.success)
        self.assertEqual(handler.executions, 1)
        self.assertEqual([event.method for event in events], ["hook/started", "hook/completed"])
        self.assertEqual(events[-1].hook_run.status, "failed")


class AgentsMdLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_discovers_and_loads_from_project_root_to_cwd(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / "AGENTS.md").write_text("root rules", encoding="utf-8")
        nested = self.root / "packages" / "api"
        nested.mkdir(parents=True)
        (nested / "AGENTS.md").write_text("api rules", encoding="utf-8")

        loaded = AgentsMdLoader().load(nested)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.text(), "root rules\n\napi rules")
        self.assertEqual(
            loaded.sources(),
            (self.root / "AGENTS.md", nested / "AGENTS.md"),
        )

    def test_nearest_root_marker_stops_parent_traversal(self) -> None:
        (self.root / "AGENTS.md").write_text("outside", encoding="utf-8")
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "AGENTS.md").write_text("inside", encoding="utf-8")
        nested = repo / "src"
        nested.mkdir()

        loaded = AgentsMdLoader().load(nested)

        assert loaded is not None
        self.assertEqual(loaded.text(), "inside")
        self.assertEqual(loaded.sources(), (repo / "AGENTS.md",))

    def test_no_marker_or_empty_marker_list_only_checks_cwd(self) -> None:
        (self.root / "AGENTS.md").write_text("parent", encoding="utf-8")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "AGENTS.md").write_text("cwd", encoding="utf-8")

        without_found_marker = AgentsMdLoader(project_root_markers=(".missing",))
        traversal_disabled = AgentsMdLoader(project_root_markers=())

        self.assertEqual(without_found_marker.load(nested).text(), "cwd")
        self.assertEqual(traversal_disabled.load(nested).text(), "cwd")

    def test_each_directory_uses_first_regular_candidate(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / "AGENTS.override.md").write_text("override", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("agents", encoding="utf-8")
        (self.root / "WORKFLOW.md").write_text("fallback", encoding="utf-8")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "WORKFLOW.md").write_text("nested fallback", encoding="utf-8")

        loader = AgentsMdLoader(
            fallback_filenames=("", "AGENTS.md", "WORKFLOW.md", "WORKFLOW.md")
        )
        loaded = loader.load(nested)

        assert loaded is not None
        self.assertEqual(
            loader.candidate_filenames(),
            ("AGENTS.override.md", "AGENTS.md", "WORKFLOW.md"),
        )
        self.assertEqual(loaded.text(), "override\n\nnested fallback")

    def test_override_directory_falls_back_to_agents_file(self) -> None:
        (self.root / "AGENTS.override.md").mkdir()
        agents = self.root / "AGENTS.md"
        agents.write_text("primary", encoding="utf-8")

        loaded = AgentsMdLoader().load(self.root)

        assert loaded is not None
        self.assertEqual(loaded.text(), "primary")
        self.assertEqual(loaded.sources(), (agents,))

    def test_total_byte_budget_truncates_later_docs(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / "AGENTS.md").write_text("root", encoding="utf-8")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "AGENTS.md").write_text("abcdef", encoding="utf-8")

        loaded = AgentsMdLoader(max_bytes=7).load(nested)

        assert loaded is not None
        self.assertEqual(loaded.text(), "root\n\nabc")
        self.assertEqual(len(loaded.warnings), 1)
        self.assertIn("3 bytes", loaded.warnings[0])

    def test_whitespace_doc_does_not_consume_budget(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / "AGENTS.md").write_text("   ", encoding="utf-8")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "AGENTS.md").write_text("abc", encoding="utf-8")

        loaded = AgentsMdLoader(max_bytes=3).load(nested)

        assert loaded is not None
        self.assertEqual(loaded.text(), "abc")
        self.assertEqual(loaded.sources(), (nested / "AGENTS.md",))

    def test_invalid_utf8_is_lossy_and_warned(self) -> None:
        path = self.root / "AGENTS.md"
        path.write_bytes(b"project\xffdoc")

        loaded = AgentsMdLoader().load(self.root)

        assert loaded is not None
        self.assertEqual(loaded.text(), "project\ufffddoc")
        self.assertEqual(len(loaded.warnings), 1)
        self.assertIn("invalid UTF-8", loaded.warnings[0])

    def test_truncating_valid_utf8_is_lossy_without_invalid_file_warning(self) -> None:
        path = self.root / "AGENTS.md"
        path.write_text("é", encoding="utf-8")

        loaded = AgentsMdLoader(max_bytes=1).load(self.root)

        assert loaded is not None
        self.assertEqual(loaded.text(), "\ufffd")
        self.assertEqual(len(loaded.warnings), 1)
        self.assertIn("truncating", loaded.warnings[0])
        self.assertNotIn("invalid UTF-8", loaded.warnings[0])

    def test_zero_budget_disables_project_docs_but_keeps_user_instructions(self) -> None:
        project = self.root / "AGENTS.md"
        project.write_text("project", encoding="utf-8")
        user_source = self.root / "user" / "AGENTS.md"

        loaded = AgentsMdLoader(max_bytes=0).load(
            self.root,
            user_instructions="global",
            user_source=user_source,
        )

        assert loaded is not None
        self.assertEqual(loaded.text(), "global")
        self.assertEqual(loaded.sources(), (user_source,))

    def test_user_and_project_instructions_use_boundary_and_render_wrapper(self) -> None:
        project = self.root / "AGENTS.md"
        project.write_text("project", encoding="utf-8")
        user_source = self.root / "user-agents.md"

        loaded = AgentsMdLoader().load(
            self.root,
            user_instructions="global",
            user_source=user_source,
        )

        assert loaded is not None
        self.assertEqual(loaded.text(), f"global{AGENTS_MD_SEPARATOR}project")
        self.assertEqual(loaded.sources(), (user_source, project))
        self.assertEqual(
            loaded.render(),
            f"# AGENTS.md instructions for {self.root}\n\n"
            f"<INSTRUCTIONS>\nglobal{AGENTS_MD_SEPARATOR}project\n</INSTRUCTIONS>",
        )

    def test_loaded_instructions_without_content_render_empty(self) -> None:
        loaded = LoadedProjectInstructions(
            cwd=self.root,
            user_instructions=" \n",
            user_source=self.root / "user-agents.md",
            entries=(InstructionEntry(" \n", self.root / "AGENTS.md"),),
        )

        self.assertEqual(loaded.render(), "")
        self.assertEqual(loaded.sources(), ())


class WorkspaceSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "greeting.txt").write_text("hello\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_file_system_rules_require_absolute_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "require absolute paths"):
            FileSystemRule(Path("relative"), AccessMode.READ)

    def test_read_only_profile_allows_read_but_denies_write(self) -> None:
        workspace = Workspace(self.root, PermissionProfile.read_only(self.root))

        self.assertIn("hello", workspace.read_text("greeting.txt", max_chars=100).content)
        with self.assertRaisesRegex(SandboxDenied, "read-only.*denied write"):
            workspace.apply_patch(PATCH)

        self.assertEqual((self.root / "greeting.txt").read_text(), "hello\n")

    def test_more_specific_deny_rule_wins_inside_writable_root(self) -> None:
        workspace = Workspace(self.root, PermissionProfile.workspace_write(self.root))
        patch = (
            "*** Begin Patch\n"
            "*** Add File: .git/config\n"
            "+unsafe\n"
            "*** End Patch"
        )

        with self.assertRaisesRegex(SandboxDenied, "effective access is deny"):
            workspace.apply_patch(patch)

        self.assertFalse((self.root / ".git" / "config").exists())

    def test_unmatched_path_is_denied_by_default(self) -> None:
        outside = self.root.parent / "outside.txt"
        profile = PermissionProfile(
            "narrow",
            (FileSystemRule(self.root / "allowed", AccessMode.WRITE),),
        )

        with self.assertRaisesRegex(SandboxDenied, "no matching readable rule"):
            WorkspaceSandbox(profile).check_read(outside)

    def test_network_policy_is_independent_from_file_system_access(self) -> None:
        restricted = WorkspaceSandbox(PermissionProfile.workspace_write(self.root))
        enabled = WorkspaceSandbox(PermissionProfile.danger_full_access(self.root))

        with self.assertRaisesRegex(SandboxDenied, "outbound network is restricted"):
            restricted.check_network("https://example.test")
        enabled.check_network("https://example.test")

    def test_network_probe_reports_denial_through_orchestrator(self) -> None:
        workspace = Workspace(self.root, PermissionProfile.workspace_write(self.root))
        events: list[Event] = []
        handler = NetworkProbeHandler(workspace.sandbox)
        orchestrator = ApprovalOrchestrator(
            lambda _request: ReviewDecision.APPROVED,
            event_sink=events.append,
        )

        with self.assertRaises(SandboxDenied):
            orchestrator.run(
                handler,
                "network-1",
                {"url": "https://example.test"},
                turn_id="turn-1",
            )

        self.assertEqual([event.method for event in events], ["sandbox/denied"])

    def test_approval_preview_cannot_read_denied_target(self) -> None:
        secret = self.root / "secret.txt"
        secret.write_text("secret\n", encoding="utf-8")
        profile = PermissionProfile(
            "workspace-with-secret-denied",
            (
                FileSystemRule(self.root, AccessMode.WRITE),
                FileSystemRule(secret, AccessMode.DENY),
            ),
        )
        handler = ApplyPatchHandler(Workspace(self.root, profile))
        events: list[Event] = []
        prompts: list[ApprovalRequest] = []
        patch = (
            "*** Begin Patch\n"
            "*** Update File: secret.txt\n"
            "@@\n"
            "-secret\n"
            "+exposed\n"
            "*** End Patch"
        )

        with self.assertRaisesRegex(SandboxDenied, "denied read"):
            ApprovalOrchestrator(
                lambda request: prompts.append(request) or ReviewDecision.APPROVED,
                event_sink=events.append,
            ).run(handler, "patch-secret", {"patch": patch}, turn_id="turn-1")

        self.assertEqual(prompts, [])
        self.assertEqual([event.method for event in events], ["sandbox/denied"])
        self.assertEqual(secret.read_text(), "secret\n")


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

    def test_approved_patch_is_still_denied_by_read_only_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "greeting.txt").write_text("hello\n", encoding="utf-8")
            ids = IdGenerator()
            reducer = EventReducer()
            events: list[Event] = []

            def consume(event: Event) -> None:
                events.append(event)
                reducer.apply(event)

            router = default_router(
                Workspace(root, PermissionProfile.read_only(root)),
                decider=lambda _request: ReviewDecision.APPROVED,
                event_sink=consume,
            )
            result = Thread(
                ScriptedStreamingModel(ids), router, ids=ids, event_sink=consume
            ).run_turn("update greeting")
            view = reducer.turns[result.id]

            self.assertEqual(view.status, "completed")
            self.assertIn("not executed", view.final_response or "")
            self.assertEqual((root / "greeting.txt").read_text(), "hello\n")
            self.assertEqual(len(view.sandbox_denials), 1)
            methods = [event.method for event in events]
            self.assertLess(
                methods.index("approval/resolved"),
                methods.index("sandbox/denied"),
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
