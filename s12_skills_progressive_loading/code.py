"""s12: load Skills progressively instead of injecting every instruction.

The inherited runtime remains offline. This chapter adds a small context
assembler that keeps developer and user fragments separate, then layers a
Skills catalog on top: the model sees metadata first and receives full
SKILL.md contents only after an explicit or implicit invocation.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence, TypeAlias


# Model-side items: close to what a model stream produces.


@dataclass(frozen=True)
class ResponseMessage:
    id: str
    role: str
    text: str


@dataclass(frozen=True)
class ResponseFunctionCall:
    id: str
    call_id: str
    name: str
    arguments: dict[str, Any]


ResponseItem: TypeAlias = ResponseMessage | ResponseFunctionCall


@dataclass(frozen=True)
class OutputItemAdded:
    item: ResponseItem


@dataclass(frozen=True)
class OutputTextDelta:
    item_id: str
    delta: str


@dataclass(frozen=True)
class OutputItemDone:
    item: ResponseItem


@dataclass(frozen=True)
class ResponseCompleted:
    pass


ModelEvent: TypeAlias = (
    OutputItemAdded | OutputTextDelta | OutputItemDone | ResponseCompleted
)


# Client-side items: stable, display-oriented facts exposed by the runtime.


@dataclass(frozen=True)
class UserMessage:
    id: str
    text: str


@dataclass(frozen=True)
class AgentMessage:
    id: str
    text: str


@dataclass(frozen=True)
class FunctionCall:
    id: str
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class FunctionCallOutput:
    id: str
    call_id: str
    output: str


TurnItem: TypeAlias = UserMessage | AgentMessage | FunctionCall | FunctionCallOutput


def to_client_item(item: ResponseItem) -> TurnItem | None:
    """Map selected model-side items to the teaching client's visible items."""
    if isinstance(item, ResponseMessage):
        if item.role == "assistant":
            return AgentMessage(id=item.id, text=item.text)
        return None
    return FunctionCall(
        id=item.id,
        call_id=item.call_id,
        name=item.name,
        arguments=item.arguments,
    )


@dataclass(frozen=True)
class Event:
    method: str
    turn_id: str
    item: TurnItem | None = None
    item_id: str | None = None
    delta: str | None = None
    final_response: str | None = None
    approval_request: ApprovalRequest | None = None
    approval_decision: ReviewDecision | None = None
    sandbox_denial: SandboxDenial | None = None
    hook_run: HookRun | None = None


@dataclass
class TurnView:
    status: str = "not_started"
    in_progress: dict[str, TurnItem] = field(default_factory=dict)
    completed: list[TurnItem] = field(default_factory=list)
    text_buffers: dict[str, str] = field(default_factory=dict)
    final_response: str | None = None
    pending_approvals: dict[str, ApprovalRequest] = field(default_factory=dict)
    approval_decisions: list[tuple[str, ReviewDecision]] = field(default_factory=list)
    sandbox_denials: list[SandboxDenial] = field(default_factory=list)
    hook_runs: list[HookRun] = field(default_factory=list)


class EventReducer:
    """Project an interleaved event stream into client-visible Turn state."""

    def __init__(self) -> None:
        self.turns: dict[str, TurnView] = {}

    def apply(self, event: Event) -> TurnView:
        turn = self.turns.setdefault(event.turn_id, TurnView())

        if event.method == "turn/started":
            turn.status = "in_progress"
        elif event.method == "item/started":
            item = _require_item(event)
            turn.in_progress[item.id] = item
            if isinstance(item, AgentMessage):
                turn.text_buffers[item.id] = item.text
        elif event.method == "item/agentMessage/delta":
            item_id = _require_item_id(event)
            if item_id not in turn.in_progress:
                raise ValueError(f"delta references unknown in-progress item {item_id!r}")
            if not isinstance(turn.in_progress[item_id], AgentMessage):
                raise ValueError(f"delta references non-agent item {item_id!r}")
            turn.text_buffers[item_id] = turn.text_buffers.get(item_id, "") + (
                event.delta or ""
            )
        elif event.method == "item/completed":
            item = _require_item(event)
            turn.in_progress.pop(item.id, None)
            turn.completed.append(item)
            if isinstance(item, AgentMessage):
                # The completed item is authoritative; deltas are a live projection.
                turn.text_buffers[item.id] = item.text
        elif event.method == "turn/completed":
            turn.status = "completed"
            turn.final_response = event.final_response
        elif event.method == "turn/aborted":
            turn.status = "aborted"
        elif event.method == "approval/requested":
            request = _require_approval_request(event)
            turn.pending_approvals[request.approval_id] = request
        elif event.method == "approval/resolved":
            request = _require_approval_request(event)
            decision = _require_approval_decision(event)
            turn.pending_approvals.pop(request.approval_id, None)
            turn.approval_decisions.append((request.approval_id, decision))
        elif event.method == "sandbox/denied":
            if event.sandbox_denial is None:
                raise ValueError("sandbox/denied requires sandbox_denial")
            turn.sandbox_denials.append(event.sandbox_denial)
        elif event.method in ("hook/started", "hook/completed"):
            if event.hook_run is None:
                raise ValueError(f"{event.method} requires hook_run")
            turn.hook_runs.append(event.hook_run)
        else:
            raise ValueError(f"unknown event method {event.method!r}")

        return turn


def _require_item(event: Event) -> TurnItem:
    if event.item is None:
        raise ValueError(f"{event.method} requires item")
    return event.item


def _require_item_id(event: Event) -> str:
    if event.item_id is None:
        raise ValueError(f"{event.method} requires item_id")
    return event.item_id


def _require_approval_request(event: Event) -> ApprovalRequest:
    if event.approval_request is None:
        raise ValueError(f"{event.method} requires approval_request")
    return event.approval_request


def _require_approval_decision(event: Event) -> ReviewDecision:
    if event.approval_decision is None:
        raise ValueError(f"{event.method} requires approval_decision")
    return event.approval_decision


class StreamingModel(Protocol):
    def stream(
        self, history: Sequence[TurnItem], tools: Sequence[ToolSpec]
    ) -> Iterator[ModelEvent]:
        """Yield one model sampling response as structured stream events."""


@dataclass
class IdGenerator:
    _counter: count = field(default_factory=lambda: count(1))

    def new(self, prefix: str) -> str:
        return f"{prefix}_{next(self._counter)}"


class ScriptedStreamingModel:
    """Deterministic model that reads, requests a patch, then answers."""

    def __init__(self, ids: IdGenerator) -> None:
        self.ids = ids
        self.sample_count = 0
        self.seen_tool_names: list[str] = []

    def stream(
        self, history: Sequence[TurnItem], tools: Sequence[ToolSpec]
    ) -> Iterator[ModelEvent]:
        self.sample_count += 1
        self.seen_tool_names = [tool.name for tool in tools]
        latest = history[-1]

        if isinstance(latest, UserMessage):
            if "read_file" not in self.seen_tool_names:
                raise RuntimeError("model cannot discover read_file")
            call = ResponseFunctionCall(
                id=self.ids.new("item"),
                call_id=self.ids.new("call"),
                name="read_file",
                arguments={"path": "greeting.txt"},
            )
            yield OutputItemAdded(call)
            yield OutputItemDone(call)
            yield ResponseCompleted()
            return

        if isinstance(latest, FunctionCallOutput):
            if latest.output.startswith("Error:"):
                text = f"Action was not executed. {latest.output}"
                message_id = self.ids.new("item")
                yield OutputItemAdded(
                    ResponseMessage(id=message_id, role="assistant", text="")
                )
                yield OutputTextDelta(item_id=message_id, delta=text)
                yield OutputItemDone(
                    ResponseMessage(id=message_id, role="assistant", text=text)
                )
                yield ResponseCompleted()
                return

            result = json.loads(latest.output)
            if "content" in result:
                call = ResponseFunctionCall(
                    id=self.ids.new("item"),
                    call_id=self.ids.new("call"),
                    name="apply_patch",
                    arguments={"patch": (
                        "*** Begin Patch\n"
                        "*** Update File: greeting.txt\n"
                        "@@\n"
                        "-hello\n"
                        "+hello, structured patch\n"
                        "*** Add File: notes.txt\n"
                        "+created by apply_patch\n"
                        "*** End Patch"
                    )},
                )
                yield OutputItemAdded(call)
                yield OutputItemDone(call)
                yield ResponseCompleted()
                return

            changed = ", ".join(change["path"] for change in result["changes"])
            text = f"Patch committed. Changed files: {changed}."
            message_id = self.ids.new("item")
            yield OutputItemAdded(
                ResponseMessage(id=message_id, role="assistant", text="")
            )
            for delta in (
                "Patch committed. ",
                "Changed files: ",
                f"{changed}.",
            ):
                yield OutputTextDelta(item_id=message_id, delta=delta)
            yield OutputItemDone(
                ResponseMessage(id=message_id, role="assistant", text=text)
            )
            yield ResponseCompleted()
            return

        raise RuntimeError(f"model cannot continue from {type(latest).__name__}")


EventSink: TypeAlias = Callable[[Event], None]


@dataclass(frozen=True)
class TurnResult:
    id: str
    final_response: str
    items: tuple[TurnItem, ...]


class Thread:
    def __init__(
        self,
        model: StreamingModel,
        router: ToolRouter,
        *,
        event_sink: EventSink | None = None,
        ids: IdGenerator | None = None,
        max_sampling_rounds: int = 8,
    ) -> None:
        self.model = model
        self.router = router
        self.event_sink = event_sink or (lambda _event: None)
        self.ids = ids or IdGenerator()
        self.max_sampling_rounds = max_sampling_rounds
        self.history: list[TurnItem] = []

    def _emit(self, event: Event) -> None:
        self.event_sink(event)

    def _complete_item(
        self, turn_id: str, item: TurnItem, turn_items: list[TurnItem]
    ) -> None:
        self.history.append(item)
        turn_items.append(item)
        self._emit(Event(method="item/completed", turn_id=turn_id, item=item))

    def _record_runtime_item(
        self, turn_id: str, item: TurnItem, turn_items: list[TurnItem]
    ) -> None:
        self._emit(Event(method="item/started", turn_id=turn_id, item=item))
        self._complete_item(turn_id, item, turn_items)

    def run_turn(self, user_text: str) -> TurnResult:
        turn_id = self.ids.new("turn")
        turn_items: list[TurnItem] = []
        self._emit(Event(method="turn/started", turn_id=turn_id))

        user_message = UserMessage(id=self.ids.new("item"), text=user_text)
        self._record_runtime_item(turn_id, user_message, turn_items)

        for _ in range(self.max_sampling_rounds):
            needs_follow_up = False
            last_agent_message: str | None = None
            active_items: dict[str, TurnItem] = {}
            saw_response_completed = False

            for model_event in self.model.stream(
                tuple(self.history), self.router.model_visible_specs()
            ):
                if isinstance(model_event, OutputItemAdded):
                    item = to_client_item(model_event.item)
                    if item is not None:
                        active_items[item.id] = item
                        self._emit(Event(method="item/started", turn_id=turn_id, item=item))
                elif isinstance(model_event, OutputTextDelta):
                    if model_event.item_id not in active_items:
                        raise RuntimeError("model delta has no active client-visible item")
                    self._emit(
                        Event(
                            method="item/agentMessage/delta",
                            turn_id=turn_id,
                            item_id=model_event.item_id,
                            delta=model_event.delta,
                        )
                    )
                elif isinstance(model_event, OutputItemDone):
                    item = to_client_item(model_event.item)
                    if item is None:
                        continue
                    if active_items.pop(item.id, None) is None:
                        raise RuntimeError("completed model item was not started")
                    self._complete_item(turn_id, item, turn_items)

                    if isinstance(item, FunctionCall):
                        try:
                            result = self.router.dispatch(item, turn_id=turn_id)
                        except ApprovalAborted:
                            self._emit(Event(method="turn/aborted", turn_id=turn_id))
                            return TurnResult(
                                id=turn_id,
                                final_response="",
                                items=tuple(turn_items),
                            )
                        output = FunctionCallOutput(
                            id=self.ids.new("item"),
                            call_id=item.call_id,
                            output=result.output,
                        )
                        self._record_runtime_item(turn_id, output, turn_items)
                        needs_follow_up = True
                    elif isinstance(item, AgentMessage):
                        last_agent_message = item.text
                elif isinstance(model_event, ResponseCompleted):
                    saw_response_completed = True

            if not saw_response_completed:
                raise RuntimeError("model stream ended before ResponseCompleted")
            if active_items:
                raise RuntimeError("model stream ended with active items")
            if needs_follow_up:
                continue
            if last_agent_message is None:
                raise RuntimeError("turn stopped without a final agent message")

            self._emit(
                Event(
                    method="turn/completed",
                    turn_id=turn_id,
                    final_response=last_agent_message,
                )
            )
            return TurnResult(
                id=turn_id,
                final_response=last_agent_message,
                items=tuple(turn_items),
            )

        raise RuntimeError("turn exceeded max_sampling_rounds")


