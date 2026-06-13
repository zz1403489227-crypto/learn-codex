from __future__ import annotations

import unittest

from s01_turn_loop.code import (
    AgentMessage,
    Event,
    FunctionCall,
    FunctionCallOutput,
    IdGenerator,
    ImmediateAnswerModel,
    ScriptedModel,
    Thread,
    UserMessage,
)


class TurnLoopTests(unittest.TestCase):
    def test_tool_call_causes_follow_up_sampling_and_paired_output(self) -> None:
        events: list[Event] = []
        ids = IdGenerator()
        model = ScriptedModel(ids)
        thread = Thread(model, event_sink=events.append, ids=ids)

        result = thread.run_turn("one two three")

        self.assertEqual(model.sample_count, 2)
        self.assertEqual(result.final_response, "The text contains 3 words.")
        self.assertEqual(
            [event.method for event in events],
            [
                "turn/started",
                "item/completed",
                "item/completed",
                "item/completed",
                "item/completed",
                "turn/completed",
            ],
        )
        self.assertEqual(
            [type(item) for item in result.items],
            [UserMessage, FunctionCall, FunctionCallOutput, AgentMessage],
        )
        call = result.items[1]
        output = result.items[2]
        self.assertIsInstance(call, FunctionCall)
        self.assertIsInstance(output, FunctionCallOutput)
        self.assertEqual(call.call_id, output.call_id)

    def test_two_turns_share_thread_history(self) -> None:
        ids = IdGenerator()
        model = ScriptedModel(ids)
        thread = Thread(model, ids=ids)

        first = thread.run_turn("one two")
        second = thread.run_turn("one two three four")

        self.assertEqual(first.final_response, "The text contains 2 words.")
        self.assertEqual(second.final_response, "The text contains 4 words.")
        self.assertEqual(len(thread.history), 8)
        self.assertEqual(len({item.id for item in thread.history}), 8)

    def test_turn_can_complete_without_a_tool_call(self) -> None:
        events: list[Event] = []
        ids = IdGenerator()
        model = ImmediateAnswerModel(ids, "Already done.")
        thread = Thread(model, event_sink=events.append, ids=ids)

        result = thread.run_turn("Answer directly")

        self.assertEqual(model.sample_count, 1)
        self.assertEqual(result.final_response, "Already done.")
        self.assertEqual([type(item) for item in result.items], [UserMessage, AgentMessage])
        self.assertEqual(events[-1].method, "turn/completed")

    def test_sampling_round_limit_stops_a_broken_model(self) -> None:
        ids = IdGenerator()

        class EndlessToolModel:
            def sample(self, history):
                return [
                    FunctionCall(
                        id=ids.new("item"),
                        call_id=ids.new("call"),
                        name="count_words",
                        arguments={"text": "still looping"},
                    )
                ]

        thread = Thread(EndlessToolModel(), ids=ids, max_sampling_rounds=2)

        with self.assertRaisesRegex(RuntimeError, "max_sampling_rounds"):
            thread.run_turn("start")


if __name__ == "__main__":
    unittest.main()

