"""s04: keep long-running shell commands as pollable process sessions.

The model is scripted so the example runs offline. It starts a real subprocess,
polls its session, and keeps each response bounded.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from itertools import count
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
    """Deterministic model that starts a command, polls it, then answers."""

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
            if "exec_command" not in self.seen_tool_names:
                raise RuntimeError("model cannot discover exec_command")
            call = ResponseFunctionCall(
                id=self.ids.new("item"),
                call_id=self.ids.new("call"),
                name="exec_command",
                arguments={
                    "cmd": "printf 'started\\n'; sleep 0.05; printf 'finished\\n'",
                    "yield_time_ms": 5,
                    "max_output_chars": 120,
                },
            )
            yield OutputItemAdded(call)
            yield OutputItemDone(call)
            yield ResponseCompleted()
            return

        if isinstance(latest, FunctionCallOutput):
            result = json.loads(latest.output)
            if result.get("session_id") is not None:
                call = ResponseFunctionCall(
                    id=self.ids.new("item"),
                    call_id=self.ids.new("call"),
                    name="write_stdin",
                    arguments={
                        "session_id": result["session_id"],
                        "chars": "",
                        "yield_time_ms": 200,
                        "max_output_chars": 120,
                    },
                )
                yield OutputItemAdded(call)
                yield OutputItemDone(call)
                yield ResponseCompleted()
                return

            text = (
                f"Command exited with code {result['exit_code']}. "
                f"Observed output: {result['output'].strip()}"
            )
            message_id = self.ids.new("item")
            yield OutputItemAdded(
                ResponseMessage(id=message_id, role="assistant", text="")
            )
            for delta in (
                f"Command exited with code {result['exit_code']}. ",
                "Observed output: ",
                result["output"].strip(),
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
class ExecResult:
    output: str
    session_id: int | None
    exit_code: int | None
    original_chars: int
    omitted_chars: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "output": self.output,
                "session_id": self.session_id,
                "exit_code": self.exit_code,
                "original_chars": self.original_chars,
                "omitted_chars": self.omitted_chars,
            },
            sort_keys=True,
        )


class HeadTailBuffer:
    """Retain a stable prefix and suffix while dropping the middle."""

    def __init__(self, max_chars: int) -> None:
        if max_chars < 0:
            raise ValueError("max_chars must be non-negative")
        self.max_chars = max_chars
        self.head_budget = max_chars // 2
        self.tail_budget = max_chars - self.head_budget
        self.head = ""
        self.tail = ""
        self.total_chars = 0

    def push(self, chunk: str) -> None:
        self.total_chars += len(chunk)
        if len(self.head) < self.head_budget:
            take = min(self.head_budget - len(self.head), len(chunk))
            self.head += chunk[:take]
            chunk = chunk[take:]
        if self.tail_budget:
            self.tail = (self.tail + chunk)[-self.tail_budget :]

    def render(self, max_chars: int | None = None) -> tuple[str, int]:
        budget = self.max_chars if max_chars is None else min(self.max_chars, max_chars)
        if budget < 0:
            raise ValueError("max_chars must be non-negative")
        head_budget = budget // 2
        tail_budget = budget - head_budget
        retained = self.head + self.tail
        if self.total_chars <= budget and len(retained) == self.total_chars:
            return retained, 0
        head = retained[:head_budget]
        tail = retained[-tail_budget:] if tail_budget else ""
        omitted = self.total_chars - len(head) - len(tail)
        if omitted <= 0:
            return head + tail, 0
        marker = f"\n... {omitted} chars omitted ...\n"
        return head + marker + tail, omitted

    def drain(self, max_chars: int | None = None) -> tuple[str, int, int]:
        rendered, omitted = self.render(max_chars)
        original_chars = self.total_chars
        self.head = ""
        self.tail = ""
        self.total_chars = 0
        return rendered, omitted, original_chars


@dataclass
class ManagedProcess:
    process: subprocess.Popen[bytes]
    pending: HeadTailBuffer
    pending_lock: threading.Lock
    reader_done: threading.Event


class ProcessManager:
    """Own subprocess identity, recent output, stdin, and exit cleanup."""

    def __init__(self, *, transcript_chars: int = 4096) -> None:
        self.transcript_chars = transcript_chars
        self._next_session_id = count(1000)
        self._sessions: dict[int, ManagedProcess] = {}

    def exec_command(
        self, cmd: str, *, yield_time_ms: int, max_output_chars: int
    ) -> ExecResult:
        self._validate_output_budget(max_output_chars)
        process = subprocess.Popen(
            cmd,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        managed = ManagedProcess(
            process=process,
            pending=HeadTailBuffer(self.transcript_chars),
            pending_lock=threading.Lock(),
            reader_done=threading.Event(),
        )
        threading.Thread(
            target=self._read_output, args=(managed,), daemon=True
        ).start()
        session_id = next(self._next_session_id)
        self._sessions[session_id] = managed
        return self._collect(session_id, yield_time_ms, max_output_chars)

    def write_stdin(
        self,
        session_id: int,
        chars: str,
        *,
        yield_time_ms: int,
        max_output_chars: int,
    ) -> ExecResult:
        self._validate_output_budget(max_output_chars)
        managed = self._sessions.get(session_id)
        if managed is None:
            raise ToolError(f"unknown process session {session_id}")
        if chars:
            if managed.process.stdin is None or managed.process.poll() is not None:
                raise ToolError(f"stdin is closed for process session {session_id}")
            try:
                managed.process.stdin.write(chars.encode())
                managed.process.stdin.flush()
            except BrokenPipeError as error:
                raise ToolError(f"stdin is closed for process session {session_id}") from error
        return self._collect(session_id, yield_time_ms, max_output_chars)

    def has_session(self, session_id: int) -> bool:
        return session_id in self._sessions

    def _read_output(self, managed: ManagedProcess) -> None:
        assert managed.process.stdout is not None
        while True:
            chunk = os.read(managed.process.stdout.fileno(), 1024)
            if not chunk:
                break
            with managed.pending_lock:
                managed.pending.push(chunk.decode(errors="replace"))
        managed.reader_done.set()

    def _collect(
        self, session_id: int, yield_time_ms: int, max_output_chars: int
    ) -> ExecResult:
        managed = self._sessions[session_id]
        deadline = time.monotonic() + max(0, yield_time_ms) / 1000

        while True:
            exited = managed.process.poll() is not None
            if exited and managed.reader_done.is_set():
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.005)

        with managed.pending_lock:
            output, omitted, pending_original_chars = managed.pending.drain(max_output_chars)
        exit_code = managed.process.poll()
        if exit_code is None:
            response_session_id: int | None = session_id
        else:
            response_session_id = None
            self._sessions.pop(session_id, None)
            self._close_process_handles(managed)
        return ExecResult(
            output=output,
            session_id=response_session_id,
            exit_code=exit_code,
            original_chars=pending_original_chars,
            omitted_chars=omitted,
        )

    @staticmethod
    def _close_process_handles(managed: ManagedProcess) -> None:
        managed.process.wait()
        if managed.process.stdin is not None:
            managed.process.stdin.close()
        if managed.process.stdout is not None:
            managed.process.stdout.close()

    @staticmethod
    def _validate_output_budget(max_output_chars: int) -> None:
        if max_output_chars < 0:
            raise ToolError("max_output_chars must be non-negative")


class ExecCommandHandler:
    spec = ToolSpec(
        name="exec_command",
        description="Run a shell command, returning output or a session ID.",
        properties={"cmd": str, "yield_time_ms": int, "max_output_chars": int},
        required=("cmd",),
    )

    def __init__(self, manager: ProcessManager) -> None:
        self.manager = manager

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        return self.manager.exec_command(
            str(arguments["cmd"]),
            yield_time_ms=int(arguments.get("yield_time_ms", 50)),
            max_output_chars=int(arguments.get("max_output_chars", 500)),
        ).to_json()


class WriteStdinHandler:
    spec = ToolSpec(
        name="write_stdin",
        description="Write to or poll an existing process session.",
        properties={
            "session_id": int,
            "chars": str,
            "yield_time_ms": int,
            "max_output_chars": int,
        },
        required=("session_id",),
    )

    def __init__(self, manager: ProcessManager) -> None:
        self.manager = manager

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        return self.manager.write_stdin(
            int(arguments["session_id"]),
            str(arguments.get("chars", "")),
            yield_time_ms=int(arguments.get("yield_time_ms", 50)),
            max_output_chars=int(arguments.get("max_output_chars", 500)),
        ).to_json()


def default_router(manager: ProcessManager | None = None) -> ToolRouter:
    manager = manager or ProcessManager()
    return ToolRouter(
        ToolRegistry([ExecCommandHandler(manager), WriteStdinHandler(manager)])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Run a command and keep its process state",
    )
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

    router = default_router()
    print("model-visible tools:", ", ".join(spec.name for spec in router.model_visible_specs()))
    model = ScriptedStreamingModel(ids)
    result = Thread(model, router, event_sink=render, ids=ids).run_turn(args.prompt)
    view = reducer.turns[result.id]
    print(f"assistant: {view.final_response}")
    print(f"completed items: {len(view.completed)}")
    print(f"sampling requests: {model.sample_count}")


if __name__ == "__main__":
    main()