JsonType: TypeAlias = str | int | float | bool | None | list[Any] | dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    """Small model-visible contract for one function tool."""

    name: str
    description: str
    properties: Mapping[str, type]
    required: tuple[str, ...] = ()
    additional_properties: bool = False


@dataclass(frozen=True)
class ToolResult:
    output: str
    success: bool


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    PROMPT = "prompt"
    FORBIDDEN = "forbidden"


POLICY_SEVERITY = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.PROMPT: 1,
    PolicyDecision.FORBIDDEN: 2,
}


@dataclass(frozen=True)
class PrefixRule:
    prefix: tuple[str, ...]
    decision: PolicyDecision
    justification: str | None = None

    def __post_init__(self) -> None:
        if not self.prefix:
            raise ValueError("policy prefix cannot be empty")

    def matches(self, command: Sequence[str]) -> bool:
        return tuple(command[: len(self.prefix)]) == self.prefix


@dataclass(frozen=True)
class PolicyEvaluation:
    decision: PolicyDecision
    matched_rules: tuple[PrefixRule, ...]
    used_fallback: bool


class ExecPolicy:
    """Evaluate command prefixes; the strictest matching decision wins."""

    def __init__(
        self,
        rules: Sequence[PrefixRule] = (),
        *,
        fallback: PolicyDecision = PolicyDecision.PROMPT,
    ) -> None:
        self.rules = tuple(rules)
        self.fallback = fallback

    def evaluate(self, command: Sequence[str]) -> PolicyEvaluation:
        if not command:
            raise ToolError("exec command cannot be empty")
        matched = tuple(rule for rule in self.rules if rule.matches(command))
        if not matched:
            return PolicyEvaluation(self.fallback, (), used_fallback=True)
        decision = max(matched, key=lambda rule: POLICY_SEVERITY[rule.decision]).decision
        return PolicyEvaluation(decision, matched, used_fallback=False)


class HookEventName(str, Enum):
    PRE_TOOL_USE = "pre_tool_use"
    PERMISSION_REQUEST = "permission_request"
    POST_TOOL_USE = "post_tool_use"


class PermissionHookDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class HookContext:
    event_name: HookEventName
    turn_id: str
    call_id: str
    tool_name: str
    arguments: Mapping[str, JsonType]
    output: str | None = None


@dataclass(frozen=True)
class HookResult:
    updated_arguments: Mapping[str, JsonType] | None = None
    block_reason: str | None = None
    permission_decision: PermissionHookDecision | None = None
    feedback: str | None = None


HookCallback: TypeAlias = Callable[[HookContext], HookResult | None]


@dataclass(frozen=True)
class HookRegistration:
    name: str
    event_name: HookEventName
    callback: HookCallback
    tool_name: str | None = None

    def matches(self, event_name: HookEventName, tool_name: str) -> bool:
        return self.event_name is event_name and (
            self.tool_name is None or self.tool_name == tool_name
        )


@dataclass(frozen=True)
class HookRun:
    hook_name: str
    event_name: HookEventName
    status: str
    message: str | None = None


@dataclass(frozen=True)
class PreToolUseOutcome:
    arguments: Mapping[str, JsonType]
    block_reason: str | None = None


class HookEngine:
    """Run matching lifecycle extensions without embedding them in handlers."""

    def __init__(
        self,
        hooks: Sequence[HookRegistration] = (),
        *,
        event_sink: EventSink | None = None,
    ) -> None:
        self.hooks = tuple(hooks)
        self.event_sink = event_sink or (lambda _event: None)

    def run_pre_tool_use(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonType],
    ) -> PreToolUseOutcome:
        current = copy.deepcopy(arguments)
        for hook in self._matching(HookEventName.PRE_TOOL_USE, tool_name):
            result = self._run(hook, turn_id, call_id, tool_name, current)
            if result is None:
                continue
            if result.block_reason:
                return PreToolUseOutcome(current, result.block_reason)
            if result.updated_arguments is not None:
                current = copy.deepcopy(result.updated_arguments)
        return PreToolUseOutcome(current)

    def run_permission_request(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonType],
    ) -> tuple[PermissionHookDecision, str | None] | None:
        allowed = False
        for hook in self._matching(HookEventName.PERMISSION_REQUEST, tool_name):
            result = self._run(hook, turn_id, call_id, tool_name, arguments)
            if result is None or result.permission_decision is None:
                continue
            if result.permission_decision is PermissionHookDecision.DENY:
                return PermissionHookDecision.DENY, result.block_reason
            allowed = True
        if allowed:
            return PermissionHookDecision.ALLOW, None
        return None

    def run_post_tool_use(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonType],
        output: str,
    ) -> str:
        feedback: list[str] = []
        for hook in self._matching(HookEventName.POST_TOOL_USE, tool_name):
            result = self._run(hook, turn_id, call_id, tool_name, arguments, output)
            if result is not None and result.feedback:
                feedback.append(result.feedback)
        return "\n".join(feedback) if feedback else output

    def _matching(
        self, event_name: HookEventName, tool_name: str
    ) -> tuple[HookRegistration, ...]:
        return tuple(
            hook for hook in self.hooks if hook.matches(event_name, tool_name)
        )

    def _run(
        self,
        hook: HookRegistration,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonType],
        output: str | None = None,
    ) -> HookResult | None:
        self._emit(turn_id, HookRun(hook.name, hook.event_name, "running"))
        try:
            result = hook.callback(
                HookContext(
                    hook.event_name,
                    turn_id,
                    call_id,
                    tool_name,
                    copy.deepcopy(arguments),
                    output,
                )
            )
        except Exception as error:
            self._emit(
                turn_id,
                HookRun(hook.name, hook.event_name, "failed", str(error)),
            )
            return None
        status = "blocked" if result is not None and result.block_reason else "completed"
        self._emit(turn_id, HookRun(hook.name, hook.event_name, status))
        return result

    def _emit(self, turn_id: str, run: HookRun) -> None:
        method = "hook/started" if run.status == "running" else "hook/completed"
        self.event_sink(Event(method=method, turn_id=turn_id, hook_run=run))


