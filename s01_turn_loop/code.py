"""s01: a minimal Codex-style thread and turn loop.

The model is scripted so the example runs offline. Replace ScriptedModel with a
real model adapter later; the turn loop should not need to know the provider.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Callable, Protocol, Sequence, TypeAlias


@dataclass(frozen=True)
class UserMessage:
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


@dataclass(frozen=True)
class AgentMessage:
    id: str
    text: str


Item: TypeAlias = UserMessage | FunctionCall | FunctionCallOutput | AgentMessage


@dataclass(frozen=True)
class Event:
    method: str
    turn_id: str
    item: Item | None = None
    final_response: str | None = None


@dataclass(frozen=True)
class TurnResult:
    id: str
    final_response: str
    items: tuple[Item, ...]


class Model(Protocol):
    def sample(self, history: Sequence[Item]) -> list[Item]:
        """Return the next completed model output items."""


@dataclass
class IdGenerator:
    _counter: count = field(default_factory=lambda: count(1))

    def new(self, prefix: str) -> str:
        return f"{prefix}_{next(self._counter)}"


class ScriptedModel:
    """Deterministic stand-in for a model that knows how to call one tool."""

    def __init__(self, ids: IdGenerator) -> None:
        self.ids = ids
        self.sample_count = 0

    def sample(self, history: Sequence[Item]) -> list[Item]:
        self.sample_count += 1
        latest = history[-1]

        if isinstance(latest, UserMessage):
            return [
                FunctionCall(
                    id=self.ids.new("item"),
                    call_id=self.ids.new("call"),
                    name="count_words",
                    arguments={"text": latest.text},
                )
            ]

        if isinstance(latest, FunctionCallOutput):
            return [
                AgentMessage(
                    id=self.ids.new("item"),
                    text=f"The text contains {latest.output} words.",
                )
            ]

        raise RuntimeError(f"ScriptedModel cannot continue from {type(latest).__name__}")


class ImmediateAnswerModel:
    """Small test/demo model that completes without calling a tool."""

    def __init__(self, ids: IdGenerator, answer: str) -> None:
        self.ids = ids
        self.answer = answer
        self.sample_count = 0

    def sample(self, history: Sequence[Item]) -> list[Item]:
        self.sample_count += 1
        return [AgentMessage(id=self.ids.new("item"), text=self.answer)]


def execute_tool(call: FunctionCall, ids: IdGenerator) -> FunctionCallOutput:
    """Execute the only tool available in s01.

    Tool registration, validation, approvals, and sandboxing arrive later.
    """
    if call.name != "count_words":
        result = f"Error: unknown tool {call.name!r}"
    else:
        text = str(call.arguments.get("text", ""))
        result = str(len(text.split()))

    return FunctionCallOutput(
        id=ids.new("item"),
        call_id=call.call_id,
        output=result,
    )


EventSink: TypeAlias = Callable[[Event], None]


class Thread:
    def __init__(
        self,
        model: Model,
        *,
        event_sink: EventSink | None = None,
        ids: IdGenerator | None = None,
        max_sampling_rounds: int = 8,
    ) -> None:
        self.model = model
        self.event_sink = event_sink or (lambda _event: None)
        self.ids = ids or IdGenerator()
        self.max_sampling_rounds = max_sampling_rounds
        self.history: list[Item] = []

    def _emit(self, event: Event) -> None:
        self.event_sink(event)

    def _append_item(self, turn_id: str, item: Item, turn_items: list[Item]) -> None:
        self.history.append(item)
        turn_items.append(item)
        self._emit(Event(method="item/completed", turn_id=turn_id, item=item))

    def run_turn(self, user_text: str) -> TurnResult:
        turn_id = self.ids.new("turn")
        turn_items: list[Item] = []
        self._emit(Event(method="turn/started", turn_id=turn_id))

        user_message = UserMessage(id=self.ids.new("item"), text=user_text)
        self._append_item(turn_id, user_message, turn_items)

        for _ in range(self.max_sampling_rounds):
            sampled_items = self.model.sample(tuple(self.history))
            if not sampled_items:
                raise RuntimeError("Model returned no items")

            needs_follow_up = False
            last_agent_message: str | None = None

            for item in sampled_items:
                self._append_item(turn_id, item, turn_items)

                if isinstance(item, FunctionCall):
                    tool_output = execute_tool(item, self.ids)
                    self._append_item(turn_id, tool_output, turn_items)
                    needs_follow_up = True
                elif isinstance(item, AgentMessage):
                    last_agent_message = item.text

            if needs_follow_up:
                continue

            if last_agent_message is None:
                raise RuntimeError("Turn stopped without a final agent message")

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

        raise RuntimeError("Turn exceeded max_sampling_rounds")


def print_event(event: Event) -> None:
    if event.item is None:
        print(event.method)
    else:
        print(f"{event.method}: {type(event.item).__name__}")
        if isinstance(event.item, FunctionCall):
            print(f"tool> {event.item.name}({json.dumps(event.item.arguments)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Codex turns model requests into agent actions",
    )
    args = parser.parse_args()

    ids = IdGenerator()
    model = ScriptedModel(ids)
    thread = Thread(model, event_sink=print_event, ids=ids)
    result = thread.run_turn(args.prompt)

    print(f"\nassistant> {result.final_response}")
    print(f"sampling requests: {model.sample_count}")
    print(f"thread items: {len(thread.history)}")


if __name__ == "__main__":
    main()
