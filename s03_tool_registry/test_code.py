import unittest

from code import (
    AgentMessage,
    CountWordsHandler,
    Event,
    EventReducer,
    FunctionCall,
    IdGenerator,
    RepeatTextHandler,
    ScriptedStreamingModel,
    Thread,
    ToolError,
    ToolRegistry,
    ToolRouter,
    ToolSpec,
    default_router,
)


class RecordingHandler:
    spec = ToolSpec(
        name="record",
        description="Record valid text.",
        properties={"text": str},
        required=("text",),
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def handle(self, arguments: dict[str, object]) -> str:
        self.calls.append(arguments)
        return "recorded"


class ToolRegistryTests(unittest.TestCase):
    def test_exposes_specs_and_routes_to_matching_handler(self) -> None:
        registry = ToolRegistry([CountWordsHandler(), RepeatTextHandler()])

        self.assertEqual(
            [spec.name for spec in registry.specs()],
            ["count_words", "repeat_text"],
        )
        self.assertEqual(registry.dispatch("count_words", {"text": "one two"}), "2")
        self.assertEqual(
            registry.dispatch("repeat_text", {"text": "ha", "times": 2}), "haha"
        )

    def test_rejects_duplicate_tool_names(self) -> None:
        with self.assertRaisesRegex(ToolError, "already registered"):
            ToolRegistry([CountWordsHandler(), CountWordsHandler()])

    def test_validates_before_executing_handler(self) -> None:
        handler = RecordingHandler()
        registry = ToolRegistry([handler])

        for arguments, message in [
            ({}, "missing required"),
            ({"text": 3}, "must be str"),
            ({"text": "ok", "extra": True}, "unknown argument"),
        ]:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(ToolError, message):
                    registry.dispatch("record", arguments)

        self.assertEqual(handler.calls, [])

    def test_type_validation_does_not_treat_bool_as_int(self) -> None:
        with self.assertRaisesRegex(ToolError, "must be int"):
            ToolRegistry([RepeatTextHandler()]).dispatch(
                "repeat_text", {"text": "ha", "times": True}
            )

    def test_router_returns_unknown_tool_error_to_model(self) -> None:
        result = default_router().dispatch(
            FunctionCall(
                id="item_1",
                call_id="call_1",
                name="missing",
                arguments={},
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(result.output, "Error: unknown tool 'missing'")


class EventReducerTests(unittest.TestCase):
    def test_completed_item_remains_authoritative_after_deltas(self) -> None:
        reducer = EventReducer()
        turn_id = "turn_1"
        started = AgentMessage(id="item_1", text="")

        reducer.apply(Event(method="turn/started", turn_id=turn_id))
        reducer.apply(Event(method="item/started", turn_id=turn_id, item=started))
        reducer.apply(
            Event(
                method="item/agentMessage/delta",
                turn_id=turn_id,
                item_id="item_1",
                delta="temporary",
            )
        )
        completed = AgentMessage(id="item_1", text="final")
        view = reducer.apply(
            Event(method="item/completed", turn_id=turn_id, item=completed)
        )

        self.assertEqual(view.text_buffers["item_1"], "final")
        self.assertNotIn("item_1", view.in_progress)


class ThreadToolRuntimeTests(unittest.TestCase):
    def test_full_turn_discovers_and_dispatches_registered_tool(self) -> None:
        ids = IdGenerator()
        reducer = EventReducer()
        model = ScriptedStreamingModel(ids)
        events: list[Event] = []

        def consume(event: Event) -> None:
            events.append(event)
            reducer.apply(event)

        result = Thread(
            model,
            default_router(),
            ids=ids,
            event_sink=consume,
        ).run_turn("registered tools need stable contracts")
        view = reducer.turns[result.id]

        self.assertEqual(model.sample_count, 2)
        self.assertEqual(model.seen_tool_names, ["count_words", "repeat_text"])
        self.assertEqual(view.final_response, "The text contains 5 words.")
        self.assertEqual(
            [type(item).__name__ for item in view.completed],
            ["UserMessage", "FunctionCall", "FunctionCallOutput", "AgentMessage"],
        )
        function_call_events = [
            event for event in events if isinstance(event.item, FunctionCall)
        ]
        self.assertEqual(
            [event.method for event in function_call_events],
            ["item/started", "item/completed"],
        )


if __name__ == "__main__":
    unittest.main()
