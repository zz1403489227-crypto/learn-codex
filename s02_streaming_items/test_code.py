import unittest

from code import (
    AgentMessage,
    Event,
    EventReducer,
    FunctionCall,
    IdGenerator,
    ScriptedStreamingModel,
    Thread,
)


class EventReducerTests(unittest.TestCase):
    def test_reconstructs_live_text_then_accepts_completed_item(self) -> None:
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
                delta="hel",
            )
        )
        live = reducer.apply(
            Event(
                method="item/agentMessage/delta",
                turn_id=turn_id,
                item_id="item_1",
                delta="lo",
            )
        )
        self.assertEqual(live.text_buffers["item_1"], "hello")

        completed = AgentMessage(id="item_1", text="hello!")
        final = reducer.apply(
            Event(method="item/completed", turn_id=turn_id, item=completed)
        )
        self.assertNotIn("item_1", final.in_progress)
        self.assertEqual(final.text_buffers["item_1"], "hello!")
        self.assertEqual(final.completed, [completed])

    def test_routes_interleaved_events_by_turn_id(self) -> None:
        reducer = EventReducer()
        reducer.apply(Event(method="turn/started", turn_id="turn_1"))
        reducer.apply(Event(method="turn/started", turn_id="turn_2"))
        reducer.apply(
            Event(
                method="item/started",
                turn_id="turn_2",
                item=AgentMessage(id="item_2", text=""),
            )
        )
        reducer.apply(
            Event(
                method="item/started",
                turn_id="turn_1",
                item=AgentMessage(id="item_1", text=""),
            )
        )
        reducer.apply(
            Event(
                method="item/agentMessage/delta",
                turn_id="turn_2",
                item_id="item_2",
                delta="two",
            )
        )
        reducer.apply(
            Event(
                method="item/agentMessage/delta",
                turn_id="turn_1",
                item_id="item_1",
                delta="one",
            )
        )

        self.assertEqual(reducer.turns["turn_1"].text_buffers["item_1"], "one")
        self.assertEqual(reducer.turns["turn_2"].text_buffers["item_2"], "two")

    def test_rejects_delta_for_unknown_item(self) -> None:
        reducer = EventReducer()
        with self.assertRaisesRegex(ValueError, "unknown in-progress item"):
            reducer.apply(
                Event(
                    method="item/agentMessage/delta",
                    turn_id="turn_1",
                    item_id="missing",
                    delta="oops",
                )
            )

    def test_rejects_agent_delta_for_non_agent_item(self) -> None:
        reducer = EventReducer()
        call = FunctionCall(
            id="item_1", call_id="call_1", name="count_words", arguments={}
        )
        reducer.apply(Event(method="item/started", turn_id="turn_1", item=call))
        with self.assertRaisesRegex(ValueError, "non-agent item"):
            reducer.apply(
                Event(
                    method="item/agentMessage/delta",
                    turn_id="turn_1",
                    item_id="item_1",
                    delta="oops",
                )
            )


class ThreadStreamingTests(unittest.TestCase):
    def test_full_turn_emits_lifecycle_and_reduces_to_completed_state(self) -> None:
        ids = IdGenerator()
        reducer = EventReducer()
        events: list[Event] = []

        def consume(event: Event) -> None:
            events.append(event)
            reducer.apply(event)

        model = ScriptedStreamingModel(ids)
        result = Thread(model, ids=ids, event_sink=consume).run_turn(
            "structured events need stable ids"
        )
        view = reducer.turns[result.id]

        self.assertEqual(model.sample_count, 2)
        self.assertEqual(view.status, "completed")
        self.assertEqual(view.final_response, "The text contains 5 words.")
        self.assertEqual(view.in_progress, {})
        self.assertEqual(
            [type(item).__name__ for item in view.completed],
            ["UserMessage", "FunctionCall", "FunctionCallOutput", "AgentMessage"],
        )
        self.assertEqual(
            "".join(event.delta or "" for event in events if event.delta),
            "The text contains 5 words.",
        )
        self.assertEqual(events[0].method, "turn/started")
        self.assertEqual(events[-1].method, "turn/completed")


if __name__ == "__main__":
    unittest.main()
