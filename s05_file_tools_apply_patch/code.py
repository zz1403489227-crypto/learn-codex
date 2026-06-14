"""s05: read workspace files and apply structured, verified patches.

The model is scripted so the example runs offline. It reads a file, proposes a
freeform patch, and receives a structured summary of the committed changes.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
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


@dataclass
class TurnView:
    status: str = "not_started"
    in_progress: dict[str, TurnItem] = field(default_factory=dict)
    completed: list[TurnItem] = field(default_factory=list)
    text_buffers: dict[str, str] = field(default_factory=dict)
    final_response: str | None = None


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
    """Deterministic model that reads a file, patches it, then answers."""

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
                        result = self.router.dispatch(item)
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


class ToolError(ValueError):
    pass


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

    def dispatch(self, name: str, arguments: Mapping[str, JsonType]) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool {name!r}")
        validated = validate_arguments(handler.spec, arguments)
        return handler.handle(validated)


class ToolRouter:
    """Expose specs to the model and route calls back to the registry."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def model_visible_specs(self) -> tuple[ToolSpec, ...]:
        return self.registry.specs()

    def dispatch(self, call: FunctionCall) -> ToolResult:
        try:
            output = self.registry.dispatch(call.name, call.arguments)
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


class Workspace:
    """Resolve every file operation beneath one explicit workspace root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

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
        staged = self._verify(plan)
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

    def _verify(
        self, plan: PatchPlan
    ) -> list[tuple[PatchChange, Path, str | None]]:
        """Validate the complete plan before committing any filesystem change."""
        staged: list[tuple[PatchChange, Path, str | None]] = []
        seen: set[Path] = set()
        for change in plan.changes:
            path = self.resolve(change.path)
            if path in seen:
                raise ToolError(f"patch targets {change.path!r} more than once")
            seen.add(path)

            if change.operation == "add":
                if path.exists():
                    raise ToolError(f"add target already exists: {change.path!r}")
                staged.append((change, path, change.new_text))
                continue

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


def default_router(workspace: Workspace) -> ToolRouter:
    return ToolRouter(
        ToolRegistry([ReadFileHandler(workspace), ApplyPatchHandler(workspace)])
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
        router = default_router(Workspace(root))
        print(
            "model-visible tools:",
            ", ".join(spec.name for spec in router.model_visible_specs()),
        )
        model = ScriptedStreamingModel(ids)
        result = Thread(model, router, event_sink=render, ids=ids).run_turn(args.prompt)
        view = reducer.turns[result.id]
        print(f"assistant: {view.final_response}")
        print(
            "greeting.txt:",
            (root / "greeting.txt").read_text(encoding="utf-8").strip(),
        )
        print(f"completed items: {len(view.completed)}")
        print(f"sampling requests: {model.sample_count}")


if __name__ == "__main__":
    main()
