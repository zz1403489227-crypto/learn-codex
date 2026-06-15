"""s08: resolve layered config and project trust before building the runtime.

The model is scripted so the example runs offline. Config layers select the
active permission profile, then the inherited sandboxed tool runtime executes.
"""

from __future__ import annotations

import argparse
import copy
import json
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
    ) -> None:
        self.decider = decider
        self.store = store or ApprovalStore()
        self.event_sink = event_sink or (lambda _event: None)

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
    ) -> None:
        self.registry = registry
        self.orchestrator = orchestrator

    def model_visible_specs(self) -> tuple[ToolSpec, ...]:
        return self.registry.specs()

    def dispatch(self, call: FunctionCall, *, turn_id: str = "turn") -> ToolResult:
        try:
            handler, arguments = self.registry.resolve(call.name, call.arguments)
            output = self.orchestrator.run(
                handler, call.call_id, arguments, turn_id=turn_id
            )
        except ApprovalAborted:
            raise
        except ToolError as error:
            return ToolResult(output=f"Error: {error}", success=False)
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


def default_router(
    workspace: Workspace,
    *,
    decider: ApprovalDecider = lambda _request: ReviewDecision.APPROVED,
    store: ApprovalStore | None = None,
    event_sink: EventSink | None = None,
) -> ToolRouter:
    return ToolRouter(
        ToolRegistry(
            [
                ReadFileHandler(workspace),
                ApplyPatchHandler(workspace),
                NetworkProbeHandler(workspace.sandbox),
            ]
        ),
        ApprovalOrchestrator(decider, store=store, event_sink=event_sink),
    )


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
            suffix = f" {type(event.item).__name__}" if event.item else ""
            print(f"{event.method}{suffix}")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "greeting.txt").write_text("hello\n", encoding="utf-8")
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
        router = default_router(
            Workspace(root, profile),
            decider=approve_for_session,
            event_sink=consume,
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


if __name__ == "__main__":
    main()
