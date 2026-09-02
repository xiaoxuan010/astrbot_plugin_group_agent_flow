from types import SimpleNamespace

import pytest

from response_policy import (
    classify_run_outcome,
    install_send_guard,
    isolate_platform_metadata,
    suppress_builtin_active_reply,
    suppress_direct_output,
    tool_send,
)


def test_suppress_direct_output_removes_terminal_plain_assistant_from_history():
    response = SimpleNamespace(
        completion_text="ordinary assistant output", result_chain=object()
    )
    assistant_message = SimpleNamespace(role="assistant", _no_save=False)
    run_context = SimpleNamespace(messages=[assistant_message])

    had_direct_output = suppress_direct_output(response, run_context=run_context)

    assert had_direct_output is True
    assert response.completion_text == ""
    assert response.result_chain is None
    assert run_context.messages == []


def test_suppress_direct_output_keeps_prior_tool_call_and_result_history():
    response = SimpleNamespace(completion_text="ordinary", result_chain=object())
    tool_call_assistant = SimpleNamespace(role="assistant", tool_calls=[object()])
    tool_result = SimpleNamespace(role="tool", tool_call_id="call-1")
    terminal_assistant = SimpleNamespace(
        role="assistant", content="already sent by tool"
    )
    run_context = SimpleNamespace(
        messages=[tool_call_assistant, tool_result, terminal_assistant]
    )

    suppress_direct_output(response, run_context=run_context)

    assert run_context.messages == [tool_call_assistant, tool_result]


def test_suppress_direct_output_handles_missing_response():
    assert suppress_direct_output(None) is False


def test_suppress_direct_output_does_not_hide_previous_history_for_error_response():
    response = SimpleNamespace(
        role="err",
        completion_text="provider failed",
        result_chain=object(),
    )
    previous_assistant = SimpleNamespace(role="assistant", _no_save=False)
    run_context = SimpleNamespace(messages=[previous_assistant])

    had_direct_output = suppress_direct_output(response, run_context=run_context)

    assert had_direct_output is True
    assert response.completion_text == ""
    assert response.result_chain is None
    assert previous_assistant._no_save is False


def test_classify_run_outcome_distinguishes_silence_protocol_output_and_actions():
    assert (
        classify_run_outcome([], silence_selected=False, had_direct_output=False)
        == "no_action"
    )
    assert (
        classify_run_outcome([], silence_selected=False, had_direct_output=True)
        == "direct_output_suppressed"
    )
    assert (
        classify_run_outcome([], silence_selected=True, had_direct_output=False)
        == "silence_selected"
    )
    assert (
        classify_run_outcome(
            [{"status": "succeeded", "action_name": "react_message"}],
            silence_selected=True,
            had_direct_output=True,
        )
        == "action_succeeded"
    )
    assert (
        classify_run_outcome(
            [{"status": "failed", "action_name": "reply_message"}],
            silence_selected=False,
            had_direct_output=False,
        )
        == "action_failed"
    )


def test_isolate_platform_metadata_disables_core_proactive_tool_without_mutating_adapter():
    shared = SimpleNamespace(support_proactive_message=True, id="qq")
    event = SimpleNamespace(platform_meta=shared, platform=shared)

    isolate_platform_metadata(event)

    assert event.platform_meta is not shared
    assert event.platform_meta.support_proactive_message is False
    assert shared.support_proactive_message is True


def test_suppress_builtin_active_reply_marks_event_as_already_directed():
    event = SimpleNamespace(is_at_or_wake_command=False)

    suppress_builtin_active_reply(event)

    assert event.is_at_or_wake_command is True


@pytest.mark.asyncio
async def test_send_guard_blocks_ordinary_content_and_core_error_but_allows_tool_send():
    sent = []

    class Event:
        def __init__(self):
            self.extras = {}
            self.result = None

        def get_extra(self, key, default=None):
            return self.extras.get(key, default)

        def set_extra(self, key, value):
            self.extras[key] = value

        def get_result(self):
            return self.result

        async def send(self, message):
            sent.append(message)

    event = Event()
    install_send_guard(event)

    event.result = SimpleNamespace(is_llm_result=lambda: True)
    await event.send("model content")
    event.result = SimpleNamespace(is_llm_result=lambda: False)
    await event.send("core error")
    await tool_send(event, "tool message")

    assert sent == ["tool message"]
    assert event.get_extra("_group_agent_external_actions", []) == []


@pytest.mark.asyncio
async def test_send_guard_blocks_reasoning_even_for_llm_result():
    sent = []

    class Event:
        def __init__(self):
            self.extras = {}
            self.result = SimpleNamespace(is_llm_result=lambda: True)

        def get_extra(self, key, default=None):
            return self.extras.get(key, default)

        def set_extra(self, key, value):
            self.extras[key] = value

        def get_result(self):
            return self.result

        async def send(self, message):
            sent.append(message)

    reasoning = SimpleNamespace(
        type="reasoning",
        get_plain_text=lambda **_: "private reasoning",
    )
    event = Event()
    install_send_guard(event)

    await event.send(reasoning)

    assert sent == []
    assert event.get_extra("_group_agent_external_actions", []) == []
