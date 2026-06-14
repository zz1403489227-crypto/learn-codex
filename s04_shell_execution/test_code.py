import json
import unittest

from code import (
    Event,
    EventReducer,
    FunctionCall,
    HeadTailBuffer,
    IdGenerator,
    ProcessManager,
    ScriptedStreamingModel,
    Thread,
    ToolError,
    default_router,
)


class HeadTailBufferTests(unittest.TestCase):
    def test_preserves_prefix_and_suffix_when_output_exceeds_budget(self) -> None:
        buffer = HeadTailBuffer(10)
        buffer.push("0123456789AB")

        rendered, omitted = buffer.render()

        self.assertTrue(rendered.startswith("01234"))
        self.assertTrue(rendered.endswith("89AB"))
        self.assertIn("2 chars omitted", rendered)
        self.assertEqual(omitted, 2)

    def test_zero_budget_drops_all_content(self) -> None:
        buffer = HeadTailBuffer(0)
        buffer.push("abc")

        rendered, omitted = buffer.render()

        self.assertNotIn("abc", rendered)
        self.assertEqual(omitted, 3)


class ProcessManagerTests(unittest.TestCase):
    def test_completed_command_returns_exit_code_without_session(self) -> None:
        result = ProcessManager().exec_command(
            "printf 'done\\n'", yield_time_ms=200, max_output_chars=100
        )

        self.assertEqual(result.output, "done\n")
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)

    def test_long_command_returns_session_then_poll_returns_exit_and_clears_it(self) -> None:
        manager = ProcessManager()
        started = manager.exec_command(
            "printf 'start\\n'; sleep 0.05; printf 'finish\\n'",
            yield_time_ms=5,
            max_output_chars=100,
        )

        self.assertIsNotNone(started.session_id)
        self.assertIsNone(started.exit_code)
        session_id = started.session_id
        assert session_id is not None

        finished = manager.write_stdin(
            session_id, "", yield_time_ms=200, max_output_chars=100
        )

        self.assertIn("finish", finished.output)
        self.assertEqual(finished.exit_code, 0)
        self.assertIsNone(finished.session_id)
        self.assertFalse(manager.has_session(session_id))

    def test_write_stdin_continues_interactive_process(self) -> None:
        manager = ProcessManager()
        started = manager.exec_command(
            "read line; printf 'got:%s\\n' \"$line\"",
            yield_time_ms=5,
            max_output_chars=100,
        )
        session_id = started.session_id
        assert session_id is not None

        finished = manager.write_stdin(
            session_id, "hello\n", yield_time_ms=200, max_output_chars=100
        )

        self.assertEqual(finished.output, "got:hello\n")
        self.assertEqual(finished.exit_code, 0)

    def test_each_response_bounds_output_and_keeps_metadata(self) -> None:
        result = ProcessManager().exec_command(
            "printf '0123456789ABCDEFGHIJ'",
            yield_time_ms=200,
            max_output_chars=10,
        )

        self.assertTrue(result.output.startswith("01234"))
        self.assertTrue(result.output.endswith("FGHIJ"))
        self.assertEqual(result.original_chars, 20)
        self.assertEqual(result.omitted_chars, 10)
        self.assertEqual(result.exit_code, 0)

    def test_unread_session_output_is_bounded_before_poll(self) -> None:
        result = ProcessManager(transcript_chars=10).exec_command(
            "printf '0123456789ABCDEFGHIJ'",
            yield_time_ms=200,
            max_output_chars=100,
        )

        self.assertTrue(result.output.startswith("01234"))
        self.assertTrue(result.output.endswith("FGHIJ"))
        self.assertEqual(result.original_chars, 20)
        self.assertEqual(result.omitted_chars, 10)

    def test_response_budget_can_be_smaller_than_session_buffer(self) -> None:
        result = ProcessManager(transcript_chars=20).exec_command(
            "printf '0123456789ABCDEFGHIJ'",
            yield_time_ms=200,
            max_output_chars=6,
        )

        self.assertTrue(result.output.startswith("012"))
        self.assertTrue(result.output.endswith("HIJ"))
        self.assertEqual(result.original_chars, 20)
        self.assertEqual(result.omitted_chars, 14)

    def test_unknown_session_is_rejected(self) -> None:
        with self.assertRaisesRegex(ToolError, "unknown process session"):
            ProcessManager().write_stdin(
                9999, "", yield_time_ms=0, max_output_chars=100
            )

    def test_negative_output_budget_is_rejected_before_spawn(self) -> None:
        with self.assertRaisesRegex(ToolError, "must be non-negative"):
            ProcessManager().exec_command(
                "printf unreachable", yield_time_ms=0, max_output_chars=-1
            )


class ThreadShellRuntimeTests(unittest.TestCase):
    def test_full_turn_starts_polls_and_completes_command(self) -> None:
        ids = IdGenerator()
        reducer = EventReducer()
        events: list[Event] = []
        model = ScriptedStreamingModel(ids)

        def consume(event: Event) -> None:
            events.append(event)
            reducer.apply(event)

        result = Thread(
            model,
            default_router(ProcessManager()),
            ids=ids,
            event_sink=consume,
        ).run_turn("demonstrate a long-running command")
        view = reducer.turns[result.id]

        self.assertEqual(model.sample_count, 3)
        self.assertEqual(model.seen_tool_names, ["exec_command", "write_stdin"])
        self.assertIn("Command exited with code 0", view.final_response or "")
        calls = [
            event.item.name
            for event in events
            if event.method == "item/completed"
            and isinstance(event.item, FunctionCall)
        ]
        self.assertEqual(calls, ["exec_command", "write_stdin"])
        outputs = [
            json.loads(item.output)
            for item in view.completed
            if type(item).__name__ == "FunctionCallOutput"
        ]
        self.assertIsNotNone(outputs[0]["session_id"])
        self.assertEqual(outputs[1]["exit_code"], 0)
        self.assertIsNone(outputs[1]["session_id"])


if __name__ == "__main__":
    unittest.main()
