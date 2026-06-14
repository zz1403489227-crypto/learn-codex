"""s03: expose, validate, and route tools through a registry.

The model is scripted so the example runs offline. The Item/Event system from
s02 stays intact while hard-coded tool execution becomes an explicit runtime.
"""

from __future__ import annotations

import argparse
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
    """Deterministic model that streams a tool call, then an answer."""

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
            if "count_words" not in self.seen_tool_names:
                raise RuntimeError("model cannot discover count_words")
            call = ResponseFunctionCall(
                id=self.ids.new("item"),
                call_id=self.ids.new("call"),
                name="count_words",
                arguments={"text": latest.text},
            )
            yield OutputItemAdded(call)
            yield OutputItemDone(call)
            yield ResponseCompleted()
            return

        if isinstance(latest, FunctionCallOutput):
            text = f"The text contains {latest.output} words."
            message_id = self.ids.new("item")
            yield OutputItemAdded(
                ResponseMessage(id=message_id, role="assistant", text="")
            )
            for delta in ("The text ", f"contains {latest.output} ", "words."):
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


class CountWordsHandler:
    spec = ToolSpec(
        name="count_words",
        description="Count whitespace-separated words in text.",
        properties={"text": str},
        required=("text",),
    )

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        return str(len(str(arguments["text"]).split()))


class RepeatTextHandler:
    spec = ToolSpec(
        name="repeat_text",
        description="Repeat text a requested number of times.",
        properties={"text": str, "times": int},
        required=("text", "times"),
    )

    def handle(self, arguments: Mapping[str, JsonType]) -> str:
        return str(arguments["text"]) * int(arguments["times"])


def default_router() -> ToolRouter:
    return ToolRouter(ToolRegistry([CountWordsHandler(), RepeatTextHandler()]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Codex routes calls through registered tools",
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