class ToolHandler(Protocol):
    @property
    def spec(self) -> ToolSpec:
        """Describe the tool to the model and validator."""

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        """Execute already-validated arguments."""

    def approval_requirement(
        self, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequirement:
        """Classify the proposed action before execution."""

    def approval_request(
        self, call_id: str, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequest:
        """Build a structured approval request for this action."""


class ToolError(ValueError):
    pass


@dataclass(frozen=True)
class SandboxDenial:
    operation: str
    target: str
    profile: str
    reason: str


class SandboxDenied(ToolError):
    def __init__(self, denial: SandboxDenial) -> None:
        self.denial = denial
        super().__init__(
            f"sandbox profile {denial.profile!r} denied "
            f"{denial.operation} access to {denial.target!r}: {denial.reason}"
        )


class ApprovalAborted(RuntimeError):
    pass


class ReviewDecision(str, Enum):
    APPROVED = "approved"
    APPROVED_FOR_SESSION = "approved_for_session"
    DENIED = "denied"
    ABORT = "abort"


class ApprovalRequirement(str, Enum):
    SKIP = "skip"
    NEEDS_APPROVAL = "needs_approval"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    tool_name: str
    summary: str
    keys: tuple[str, ...]
    reason: str | None = None
    available_decisions: tuple[ReviewDecision, ...] = (
        ReviewDecision.APPROVED,
        ReviewDecision.APPROVED_FOR_SESSION,
        ReviewDecision.DENIED,
        ReviewDecision.ABORT,
    )


ApprovalDecider: TypeAlias = Callable[[ApprovalRequest], ReviewDecision]


class ApprovalStore:
    """Cache only explicit session-scoped approvals by exact action key."""

    def __init__(self) -> None:
        self._approved_keys: set[str] = set()

    def covers(self, keys: Sequence[str]) -> bool:
        return bool(keys) and all(key in self._approved_keys for key in keys)

    def approve_for_session(self, keys: Sequence[str]) -> None:
        self._approved_keys.update(keys)


class ApprovalOrchestrator:
    """Approve intent, then let the runtime sandbox enforce actual access."""

    def __init__(
        self,
        decider: ApprovalDecider,
        *,
        store: ApprovalStore | None = None,
        event_sink: EventSink | None = None,
        hooks: HookEngine | None = None,
    ) -> None:
        self.decider = decider
        self.store = store or ApprovalStore()
        self.event_sink = event_sink or (lambda _event: None)
        self.hooks = hooks or HookEngine()

    def run(
        self,
        handler: ToolHandler,
        call_id: str,
        arguments: Mapping[str, JsonType],
        *,
        turn_id: str,
    ) -> str:
        requirement = handler.approval_requirement(arguments)
        if requirement is ApprovalRequirement.FORBIDDEN:
            raise ToolError(f"{handler.spec.name} is forbidden by policy")
        if requirement is ApprovalRequirement.SKIP:
            return self._execute(handler, arguments, turn_id=turn_id)

        try:
            request = handler.approval_request(call_id, arguments)
        except SandboxDenied as error:
            self._emit_sandbox_denial(error.denial, turn_id=turn_id)
            raise
        if self.store.covers(request.keys):
            return self._execute(handler, arguments, turn_id=turn_id)

        hook_decision = self.hooks.run_permission_request(
            turn_id=turn_id,
            call_id=call_id,
            tool_name=handler.spec.name,
            arguments=arguments,
        )
        if hook_decision is not None:
            decision, reason = hook_decision
            if decision is PermissionHookDecision.ALLOW:
                return self._execute(handler, arguments, turn_id=turn_id)
            raise ToolError(reason or f"{handler.spec.name} denied by permission hook")

        self.event_sink(
            Event(
                method="approval/requested",
                turn_id=turn_id,
                approval_request=request,
            )
        )
        decision = self.decider(request)
        if decision not in request.available_decisions:
            raise ToolError(f"unsupported approval decision {decision.value!r}")
        self.event_sink(
            Event(
                method="approval/resolved",
                turn_id=turn_id,
                approval_request=request,
                approval_decision=decision,
            )
        )

        if decision is ReviewDecision.APPROVED_FOR_SESSION:
            self.store.approve_for_session(request.keys)
            return self._execute(handler, arguments, turn_id=turn_id)
        if decision is ReviewDecision.APPROVED:
            return self._execute(handler, arguments, turn_id=turn_id)
        if decision is ReviewDecision.ABORT:
            raise ApprovalAborted(f"{handler.spec.name} aborted by user")
        raise ToolError(f"{handler.spec.name} rejected by user")

    def _execute(
        self,
        handler: ToolHandler,
        arguments: Mapping[str, JsonType],
        *,
        turn_id: str,
    ) -> str:
        try:
            return handler.handle(arguments)
        except SandboxDenied as error:
            self._emit_sandbox_denial(error.denial, turn_id=turn_id)
            raise

    def _emit_sandbox_denial(self, denial: SandboxDenial, *, turn_id: str) -> None:
        self.event_sink(
            Event(
                method="sandbox/denied",
                turn_id=turn_id,
                sandbox_denial=denial,
            )
        )


class ToolRegistry:
    """Keep model-visible specs tied to executable handlers."""

    def __init__(self, handlers: Sequence[ToolHandler] = ()) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        for handler in handlers:
            self.register(handler)

    def register(self, handler: ToolHandler) -> None:
        name = handler.spec.name
        if name in self._handlers:
            raise ToolError(f"tool {name!r} is already registered")
        self._handlers[name] = handler

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(handler.spec for handler in self._handlers.values())

    def resolve(
        self, name: str, arguments: Mapping[str, JsonType]
    ) -> tuple[ToolHandler, Mapping[str, JsonType]]:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool {name!r}")
        validated = validate_arguments(handler.spec, arguments)
        return handler, validated


class ToolRouter:
    """Expose specs to the model and route calls back to the registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        orchestrator: ApprovalOrchestrator,
        hooks: HookEngine | None = None,
    ) -> None:
        self.registry = registry
        self.orchestrator = orchestrator
        self.hooks = hooks or HookEngine()

    def model_visible_specs(self) -> tuple[ToolSpec, ...]:
        return self.registry.specs()

    def dispatch(self, call: FunctionCall, *, turn_id: str = "turn") -> ToolResult:
        try:
            handler, arguments = self.registry.resolve(call.name, call.arguments)
            pre = self.hooks.run_pre_tool_use(
                turn_id=turn_id,
                call_id=call.call_id,
                tool_name=call.name,
                arguments=arguments,
            )
            if pre.block_reason:
                raise ToolError(
                    f"{call.name} blocked by pre-tool-use hook: {pre.block_reason}"
                )
            arguments = validate_arguments(handler.spec, pre.arguments)
            output = self.orchestrator.run(
                handler, call.call_id, arguments, turn_id=turn_id
            )
        except ApprovalAborted:
            raise
        except ToolError as error:
            return ToolResult(output=f"Error: {error}", success=False)
        output = self.hooks.run_post_tool_use(
            turn_id=turn_id,
            call_id=call.call_id,
            tool_name=call.name,
            arguments=arguments,
            output=output,
        )
        return ToolResult(output=output, success=True)


def validate_arguments(
    spec: ToolSpec, arguments: Mapping[str, JsonType]
) -> Mapping[str, JsonType]:
    """Validate the small schema subset used by this teaching runtime."""
    missing = [name for name in spec.required if name not in arguments]
    if missing:
        raise ToolError(f"{spec.name} missing required argument {missing[0]!r}")

    if not spec.additional_properties:
        unknown = [name for name in arguments if name not in spec.properties]
        if unknown:
            raise ToolError(f"{spec.name} received unknown argument {unknown[0]!r}")

    for name, expected_type in spec.properties.items():
        if name in arguments and type(arguments[name]) is not expected_type:
            raise ToolError(
                f"{spec.name} argument {name!r} must be {expected_type.__name__}"
            )
    return arguments


@dataclass(frozen=True)
class ReadResult:
    path: str
    content: str
    chars: int

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)


@dataclass(frozen=True)
class PatchChange:
    operation: str
    path: str
    old_text: str | None = None
    new_text: str | None = None


@dataclass(frozen=True)
class PatchPlan:
    changes: tuple[PatchChange, ...]


@dataclass(frozen=True)
class PatchResult:
    changes: tuple[dict[str, str], ...]

    def to_json(self) -> str:
        return json.dumps({"changes": self.changes}, sort_keys=True)


class AccessMode(str, Enum):
    DENY = "deny"
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class FileSystemRule:
    path: Path
    access: AccessMode

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise ValueError("filesystem sandbox rules require absolute paths")
        object.__setattr__(self, "path", self.path.resolve())


@dataclass(frozen=True)
class PermissionProfile:
    """Teaching permission profile compiled into runtime-enforced rules."""

    name: str
    file_system: tuple[FileSystemRule, ...]
    network_enabled: bool = False

    @classmethod
    def read_only(cls, root: Path) -> PermissionProfile:
        return cls(
            name="read-only",
            file_system=(FileSystemRule(root.resolve(), AccessMode.READ),),
        )

    @classmethod
    def workspace_write(cls, root: Path) -> PermissionProfile:
        root = root.resolve()
        return cls(
            name="workspace-write",
            file_system=(
                FileSystemRule(root, AccessMode.WRITE),
                FileSystemRule(root / ".git", AccessMode.DENY),
                FileSystemRule(root / ".codex", AccessMode.DENY),
            ),
        )

    @classmethod
    def danger_full_access(cls, root: Path) -> PermissionProfile:
        return cls(
            name="danger-full-access",
            file_system=(FileSystemRule(root.resolve(), AccessMode.WRITE),),
            network_enabled=True,
        )


class ConfigSource(str, Enum):
    SYSTEM = "system"
    USER = "user"
    PROJECT = "project"
    SESSION = "session"

    @property
    def precedence(self) -> int:
        return {
            ConfigSource.SYSTEM: 0,
            ConfigSource.USER: 1,
            ConfigSource.PROJECT: 2,
            ConfigSource.SESSION: 3,
        }[self]


class TrustLevel(str, Enum):
    UNKNOWN = "unknown"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


PROJECT_LOCAL_CONFIG_DENYLIST = frozenset(
    {
        "model_provider",
        "notify",
        "openai_base_url",
        "profile",
        "profiles",
    }
)


@dataclass(frozen=True)
class ConfigLayer:
    name: str
    source: ConfigSource
    values: Mapping[str, Any]
    disabled_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self.disabled_reason is None


@dataclass(frozen=True)
class ConfigRequirements:
    allowed_permission_profiles: tuple[str, ...]
    default_permission_profile: str

    def __post_init__(self) -> None:
        if self.default_permission_profile not in self.allowed_permission_profiles:
            raise ValueError("requirements default must be in allowed profiles")


@dataclass(frozen=True)
class ConfigLock:
    """Teaching snapshot used to detect drift in resolved runtime values."""

    values: Mapping[str, Any]

    def verify(self, candidate: Mapping[str, Any]) -> None:
        if self.values != candidate:
            raise ConfigError("resolved config drifted from the config lock")


@dataclass(frozen=True)
class ResolvedRuntimeConfig:
    values: Mapping[str, Any]
    origins: Mapping[str, str]
    active_permission_profile: str
    permission_profile: PermissionProfile
    approval_policy: str
    warnings: tuple[str, ...]
    lock: ConfigLock


class ConfigError(ValueError):
    pass


class ConfigLayerStack:
    """Merge enabled layers, preserve origins, then apply separate requirements."""

    def __init__(
        self,
        layers: Sequence[ConfigLayer],
        *,
        trust_level: TrustLevel,
        requirements: ConfigRequirements,
    ) -> None:
        if any(
            left.source.precedence > right.source.precedence
            for left, right in zip(layers, layers[1:])
        ):
            raise ConfigError("config layers must be ordered by increasing precedence")
        self.layers = tuple(self._prepare_layer(layer, trust_level) for layer in layers)
        self.trust_level = trust_level
        self.requirements = requirements
        self.warnings: list[str] = []
        for original, prepared in zip(layers, self.layers):
            removed = (
                set(original.values) - set(prepared.values)
                if prepared.enabled
                else set()
            )
            if original.source is ConfigSource.PROJECT and removed:
                self.warnings.append(
                    f"project layer {original.name!r} ignored keys: "
                    + ", ".join(sorted(removed))
                )

    def _prepare_layer(
        self, layer: ConfigLayer, trust_level: TrustLevel
    ) -> ConfigLayer:
        if layer.source is not ConfigSource.PROJECT:
            return ConfigLayer(
                layer.name,
                layer.source,
                copy.deepcopy(layer.values),
                layer.disabled_reason,
            )
        if trust_level is not TrustLevel.TRUSTED:
            return ConfigLayer(
                layer.name,
                layer.source,
                {},
                disabled_reason=f"project is {trust_level.value}",
            )
        sanitized = {
            key: copy.deepcopy(value)
            for key, value in layer.values.items()
            if key not in PROJECT_LOCAL_CONFIG_DENYLIST
        }
        return ConfigLayer(layer.name, layer.source, sanitized, layer.disabled_reason)

    def effective_config(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for layer in self.layers:
            if layer.enabled:
                _merge_config(merged, layer.values)
        return merged

    def origins(self) -> dict[str, str]:
        origins: dict[str, str] = {}
        for layer in self.layers:
            if layer.enabled:
                _record_origins(layer.values, layer.name, origins)
        return origins

    def resolve(self, root: Path) -> ResolvedRuntimeConfig:
        effective = self.effective_config()
        requested = str(
            effective.get("default_permissions")
            or (
                ":workspace"
                if self.trust_level in (TrustLevel.TRUSTED, TrustLevel.UNTRUSTED)
                else ":read-only"
            )
        )
        active = requested
        warnings = list(self.warnings)
        if active not in self.requirements.allowed_permission_profiles:
            warnings.append(
                f"permission profile {active!r} is disallowed by requirements; "
                f"using {self.requirements.default_permission_profile!r}"
            )
            active = self.requirements.default_permission_profile

        profile = _permission_profile_from_id(active, root)
        approval_policy = str(
            effective.get("approval_policy")
            or (
                "unless-trusted"
                if self.trust_level is TrustLevel.UNTRUSTED
                else "on-request"
            )
        )
        resolved_values = copy.deepcopy(effective)
        resolved_values["active_permission_profile"] = active
        resolved_values["approval_policy"] = approval_policy
        return ResolvedRuntimeConfig(
            values=resolved_values,
            origins=self.origins(),
            active_permission_profile=active,
            permission_profile=profile,
            approval_policy=approval_policy,
            warnings=tuple(warnings),
            lock=ConfigLock(copy.deepcopy(resolved_values)),
        )


def _merge_config(base: dict[str, Any], overlay: Mapping[str, Any]) -> None:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _merge_config(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def _record_origins(
    values: Mapping[str, Any],
    layer_name: str,
    origins: dict[str, str],
    prefix: str = "",
) -> None:
    for key, value in values.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            origins.pop(path, None)
            _record_origins(value, layer_name, origins, path)
        else:
            for existing in tuple(origins):
                if existing == path or existing.startswith(f"{path}."):
                    del origins[existing]
            origins[path] = layer_name


def _permission_profile_from_id(profile_id: str, root: Path) -> PermissionProfile:
    if profile_id == ":read-only":
        return PermissionProfile.read_only(root)
    if profile_id == ":workspace":
        return PermissionProfile.workspace_write(root)
    if profile_id == ":danger-full-access":
        return PermissionProfile.danger_full_access(root)
    raise ConfigError(f"unknown teaching permission profile {profile_id!r}")


class WorkspaceSandbox:
    """Resolve the effective rule and deny access before side effects occur."""

    def __init__(self, profile: PermissionProfile) -> None:
        self.profile = profile

    def check_read(self, path: Path) -> None:
        access = self._access_for(path)
        if access not in (AccessMode.READ, AccessMode.WRITE):
            self._deny("read", path, "no matching readable rule")

    def check_write(self, path: Path) -> None:
        access = self._access_for(path)
        if access is not AccessMode.WRITE:
            self._deny("write", path, f"effective access is {access.value}")

    def check_network(self, target: str) -> None:
        if not self.profile.network_enabled:
            self._deny("network", target, "outbound network is restricted")

    def _access_for(self, path: Path) -> AccessMode:
        resolved = path.resolve()
        matches = [
            rule
            for rule in self.profile.file_system
            if resolved == rule.path or resolved.is_relative_to(rule.path)
        ]
        if not matches:
            return AccessMode.DENY
        precedence = {AccessMode.READ: 0, AccessMode.WRITE: 1, AccessMode.DENY: 2}
        return max(
            matches,
            key=lambda rule: (len(rule.path.parts), precedence[rule.access]),
        ).access

    def _deny(self, operation: str, target: Path | str, reason: str) -> None:
        raise SandboxDenied(
            SandboxDenial(
                operation=operation,
                target=str(target),
                profile=self.profile.name,
                reason=reason,
            )
        )


class Workspace:
    """Keep paths inside the workspace, then apply the active sandbox profile."""

    def __init__(
        self,
        root: Path,
        profile: PermissionProfile | None = None,
    ) -> None:
        self.root = root.resolve()
        self.sandbox = WorkspaceSandbox(
            profile or PermissionProfile.workspace_write(self.root)
        )

    def resolve(self, relative_path: str) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            raise ToolError("absolute paths are outside this teaching workspace")
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolError(f"path escapes workspace: {relative_path!r}")
        return resolved

    def read_text(self, relative_path: str, *, max_chars: int) -> ReadResult:
        if max_chars < 0:
            raise ToolError("max_chars must be non-negative")
        path = self.resolve(relative_path)
        self.sandbox.check_read(path)
        if not path.is_file():
            raise ToolError(f"file does not exist: {relative_path!r}")
        with path.open(encoding="utf-8") as file:
            content = file.read(max_chars + 1)
        if len(content) > max_chars:
            raise ToolError(
                f"file {relative_path!r} exceeds limit of {max_chars} chars"
            )
        return ReadResult(relative_path, content, len(content))

    def apply_patch(self, patch: str) -> PatchResult:
        plan = parse_patch(patch)
        staged = self._verify(plan, enforce_write_permissions=True)
        summary: list[dict[str, str]] = []
        for change, path, new_content in staged:
            if change.operation == "delete":
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                assert new_content is not None
                path.write_text(new_content, encoding="utf-8")
            summary.append({"operation": change.operation, "path": change.path})
        return PatchResult(tuple(summary))

    def preview_patch(self, patch: str) -> PatchPlan:
        """Verify patch shape for approval without pretending it already has access."""
        plan = parse_patch(patch)
        self._verify(plan, enforce_write_permissions=False)
        return plan

    def _verify(
        self, plan: PatchPlan, *, enforce_write_permissions: bool
    ) -> list[tuple[PatchChange, Path, str | None]]:
        """Validate the complete plan before committing any filesystem change."""
        staged: list[tuple[PatchChange, Path, str | None]] = []
        seen: set[Path] = set()
        for change in plan.changes:
            path = self.resolve(change.path)
            if path in seen:
                raise ToolError(f"patch targets {change.path!r} more than once")
            seen.add(path)
            if enforce_write_permissions:
                self.sandbox.check_write(path)

            if change.operation == "add":
                if path.exists():
                    raise ToolError(f"add target already exists: {change.path!r}")
                staged.append((change, path, change.new_text))
                continue

            self.sandbox.check_read(path)
            if not path.is_file():
                raise ToolError(f"{change.operation} target is not a file: {change.path!r}")
            original = path.read_text(encoding="utf-8")
            if change.operation == "delete":
                staged.append((change, path, None))
                continue

            assert change.old_text is not None and change.new_text is not None
            occurrences = original.count(change.old_text)
            if occurrences != 1:
                raise ToolError(
                    f"expected text for {change.path!r} occurs {occurrences} times"
                )
            staged.append(
                (change, path, original.replace(change.old_text, change.new_text, 1))
            )
        return staged


def parse_patch(patch: str) -> PatchPlan:
    """Parse a deliberately small subset of the Codex apply_patch grammar."""
    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise ToolError("patch must start with '*** Begin Patch'")
    if lines[-1].strip() != "*** End Patch":
        raise ToolError("patch must end with '*** End Patch'")

    changes: list[PatchChange] = []
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        index += 1
        if header.startswith("*** Add File: "):
            path = header.removeprefix("*** Add File: ")
            added: list[str] = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                if not lines[index].startswith("+"):
                    raise ToolError("add-file content lines must start with '+'")
                added.append(lines[index][1:])
                index += 1
            if not added:
                raise ToolError(f"add file {path!r} requires content")
            changes.append(PatchChange("add", path, new_text="\n".join(added) + "\n"))
            continue

        if header.startswith("*** Delete File: "):
            path = header.removeprefix("*** Delete File: ")
            changes.append(PatchChange("delete", path))
            continue

        if header.startswith("*** Update File: "):
            path = header.removeprefix("*** Update File: ")
            if index >= len(lines) - 1 or lines[index] != "@@":
                raise ToolError(f"update file {path!r} requires '@@'")
            index += 1
            old: list[str] = []
            new: list[str] = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                line = lines[index]
                index += 1
                if line.startswith("-"):
                    old.append(line[1:])
                elif line.startswith("+"):
                    new.append(line[1:])
                elif line.startswith(" "):
                    old.append(line[1:])
                    new.append(line[1:])
                else:
                    raise ToolError("update lines must start with '-', '+', or space")
            if not old:
                raise ToolError(f"update file {path!r} requires expected old text")
            changes.append(
                PatchChange(
                    "update",
                    path,
                    old_text="\n".join(old) + "\n",
                    new_text="\n".join(new) + ("\n" if new else ""),
                )
            )
            continue

        raise ToolError(f"invalid patch header: {header!r}")

    if not changes:
        raise ToolError("patch must contain at least one file change")
    return PatchPlan(tuple(changes))


class ReadFileHandler:
    spec = ToolSpec(
        name="read_file",
        description="Read one UTF-8 text file inside the workspace.",
        properties={"path": str, "max_chars": int},
        required=("path",),
    )

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        return self.workspace.read_text(
            str(arguments["path"]), max_chars=int(arguments.get("max_chars", 4000))
        ).to_json()

    def approval_requirement(
        self, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequirement:
        return ApprovalRequirement.SKIP

    def approval_request(
        self, call_id: str, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequest:
        raise ToolError("read_file does not require approval")


class ApplyPatchHandler:
    spec = ToolSpec(
        name="apply_patch",
        description="Apply a structured patch after validating every target.",
        properties={"patch": str},
        required=("patch",),
    )

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        return self.workspace.apply_patch(str(arguments["patch"])).to_json()

    def approval_requirement(
        self, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequirement:
        return ApprovalRequirement.NEEDS_APPROVAL

    def approval_request(
        self, call_id: str, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequest:
        plan = self.workspace.preview_patch(str(arguments["patch"]))
        paths = tuple(change.path for change in plan.changes)
        return ApprovalRequest(
            approval_id=call_id,
            tool_name=self.spec.name,
            summary="Modify files: " + ", ".join(paths),
            keys=tuple(f"apply_patch:{path}" for path in paths),
            reason="The proposed patch changes workspace files.",
        )


class NetworkProbeHandler:
    spec = ToolSpec(
        name="network_probe",
        description="Simulate contacting one URL after checking network permission.",
        properties={"url": str},
        required=("url",),
    )

    def __init__(self, sandbox: WorkspaceSandbox) -> None:
        self.sandbox = sandbox

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        url = str(arguments["url"])
        self.sandbox.check_network(url)
        return json.dumps({"contacted": url}, sort_keys=True)

    def approval_requirement(
        self, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequirement:
        return ApprovalRequirement.SKIP

    def approval_request(
        self, call_id: str, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequest:
        raise ToolError("network_probe does not require approval in this teaching policy")


class SimulatedExecHandler:
    """Classify a tokenized command, then return a deterministic offline result."""

    spec = ToolSpec(
        name="exec_command",
        description="Simulate one tokenized command after exec policy evaluation.",
        properties={"command": list},
        required=("command",),
    )

    def __init__(self, policy: ExecPolicy) -> None:
        self.policy = policy
        self.executions: list[tuple[str, ...]] = []

    def _command(self, arguments: Mapping[str, JsonType]) -> tuple[str, ...]:
        raw = arguments["command"]
        if not isinstance(raw, list) or not raw or not all(
            isinstance(token, str) for token in raw
        ):
            raise ToolError("exec_command argument 'command' must be a non-empty string list")
        return tuple(raw)

    def _evaluation(self, arguments: Mapping[str, JsonType]) -> PolicyEvaluation:
        return self.policy.evaluate(self._command(arguments))

    def approval_requirement(
        self, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequirement:
        decision = self._evaluation(arguments).decision
        return {
            PolicyDecision.ALLOW: ApprovalRequirement.SKIP,
            PolicyDecision.PROMPT: ApprovalRequirement.NEEDS_APPROVAL,
            PolicyDecision.FORBIDDEN: ApprovalRequirement.FORBIDDEN,
        }[decision]

    def approval_request(
        self, call_id: str, arguments: Mapping[str, JsonType]
    ) -> ApprovalRequest:
        command = self._command(arguments)
        evaluation = self._evaluation(arguments)
        reasons = [
            rule.justification
            for rule in evaluation.matched_rules
            if rule.justification
        ]
        return ApprovalRequest(
            approval_id=call_id,
            tool_name=self.spec.name,
            summary="Run command: " + " ".join(command),
            keys=("exec:" + "\x00".join(command),),
            reason=reasons[-1] if reasons else "Command requires approval by policy.",
        )

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        command = self._command(arguments)
        self.executions.append(command)
        return json.dumps({"command": command, "status": "simulated"}, sort_keys=True)


def default_router(
    workspace: Workspace,
    *,
    decider: ApprovalDecider = lambda _request: ReviewDecision.APPROVED,
    store: ApprovalStore | None = None,
    event_sink: EventSink | None = None,
    hooks: HookEngine | None = None,
    exec_policy: ExecPolicy | None = None,
) -> ToolRouter:
    hooks = hooks or HookEngine(event_sink=event_sink)
    return ToolRouter(
        ToolRegistry(
            [
                ReadFileHandler(workspace),
                ApplyPatchHandler(workspace),
                NetworkProbeHandler(workspace.sandbox),
                SimulatedExecHandler(exec_policy or ExecPolicy()),
            ]
        ),
        ApprovalOrchestrator(
            decider, store=store, event_sink=event_sink, hooks=hooks
        ),
        hooks,
    )


DEFAULT_AGENTS_MD_FILENAME = "AGENTS.md"
LOCAL_AGENTS_MD_FILENAME = "AGENTS.override.md"
AGENTS_MD_SEPARATOR = "\n\n--- project-doc ---\n\n"


@dataclass(frozen=True)
class InstructionEntry:
    contents: str
    source_path: Path


@dataclass(frozen=True)
class LoadedProjectInstructions:
    """Ordered, source-aware instructions for one selected working directory."""

    cwd: Path
    user_instructions: str | None = None
    user_source: Path | None = None
    entries: tuple[InstructionEntry, ...] = ()
    warnings: tuple[str, ...] = ()

    def text(self) -> str:
        parts: list[str] = []
        if self.user_instructions and self.user_instructions.strip():
            parts.append(self.user_instructions)
        project_text = "\n\n".join(
            entry.contents for entry in self.entries if entry.contents.strip()
        )
        if project_text:
            if parts:
                return parts[0] + AGENTS_MD_SEPARATOR + project_text
            return project_text
        return parts[0] if parts else ""

    def sources(self) -> tuple[Path, ...]:
        sources: list[Path] = []
        if (
            self.user_source is not None
            and self.user_instructions
            and self.user_instructions.strip()
        ):
            sources.append(self.user_source)
        sources.extend(
            entry.source_path for entry in self.entries if entry.contents.strip()
        )
        return tuple(sources)

    def render(self) -> str:
        text = self.text()
        if not text:
            return ""
        return (
            f"# AGENTS.md instructions for {self.cwd}\n\n"
            f"<INSTRUCTIONS>\n{text}\n</INSTRUCTIONS>"
        )


class AgentsMdLoader:
    """Load one project environment's scoped instructions from root to cwd."""

    def __init__(
        self,
        *,
        max_bytes: int = 32 * 1024,
        fallback_filenames: Sequence[str] = (),
        project_root_markers: Sequence[str] = (".git",),
    ) -> None:
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        self.max_bytes = max_bytes
        self.fallback_filenames = tuple(fallback_filenames)
        self.project_root_markers = tuple(project_root_markers)

    def candidate_filenames(self) -> tuple[str, ...]:
        names: list[str] = [LOCAL_AGENTS_MD_FILENAME, DEFAULT_AGENTS_MD_FILENAME]
        for candidate in self.fallback_filenames:
            if candidate and candidate not in names:
                names.append(candidate)
        return tuple(names)

    def discover(self, cwd: Path) -> tuple[Path, ...]:
        cwd = cwd.absolute()
        search_dirs = self._search_dirs(cwd)
        found: list[Path] = []
        for directory in search_dirs:
            for filename in self.candidate_filenames():
                candidate = directory / filename
                if candidate.is_file():
                    found.append(candidate)
                    break
        return tuple(found)

    def load(
        self,
        cwd: Path,
        *,
        user_instructions: str | None = None,
        user_source: Path | None = None,
    ) -> LoadedProjectInstructions | None:
        cwd = cwd.absolute()
        entries: list[InstructionEntry] = []
        warnings: list[str] = []
        remaining = self.max_bytes
        if remaining:
            for path in self.discover(cwd):
                if remaining == 0:
                    break
                try:
                    data = path.read_bytes()
                except FileNotFoundError:
                    continue
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError:
                    warnings.append(
                        f"Project AGENTS.md instructions from {str(path)!r} "
                        "contain invalid UTF-8; invalid byte sequences were replaced."
                    )
                if len(data) > remaining:
                    warnings.append(
                        f"Project doc {str(path)!r} exceeds remaining budget "
                        f"({remaining} bytes); truncating."
                    )
                    data = data[:remaining]
                text = data.decode("utf-8", errors="replace")
                if text.strip():
                    entries.append(InstructionEntry(text, path))
                    remaining -= len(data)

        loaded = LoadedProjectInstructions(
            cwd=cwd,
            user_instructions=(
                user_instructions
                if user_instructions is not None and user_instructions.strip()
                else None
            ),
            user_source=user_source,
            entries=tuple(entries),
            warnings=tuple(warnings),
        )
        return loaded if loaded.text() else None

    def _search_dirs(self, cwd: Path) -> tuple[Path, ...]:
        if not self.project_root_markers:
            return (cwd,)
        project_root: Path | None = None
        for ancestor in (cwd, *cwd.parents):
            if any((ancestor / marker).exists() for marker in self.project_root_markers):
                project_root = ancestor
                break
        if project_root is None:
            return (cwd,)

        directories: list[Path] = []
        cursor = cwd
        while True:
            directories.append(cursor)
            if cursor == project_root:
                break
            cursor = cursor.parent
        directories.reverse()
        return tuple(directories)


@dataclass(frozen=True)
class ModelMessage:
    role: str
    sections: tuple[str, ...]

    def text(self) -> str:
        return "\n\n".join(section for section in self.sections if section.strip())


class ContextFragment:
    role: str
    start_marker: str
    end_marker: str

    def body(self) -> str:
        """Return the fragment body between the markers."""
        raise NotImplementedError

    def render(self) -> str:
        body = self.body()
        if not self.start_marker and not self.end_marker:
            return body
        return f"{self.start_marker}{body}{self.end_marker}"


def matches_marked_text(start_marker: str, end_marker: str, text: str) -> bool:
    if not start_marker or not end_marker:
        return False
    stripped = text.strip()
    return stripped.lower().startswith(start_marker.lower()) and stripped.lower().endswith(
        end_marker.lower()
    )


@dataclass(frozen=True)
class FragmentRegistration:
    start_marker: str
    end_marker: str
    prefix_only: bool = False

    def matches(self, text: str) -> bool:
        if self.prefix_only:
            return text.strip().lower().startswith(self.start_marker.lower())
        return matches_marked_text(self.start_marker, self.end_marker, text)


USER_FRAGMENT_REGISTRY: tuple[FragmentRegistration, ...] = (
    FragmentRegistration("<environment_context>", "</environment_context>"),
    FragmentRegistration("# AGENTS.md instructions", "</INSTRUCTIONS>"),
    FragmentRegistration("<skill>", "</skill>"),
    FragmentRegistration("<user_shell_command>", "</user_shell_command>"),
)

DEVELOPER_CONTEXT_PREFIXES: tuple[str, ...] = (
    "<permissions instructions>",
    "<model_switch>",
    "<collaboration_mode>",
    "<token_budget>",
    "<skills_instructions>",
)


def is_contextual_user_text(text: str) -> bool:
    return any(registration.matches(text) for registration in USER_FRAGMENT_REGISTRY) or (
        _matches_external_user_fragment(text)
    )


def _matches_external_user_fragment(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith("<external_"):
        return False
    open_end = stripped.find(">")
    if open_end == -1:
        return False
    key = stripped[len("<external_") : open_end]
    return bool(key) and stripped.endswith(f"</external_{key}>")


def is_contextual_developer_text(text: str) -> bool:
    stripped = text.strip()
    return any(
        stripped.lower().startswith(prefix.lower())
        for prefix in DEVELOPER_CONTEXT_PREFIXES
    )


@dataclass(frozen=True)
class EnvironmentFragment(ContextFragment):
    cwd: Path | None = None
    shell: str | None = None
    current_date: str | None = None
    timezone: str | None = None
    network: str | None = None
    workspace_roots: tuple[Path, ...] = ()
    permission_profile_name: str | None = None

    role: str = "user"
    start_marker: str = "<environment_context>"
    end_marker: str = "</environment_context>"

    def body(self) -> str:
        lines: list[str] = []
        if self.cwd is not None:
            lines.append(f"  <cwd>{self.cwd}</cwd>")
        if self.shell:
            lines.append(f"  <shell>{self.shell}</shell>")
        if self.current_date:
            lines.append(f"  <current_date>{self.current_date}</current_date>")
        if self.timezone:
            lines.append(f"  <timezone>{self.timezone}</timezone>")
        if self.network:
            lines.append(f"  <network>{self.network}</network>")
        if self.workspace_roots or self.permission_profile_name:
            roots = "".join(f"<root>{root}</root>" for root in self.workspace_roots)
            profile = self.permission_profile_name or "unknown"
            lines.append(
                "  <filesystem>"
                f"<workspace_roots>{roots}</workspace_roots>"
                f"<permission_profile type=\"{profile}\" />"
                "</filesystem>"
            )
        return "\n" + "\n".join(lines) + "\n"

    def equals_except_shell(self, other: EnvironmentFragment) -> bool:
        return (
            self.cwd == other.cwd
            and self.current_date == other.current_date
            and self.timezone == other.timezone
            and self.network == other.network
            and self.workspace_roots == other.workspace_roots
            and self.permission_profile_name == other.permission_profile_name
        )

    def diff_from(self, previous: EnvironmentFragment) -> EnvironmentFragment | None:
        if previous.equals_except_shell(self):
            return None
        return EnvironmentFragment(
            cwd=self.cwd if self.cwd != previous.cwd else None,
            shell=self.shell if self.cwd != previous.cwd else None,
            current_date=self.current_date,
            timezone=self.timezone,
            network=self.network,
            workspace_roots=self.workspace_roots,
            permission_profile_name=self.permission_profile_name,
        )


@dataclass(frozen=True)
class PermissionsFragment(ContextFragment):
    permission_profile: str
    approval_policy: str
    exec_policy_summary: str

    role: str = "developer"
    start_marker: str = "<permissions instructions>"
    end_marker: str = "</permissions instructions>"

    def body(self) -> str:
        return (
            "\n"
            f"permission_profile: {self.permission_profile}\n"
            f"approval_policy: {self.approval_policy}\n"
            f"exec_policy: {self.exec_policy_summary}\n"
        )


@dataclass(frozen=True)
class ModelSwitchFragment(ContextFragment):
    model_instructions: str

    role: str = "developer"
    start_marker: str = "<model_switch>"
    end_marker: str = "</model_switch>"

    def body(self) -> str:
        return (
            "\nThe user was previously using a different model. "
            "Continue with these model instructions:\n\n"
            f"{self.model_instructions}\n"
        )


@dataclass(frozen=True)
class CollaborationFragment(ContextFragment):
    instructions: str

    role: str = "developer"
    start_marker: str = "<collaboration_mode>"
    end_marker: str = "</collaboration_mode>"

    def body(self) -> str:
        return self.instructions


@dataclass(frozen=True)
class TokenBudgetFragment(ContextFragment):
    remaining_tokens: int

    role: str = "developer"
    start_marker: str = "<token_budget>"
    end_marker: str = "</token_budget>"

    def body(self) -> str:
        return f"\nYou have {self.remaining_tokens} tokens left in this context window.\n"


@dataclass(frozen=True)
class ExternalUserFragment(ContextFragment):
    key: str
    value: str
    max_chars: int = 4000

    role: str = "user"
    start_marker: str = "<external_"
    end_marker: str = ">"

    def body(self) -> str:
        value = self.value
        if len(value) > self.max_chars:
            half = max(0, (self.max_chars - 15) // 2)
            value = f"{value[:half]}\n...[truncated]...\n{value[-half:]}"
        return f"{self.key}>{value}</external_{self.key}"

    def render(self) -> str:
        return f"<external_{self.body()}>"


class SkillScope(str, Enum):
    REPO = "repo"
    USER = "user"
    SYSTEM = "system"
    ADMIN = "admin"


SKILL_MD_FILENAME = "SKILL.md"
SKILLS_METADATA_PATH = Path("agents") / "openai.yaml"
SKILLS_INSTRUCTIONS_OPEN_TAG = "<skills_instructions>"
SKILLS_INSTRUCTIONS_CLOSE_TAG = "</skills_instructions>"
COMMON_ENV_VAR_MENTIONS = frozenset(
    {"PATH", "HOME", "PWD", "SHELL", "USER", "TMPDIR", "XDG_CONFIG_HOME"}
)


@dataclass(frozen=True)
class SkillPolicy:
    allow_implicit_invocation: bool = True


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    path_to_skills_md: Path
    scope: SkillScope
    root: Path
    policy: SkillPolicy = field(default_factory=SkillPolicy)

    @property
    def directory(self) -> Path:
        return self.path_to_skills_md.parent

    def allows_implicit_invocation(self) -> bool:
        return self.policy.allow_implicit_invocation


@dataclass(frozen=True)
class SkillRoot:
    path: Path
    scope: SkillScope

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.resolve())


@dataclass(frozen=True)
class SkillLoadError:
    path: Path
    message: str


@dataclass(frozen=True)
class SkillLoadOutcome:
    skills: tuple[SkillMetadata, ...]
    roots: tuple[Path, ...]
    errors: tuple[SkillLoadError, ...] = ()
    disabled_paths: frozenset[Path] = frozenset()

    def enabled_skills(self) -> tuple[SkillMetadata, ...]:
        return tuple(
            skill for skill in self.skills if skill.path_to_skills_md not in self.disabled_paths
        )

    def allowed_skills_for_implicit_invocation(self) -> tuple[SkillMetadata, ...]:
        return tuple(
            skill
            for skill in self.enabled_skills()
            if skill.allows_implicit_invocation()
        )

    def skill_root_by_path(self) -> dict[Path, Path]:
        return {skill.path_to_skills_md: skill.root for skill in self.skills}


class SkillLoader:
    """Discover SKILL.md files and parse the metadata needed for a prompt catalog."""

    def __init__(self, *, max_depth: int = 6, max_dirs_per_root: int = 2000) -> None:
        if max_depth < 0 or max_dirs_per_root < 1:
            raise ValueError("skill scan limits must be positive")
        self.max_depth = max_depth
        self.max_dirs_per_root = max_dirs_per_root

    def load(self, roots: Sequence[SkillRoot]) -> SkillLoadOutcome:
        skills: list[SkillMetadata] = []
        errors: list[SkillLoadError] = []
        used_roots: list[Path] = []
        seen_paths: set[Path] = set()
        for root in roots:
            root_path = root.path.resolve()
            before = len(skills)
            for path in self._discover_skill_files(root_path):
                try:
                    skill = self._parse_skill(path, root)
                except ValueError as error:
                    errors.append(SkillLoadError(path, str(error)))
                    continue
                if skill.path_to_skills_md in seen_paths:
                    continue
                seen_paths.add(skill.path_to_skills_md)
                skills.append(skill)
            if len(skills) > before:
                used_roots.append(root_path)

        return SkillLoadOutcome(
            skills=tuple(sorted(skills, key=_load_order_key)),
            roots=tuple(used_roots),
            errors=tuple(errors),
        )

    def _discover_skill_files(self, root: Path) -> Iterator[Path]:
        if not root.is_dir():
            return
        queue: list[tuple[Path, int]] = [(root, 0)]
        visited: set[Path] = {root.resolve()}
        while queue and len(visited) <= self.max_dirs_per_root:
            directory, depth = queue.pop(0)
            try:
                entries = sorted(directory.iterdir(), key=lambda path: path.name)
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    resolved = entry.resolve()
                    if depth + 1 <= self.max_depth and resolved not in visited:
                        visited.add(resolved)
                        queue.append((resolved, depth + 1))
                    continue
                if entry.is_file() and entry.name == SKILL_MD_FILENAME:
                    yield entry.resolve()

    def _parse_skill(self, path: Path, root: SkillRoot) -> SkillMetadata:
        text = path.read_text(encoding="utf-8")
        frontmatter = _extract_yaml_frontmatter(text)
        if not frontmatter:
            raise ValueError("missing YAML frontmatter delimited by ---")
        fields = _parse_simple_yaml(frontmatter)
        base_name = _single_line(fields.get("name") or path.parent.name)
        description = _single_line(fields.get("description") or "")
        if not base_name:
            raise ValueError("missing field `name`")
        if not description:
            raise ValueError("missing field `description`")
        metadata = _parse_skill_metadata_file(path.parent / SKILLS_METADATA_PATH)
        return SkillMetadata(
            name=base_name,
            description=description,
            path_to_skills_md=path.resolve(),
            scope=root.scope,
            root=root.path,
            policy=metadata,
        )


def _extract_yaml_frontmatter(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            return "\n".join(body)
        body.append(line)
    return None


def _parse_simple_yaml(frontmatter: str) -> dict[str, str]:
    values: dict[str, str] = {}
    lines = frontmatter.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#") or line.startswith(" "):
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value in ("|-", "|"):
            block: list[str] = []
            while index < len(lines) and (
                lines[index].startswith(" ") or not lines[index].strip()
            ):
                block.append(lines[index].strip())
                index += 1
            values[key] = " ".join(part for part in block if part)
        else:
            values[key] = raw_value.strip("\"'")
    return values


def _parse_skill_metadata_file(path: Path) -> SkillPolicy:
    if not path.is_file():
        return SkillPolicy()
    allow_implicit = True
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("allow_implicit_invocation:"):
            _, raw_value = stripped.split(":", 1)
            allow_implicit = raw_value.strip().lower() != "false"
    return SkillPolicy(allow_implicit_invocation=allow_implicit)


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _load_order_key(skill: SkillMetadata) -> tuple[int, str, str]:
    rank = {
        SkillScope.REPO: 0,
        SkillScope.USER: 1,
        SkillScope.SYSTEM: 2,
        SkillScope.ADMIN: 3,
    }[skill.scope]
    return (rank, skill.name, str(skill.path_to_skills_md))


def _prompt_order_key(skill: SkillMetadata) -> tuple[int, str, str]:
    rank = {
        SkillScope.SYSTEM: 0,
        SkillScope.ADMIN: 1,
        SkillScope.REPO: 2,
        SkillScope.USER: 3,
    }[skill.scope]
    return (rank, skill.name, str(skill.path_to_skills_md))


@dataclass(frozen=True)
class SkillRenderReport:
    total_count: int
    included_count: int
    omitted_count: int
    truncated_description_chars: int


@dataclass(frozen=True)
class AvailableSkills:
    root_lines: tuple[str, ...]
    skill_lines: tuple[str, ...]
    report: SkillRenderReport
    warning: str | None = None


class SkillCatalogRenderer:
    """Render a bounded skills catalog: metadata now, full instructions later."""

    def __init__(self, *, metadata_budget_chars: int = 8000) -> None:
        if metadata_budget_chars < 1:
            raise ValueError("skill metadata budget must be positive")
        self.metadata_budget_chars = metadata_budget_chars

    @staticmethod
    def default_budget(context_window: int | None = None) -> int:
        if context_window and context_window > 0:
            return max(1, context_window * 2 // 100)
        return 8000

    def render(self, outcome: SkillLoadOutcome) -> AvailableSkills | None:
        skills = sorted(outcome.allowed_skills_for_implicit_invocation(), key=_prompt_order_key)
        if not skills:
            return None
        aliases = {root: f"r{index}" for index, root in enumerate(outcome.roots)}
        root_lines = tuple(
            f"- `{alias}` = `{root.as_posix()}`" for root, alias in aliases.items()
        )
        lines = [
            _skill_line(skill, _aliased_skill_path(skill, aliases), skill.description)
            for skill in skills
        ]
        rendered, truncated = self._fit_lines(skills, lines, aliases)
        omitted = len(skills) - len(rendered)
        warning = None
        if omitted:
            warning = (
                "Exceeded skills context budget. Some skills were omitted from "
                "the model-visible skills list."
            )
        elif truncated > 100:
            warning = (
                "Skill descriptions were shortened to fit the skills context budget."
            )
        return AvailableSkills(
            root_lines=root_lines,
            skill_lines=tuple(rendered),
            report=SkillRenderReport(len(skills), len(rendered), omitted, truncated),
            warning=warning,
        )

    def _fit_lines(
        self,
        skills: Sequence[SkillMetadata],
        full_lines: Sequence[str],
        aliases: Mapping[Path, str],
    ) -> tuple[list[str], int]:
        if _lines_size(full_lines) <= self.metadata_budget_chars:
            return list(full_lines), 0
        min_lines = [
            _skill_line(skill, _aliased_skill_path(skill, aliases), "")
            for skill in skills
        ]
        if _lines_size(min_lines) > self.metadata_budget_chars:
            kept: list[str] = []
            used = 0
            truncated = 0
            for skill, line in zip(skills, min_lines):
                cost = len(line) + 1
                if used + cost <= self.metadata_budget_chars:
                    kept.append(line)
                    used += cost
                truncated += len(skill.description)
            return kept, truncated

        extra_budget = self.metadata_budget_chars - _lines_size(min_lines)
        allocations = [0 for _ in skills]
        descriptions = [skill.description for skill in skills]
        while extra_budget > 0:
            changed = False
            for index, description in enumerate(descriptions):
                if allocations[index] >= len(description):
                    continue
                allocations[index] += 1
                extra_budget -= 1
                changed = True
                if extra_budget == 0:
                    break
            if not changed:
                break
        rendered = [
            _skill_line(
                skill,
                _aliased_skill_path(skill, aliases),
                skill.description[: allocations[index]],
            )
            for index, skill in enumerate(skills)
        ]
        truncated = sum(
            max(0, len(skill.description) - allocations[index])
            for index, skill in enumerate(skills)
        )
        return rendered, truncated


def _lines_size(lines: Sequence[str]) -> int:
    return sum(len(line) + 1 for line in lines)


def _aliased_skill_path(skill: SkillMetadata, aliases: Mapping[Path, str]) -> str:
    alias = aliases.get(skill.root)
    if alias is None:
        return skill.path_to_skills_md.as_posix()
    try:
        return f"{alias}/{skill.path_to_skills_md.relative_to(skill.root).as_posix()}"
    except ValueError:
        return skill.path_to_skills_md.as_posix()


def _skill_line(skill: SkillMetadata, path: str, description: str) -> str:
    if description:
        return f"- {skill.name}: {description} (file: {path})"
    return f"- {skill.name}: (file: {path})"


@dataclass(frozen=True)
class AvailableSkillsFragment(ContextFragment):
    available: AvailableSkills

    role: str = "developer"
    start_marker: str = SKILLS_INSTRUCTIONS_OPEN_TAG
    end_marker: str = SKILLS_INSTRUCTIONS_CLOSE_TAG

    def body(self) -> str:
        lines = [
            "## Skills",
            (
                "A skill is a set of local instructions to follow that is stored "
                "in a `SKILL.md` file."
            ),
        ]
        if self.available.root_lines:
            lines.append("### Skill roots")
            lines.extend(self.available.root_lines)
        lines.append("### Available skills")
        lines.extend(self.available.skill_lines)
        lines.extend(
            [
                "### How to use skills",
                (
                    "- Discovery: choose from the catalog first; do not assume "
                    "every skill body is already in context."
                ),
                (
                    "- Progressive disclosure: after deciding to use a skill, "
                    "read its `SKILL.md` completely before taking task actions."
                ),
                (
                    "- Resolve relative files such as `scripts/foo.py` from the "
                    "directory that contains that skill's `SKILL.md`."
                ),
            ]
        )
        return "\n" + "\n".join(lines) + "\n"


@dataclass(frozen=True)
class SkillInstructionsFragment(ContextFragment):
    skill: SkillMetadata
    contents: str
    path_label: str | None = None

    role: str = "user"
    start_marker: str = "<skill>"
    end_marker: str = "</skill>"

    def body(self) -> str:
        path = self.path_label or self.skill.path_to_skills_md.as_posix()
        return f"\n<name>{self.skill.name}</name>\n<path>{path}</path>\n{self.contents}\n"


@dataclass(frozen=True)
class SkillSelection:
    name: str
    path: Path | None = None


class SkillMentionResolver:
    """Resolve structured or textual skill mentions without guessing ambiguous names."""

    _plain_pattern = re.compile(r"\$([A-Za-z0-9_:-]+)")
    _linked_pattern = re.compile(r"\[\$([A-Za-z0-9_:-]+)\]\s*\(\s*([^)]+?)\s*\)")

    def resolve(
        self,
        text: str,
        outcome: SkillLoadOutcome,
        *,
        structured: Sequence[SkillSelection] = (),
        connector_names: frozenset[str] = frozenset(),
    ) -> tuple[SkillMetadata, ...]:
        skills = outcome.enabled_skills()
        by_path = {skill.path_to_skills_md: skill for skill in skills}
        by_name: dict[str, list[SkillMetadata]] = {}
        for skill in skills:
            by_name.setdefault(skill.name, []).append(skill)

        selected: list[SkillMetadata] = []
        seen_paths: set[Path] = set()
        blocked_names: set[str] = set()

        for selection in structured:
            if selection.path is None:
                blocked_names.add(selection.name)
                continue
            skill = by_path.get(selection.path.resolve())
            blocked_names.add(selection.name)
            if skill is not None and skill.path_to_skills_md not in outcome.disabled_paths:
                _append_unique(selected, seen_paths, skill)

        for name, raw_path in self._linked_pattern.findall(text):
            if name in COMMON_ENV_VAR_MENTIONS:
                continue
            blocked_names.add(name)
            skill = by_path.get(Path(raw_path.strip()).expanduser().resolve())
            if skill is not None:
                _append_unique(selected, seen_paths, skill)

        for name in self._plain_pattern.findall(text):
            if (
                name in COMMON_ENV_VAR_MENTIONS
                or name in connector_names
                or name in blocked_names
            ):
                continue
            candidates = by_name.get(name, [])
            if len(candidates) == 1:
                _append_unique(selected, seen_paths, candidates[0])

        return tuple(selected)


def _append_unique(
    selected: list[SkillMetadata], seen_paths: set[Path], skill: SkillMetadata
) -> None:
    if skill.path_to_skills_md not in seen_paths:
        selected.append(skill)
        seen_paths.add(skill.path_to_skills_md)


class SkillInjector:
    """Read complete SKILL.md bodies only for skills selected for this turn."""

    def build_injections(
        self,
        selected: Sequence[SkillMetadata],
    ) -> tuple[SkillInstructionsFragment, ...]:
        fragments: list[SkillInstructionsFragment] = []
        for skill in selected:
            contents = skill.path_to_skills_md.read_text(encoding="utf-8")
            fragments.append(SkillInstructionsFragment(skill, contents))
        return tuple(fragments)


@dataclass(frozen=True)
class ImplicitSkillInvocation:
    skill: SkillMetadata
    reason: str


class ImplicitSkillInvocationDetector:
    """Notice follow-up tool activity that proves the agent is using a skill."""

    script_extensions = {".py", ".sh", ".js", ".ts", ".rb", ".pl", ".ps1"}
    runner_names = {"python", "python3", "bash", "zsh", "sh", "node", "deno", "ruby", "perl", "pwsh"}

    def detect(
        self,
        outcome: SkillLoadOutcome,
        command: str | Sequence[str],
        *,
        workdir: Path,
    ) -> ImplicitSkillInvocation | None:
        tokens = _command_tokens(command)
        if not tokens:
            return None
        workdir = workdir.resolve()
        for skill in outcome.allowed_skills_for_implicit_invocation():
            if self._reads_skill_doc(tokens, skill, workdir):
                return ImplicitSkillInvocation(skill, "read SKILL.md")
            if self._runs_skill_script(tokens, skill, workdir):
                return ImplicitSkillInvocation(skill, "ran script under skill scripts/")
        return None

    def _reads_skill_doc(
        self, tokens: Sequence[str], skill: SkillMetadata, workdir: Path
    ) -> bool:
        for token in tokens:
            candidate = _resolve_token_path(token, workdir)
            if candidate == skill.path_to_skills_md:
                return True
        return False

    def _runs_skill_script(
        self, tokens: Sequence[str], skill: SkillMetadata, workdir: Path
    ) -> bool:
        script_tokens = tokens
        if Path(tokens[0]).name in self.runner_names:
            if "-c" in tokens[:3]:
                return False
            script_tokens = tokens[1:]
        for token in script_tokens:
            path = Path(token)
            if path.suffix not in self.script_extensions:
                continue
            candidate = _resolve_token_path(token, workdir)
            scripts_dir = (skill.directory / "scripts").resolve()
            if candidate == scripts_dir or candidate.is_relative_to(scripts_dir):
                return True
        return False


def _command_tokens(command: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, str):
        try:
            return tuple(shlex.split(command))
        except ValueError:
            return tuple(command.split())
    return tuple(command)


def _resolve_token_path(token: str, workdir: Path) -> Path:
    cleaned = token.strip("\"'")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        path = workdir / path
    return path.resolve()


class SkillInvocationTracker:
    """Deduplicate implicit invocation telemetry within one turn."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def record(self, invocation: ImplicitSkillInvocation) -> bool:
        key = (invocation.skill.name, invocation.skill.path_to_skills_md.as_posix())
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


@dataclass(frozen=True)
class ContextSnapshot:
    cwd: Path
    shell: str
    current_date: str | None
    timezone: str | None
    permission_profile: str
    approval_policy: str
    exec_policy_summary: str
    model: str
    model_instructions: str
    collaboration_instructions: str | None = None
    remaining_tokens: int | None = None
    network: str | None = None
    workspace_roots: tuple[Path, ...] = ()

    def environment_fragment(self) -> EnvironmentFragment:
        roots = self.workspace_roots or (self.cwd,)
        return EnvironmentFragment(
            cwd=self.cwd,
            shell=self.shell,
            current_date=self.current_date,
            timezone=self.timezone,
            network=self.network,
            workspace_roots=roots,
            permission_profile_name=self.permission_profile,
        )

    def permissions_key(self) -> tuple[str, str, str]:
        return (self.permission_profile, self.approval_policy, self.exec_policy_summary)


class ContextAssembler:
    """Build initial context and later diffs from typed fragments."""

    def build_initial(
        self,
        snapshot: ContextSnapshot,
        *,
        developer_instructions: str | None = None,
        project_instructions: LoadedProjectInstructions | None = None,
        external_user_fragments: Sequence[ExternalUserFragment] = (),
        available_skills: AvailableSkills | None = None,
    ) -> list[ModelMessage]:
        developer_sections: list[str] = [
            PermissionsFragment(
                snapshot.permission_profile,
                snapshot.approval_policy,
                snapshot.exec_policy_summary,
            ).render()
        ]
        if developer_instructions and developer_instructions.strip():
            developer_sections.append(developer_instructions)
        if snapshot.collaboration_instructions:
            developer_sections.append(
                CollaborationFragment(snapshot.collaboration_instructions).render()
            )
        if snapshot.remaining_tokens is not None:
            developer_sections.append(TokenBudgetFragment(snapshot.remaining_tokens).render())
        if available_skills is not None:
            developer_sections.append(AvailableSkillsFragment(available_skills).render())

        user_sections: list[str] = []
        if project_instructions is not None:
            rendered = project_instructions.render()
            if rendered:
                user_sections.append(rendered)
        user_sections.extend(fragment.render() for fragment in external_user_fragments)
        user_sections.append(snapshot.environment_fragment().render())

        return self._messages(developer_sections, user_sections)

    def build_updates(
        self,
        previous: ContextSnapshot | None,
        current: ContextSnapshot,
    ) -> list[ModelMessage]:
        if previous is None:
            return self.build_initial(current)

        developer_sections: list[str] = []
        if previous.model != current.model:
            developer_sections.append(
                ModelSwitchFragment(current.model_instructions).render()
            )
        if previous.permissions_key() != current.permissions_key():
            developer_sections.append(
                PermissionsFragment(
                    current.permission_profile,
                    current.approval_policy,
                    current.exec_policy_summary,
                ).render()
            )
        if previous.collaboration_instructions != current.collaboration_instructions:
            if current.collaboration_instructions:
                developer_sections.append(
                    CollaborationFragment(current.collaboration_instructions).render()
                )
        if previous.remaining_tokens != current.remaining_tokens:
            if current.remaining_tokens is not None:
                developer_sections.append(TokenBudgetFragment(current.remaining_tokens).render())

        environment_diff = current.environment_fragment().diff_from(
            previous.environment_fragment()
        )
        user_sections = [environment_diff.render()] if environment_diff else []

        return self._messages(developer_sections, user_sections)

    def _messages(
        self,
        developer_sections: Sequence[str],
        user_sections: Sequence[str],
    ) -> list[ModelMessage]:
        messages: list[ModelMessage] = []
        dev = tuple(section for section in developer_sections if section.strip())
        usr = tuple(section for section in user_sections if section.strip())
        if dev:
            messages.append(ModelMessage("developer", dev))
        if usr:
            messages.append(ModelMessage("user", usr))
        return messages


def is_context_update_message(message: ModelMessage) -> bool:
    if message.role == "user":
        return any(is_contextual_user_text(section) for section in message.sections)
    if message.role == "developer":
        return any(is_contextual_developer_text(section) for section in message.sections)
    return False


def has_non_contextual_developer_section(message: ModelMessage) -> bool:
    if message.role != "developer":
        return False
    return any(not is_contextual_developer_text(section) for section in message.sections)


@dataclass
class ContextHistory:
    assembler: ContextAssembler = field(default_factory=ContextAssembler)
    items: list[ModelMessage] = field(default_factory=list)
    reference_snapshot: ContextSnapshot | None = None

    def record_context_for_turn(
        self,
        snapshot: ContextSnapshot,
        *,
        developer_instructions: str | None = None,
        project_instructions: LoadedProjectInstructions | None = None,
        available_skills: AvailableSkills | None = None,
    ) -> list[ModelMessage]:
        if self.reference_snapshot is None:
            updates = self.assembler.build_initial(
                snapshot,
                developer_instructions=developer_instructions,
                project_instructions=project_instructions,
                available_skills=available_skills,
            )
        else:
            updates = self.assembler.build_updates(self.reference_snapshot, snapshot)
        self.items.extend(updates)
        self.reference_snapshot = snapshot
        return updates

    def append_user_turn(self, text: str) -> None:
        self.items.append(ModelMessage("user", (text,)))

    def append_assistant_turn(self, text: str) -> None:
        self.items.append(ModelMessage("assistant", (text,)))

    def rollback_last_user_turn(self) -> None:
        user_indices = [
            index
            for index, item in enumerate(self.items)
            if item.role == "user" and not is_context_update_message(item)
        ]
        if not user_indices:
            return
        cut = user_indices[-1]
        while cut > 0 and is_context_update_message(self.items[cut - 1]):
            removed = self.items[cut - 1]
            if (
                removed.role == "developer"
                and has_non_contextual_developer_section(removed)
            ):
                self.reference_snapshot = None
            cut -= 1
        del self.items[cut:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="Update the greeting file")
    args = parser.parse_args()

    ids = IdGenerator()
    reducer = EventReducer()
    streaming_text = False

    def render(event: Event) -> None:
        nonlocal streaming_text
        turn = reducer.apply(event)
        if event.method == "item/agentMessage/delta":
            print(event.delta, end="", flush=True)
            streaming_text = True
        elif event.method == "turn/completed":
            print(f"\nturn status: {turn.status}")
        else:
            if streaming_text:
                print()
                streaming_text = False
            if event.item:
                suffix = f" {type(event.item).__name__}"
            elif event.hook_run:
                suffix = (
                    f" {event.hook_run.event_name.value}:{event.hook_run.hook_name}"
                    f":{event.hook_run.status}"
                )
            else:
                suffix = ""
            print(f"{event.method}{suffix}")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "greeting.txt").write_text("hello\n", encoding="utf-8")
        (root / ".git").mkdir()
        (root / "AGENTS.md").write_text(
            "Use structured patches for repository changes.\n",
            encoding="utf-8",
        )
        nested = root / "packages" / "api"
        nested.mkdir(parents=True)
        (nested / "AGENTS.override.md").write_text(
            "Run API tests after changing this package.\n",
            encoding="utf-8",
        )
        repo_skills = root / ".codex" / "skills"
        lint_skill_dir = repo_skills / "lint-fix"
        lint_skill_dir.mkdir(parents=True)
        (lint_skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: lint-fix\n"
            "description: Fix lint failures with the local formatter and tests.\n"
            "---\n\n"
            "# Lint Fix\n\n"
            "1. Run `python -m compileall` after edits.\n"
            "2. Prefer the provided script when available.\n",
            encoding="utf-8",
        )
        scripts_dir = lint_skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "fix.py").write_text(
            "print('lint fixed')\n",
            encoding="utf-8",
        )
        deep_skill_dir = repo_skills / "deep-docs"
        deep_skill_dir.mkdir()
        (deep_skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: deep-docs\n"
            "description: Read extra design references before changing documentation.\n"
            "---\n\n"
            "# Deep Docs\n\nRead references selectively.\n",
            encoding="utf-8",
        )
        (deep_skill_dir / "agents").mkdir()
        (deep_skill_dir / "agents" / "openai.yaml").write_text(
            "policy:\n  allow_implicit_invocation: false\n",
            encoding="utf-8",
        )
        user_skills = root / "user-skills"
        review_skill_dir = user_skills / "review"
        review_skill_dir.mkdir(parents=True)
        (review_skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: review\n"
            "description: Review code for regressions and missing tests.\n"
            "---\n\n"
            "# Review\n\nList findings before summary.\n",
            encoding="utf-8",
        )
        skills = SkillLoader().load(
            (
                SkillRoot(repo_skills, SkillScope.REPO),
                SkillRoot(user_skills, SkillScope.USER),
            )
        )
        available_skills = SkillCatalogRenderer(metadata_budget_chars=360).render(skills)
        assert available_skills is not None
        print("available skills catalog:")
        for line in available_skills.root_lines + available_skills.skill_lines:
            print(line)
        selected_skills = SkillMentionResolver().resolve(
            "Please use $lint-fix before editing.",
            skills,
        )
        injected = SkillInjector().build_injections(selected_skills)
        print("explicit skill injections:")
        for fragment in injected:
            print(f"- {fragment.skill.name}: {len(fragment.contents)} chars")
        implicit = ImplicitSkillInvocationDetector().detect(
            skills,
            ["python3", str(scripts_dir / "fix.py")],
            workdir=root,
        )
        tracker = SkillInvocationTracker()
        if implicit and tracker.record(implicit):
            print(f"implicit skill invocation: {implicit.skill.name} ({implicit.reason})")
        loaded_instructions = AgentsMdLoader().load(
            nested,
            user_instructions="Keep explanations concise.",
            user_source=root / "user" / "AGENTS.md",
        )
        assert loaded_instructions is not None
        print("AGENTS.md sources:")
        for source in loaded_instructions.sources():
            print(f"- {source}")
        print("model-visible project instructions:")
        print(loaded_instructions.render())
        snapshot = ContextSnapshot(
            cwd=nested,
            shell="zsh",
            current_date="2026-06-15",
            timezone="America/Los_Angeles",
            permission_profile="read-only",
            approval_policy="on-request",
            exec_policy_summary="prompt for writes; deny dangerous commands",
            model="gpt-5",
            model_instructions="Use concise coding-agent reasoning.",
            collaboration_instructions="Explain context changes before editing.",
            remaining_tokens=12000,
            workspace_roots=(root,),
        )
        history = ContextHistory()
        initial_context = history.record_context_for_turn(
            snapshot,
            developer_instructions="Use Python 3.11 for teaching examples.",
            project_instructions=loaded_instructions,
            available_skills=available_skills,
        )
        print("initial context messages:")
        for message in initial_context:
            print(f"- {message.role}: {len(message.sections)} sections")
        changed_snapshot = ContextSnapshot(
            cwd=root,
            shell="zsh",
            current_date="2026-06-16",
            timezone="America/Los_Angeles",
            permission_profile="workspace-write",
            approval_policy="on-request",
            exec_policy_summary="prompt for writes; deny dangerous commands",
            model="gpt-5",
            model_instructions="Use concise coding-agent reasoning.",
            collaboration_instructions="Explain context changes before editing.",
            remaining_tokens=9000,
            workspace_roots=(root,),
        )
        diff_context = history.record_context_for_turn(changed_snapshot)
        print("context update messages:")
        for message in diff_context:
            print(f"- {message.role}: {message.text().splitlines()[0]}")

        decisions: list[ApprovalRequest] = []

        def approve_for_session(request: ApprovalRequest) -> ReviewDecision:
            decisions.append(request)
            print(f"approval prompt: {request.summary}")
            return ReviewDecision.APPROVED_FOR_SESSION

        def consume(event: Event) -> None:
            render(event)

        layers = [
            ConfigLayer(
                "system",
                ConfigSource.SYSTEM,
                {"model": "gpt-default", "features": {"shell": False}},
            ),
            ConfigLayer(
                "user",
                ConfigSource.USER,
                {"default_permissions": ":workspace", "features": {"shell": True}},
            ),
            ConfigLayer(
                "project",
                ConfigSource.PROJECT,
                {
                    "default_permissions": ":danger-full-access",
                    "model_provider": "project-controlled-provider",
                },
            ),
            ConfigLayer(
                "session",
                ConfigSource.SESSION,
                {"model": "gpt-session"},
            ),
        ]
        stack = ConfigLayerStack(
            layers,
            trust_level=TrustLevel.TRUSTED,
            requirements=ConfigRequirements(
                allowed_permission_profiles=(":read-only", ":workspace"),
                default_permission_profile=":read-only",
            ),
        )
        config = stack.resolve(root)
        profile = config.permission_profile
        hooks = HookEngine(
            [
                HookRegistration(
                    "rewrite-status",
                    HookEventName.PRE_TOOL_USE,
                    lambda context: HookResult(
                        updated_arguments={"command": ["echo", "status checked by hook"]}
                    )
                    if context.arguments.get("command") == ["git", "status"]
                    else HookResult(),
                    tool_name="exec_command",
                ),
                HookRegistration(
                    "audit-exec",
                    HookEventName.POST_TOOL_USE,
                    lambda context: HookResult(
                        feedback=f"post hook audited: {context.output}"
                    ),
                    tool_name="exec_command",
                ),
            ],
            event_sink=consume,
        )
        exec_policy = ExecPolicy(
            [
                PrefixRule(("echo",), PolicyDecision.ALLOW),
                PrefixRule(("git",), PolicyDecision.PROMPT),
                PrefixRule(
                    ("rm",),
                    PolicyDecision.FORBIDDEN,
                    "Use a reviewed cleanup tool.",
                ),
            ]
        )
        router = default_router(
            Workspace(root, profile),
            decider=approve_for_session,
            event_sink=consume,
            hooks=hooks,
            exec_policy=exec_policy,
        )
        print(f"active permission profile: {config.active_permission_profile}")
        print(f"approval policy: {config.approval_policy}")
        print(f"model origin: {config.origins['model']}")
        for warning in config.warnings:
            print(f"config warning: {warning}")
        print(
            "model-visible tools:",
            ", ".join(spec.name for spec in router.model_visible_specs()),
        )
        rewritten = router.dispatch(
            FunctionCall(
                "demo-item-1",
                "demo-call-1",
                "exec_command",
                {"command": ["git", "status"]},
            ),
            turn_id="demo-turn",
        )
        forbidden = router.dispatch(
            FunctionCall(
                "demo-item-2",
                "demo-call-2",
                "exec_command",
                {"command": ["rm", "-rf", "build"]},
            ),
            turn_id="demo-turn",
        )
        print(f"rewritten command result: {rewritten.output}")
        print(f"forbidden command result: {forbidden.output}")
        model = ScriptedStreamingModel(ids)
        result = Thread(model, router, event_sink=consume, ids=ids).run_turn(args.prompt)
        view = reducer.turns[result.id]
        print(f"assistant: {view.final_response}")
        print(
            "greeting.txt:",
            (root / "greeting.txt").read_text(encoding="utf-8").strip(),
        )
        print(f"completed items: {len(view.completed)}")
        print(f"sampling requests: {model.sample_count}")
        print(f"approval prompts: {len(decisions)}")
        print(f"sandbox denials: {len(view.sandbox_denials)}")
        print(f"demo hook events: {len(reducer.turns['demo-turn'].hook_runs)}")


if __name__ == "__main__":
    main()
