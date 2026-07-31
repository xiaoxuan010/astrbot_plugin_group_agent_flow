from types import SimpleNamespace

import pytest
from astrbot.api import message_components as Comp

from event_codec import build_agent_action_record, extract_group_event


class FakeEvent:
    def __init__(self):
        self.message_obj = SimpleNamespace(
            message_id="msg-200",
            timestamp=1783836222,
            group=SimpleNamespace(group_name="测试群"),
        )
        self._messages = [
            Comp.Reply(
                id="msg-199",
                sender_id="10000",
                sender_nickname="Quoted",
                time=1710000000,
                message_str="上一条",
            ),
            Comp.At(qq="7", name="Bot"),
            Comp.Plain("正文"),
            Comp.Image(file="https://example.com/image.jpg"),
        ]

    def get_platform_id(self):
        return "NapCat-7"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return "1"

    def get_sender_id(self):
        return "10001"

    def get_sender_name(self):
        return "Alice"

    def get_self_id(self):
        return "7"

    def get_messages(self):
        return self._messages

    def get_message_outline(self):
        return "[引用消息] [At:Bot] 正文 [图片]"

    def get_message_str(self):
        return "正文"


def test_extract_group_event_preserves_routing_and_reply_metadata():
    record = extract_group_event(FakeEvent(), max_text_chars=4000)

    assert record["schema_version"] == 2
    assert record["record_kind"] == "group_message"
    assert record["flow_id"] == "NapCat-7:group:1"
    assert record["message_id"] == "msg-200"
    assert record["reply_to"] == "msg-199"
    assert record["is_directed_at_bot"] is True
    assert record["text"] == "[引用消息] [At:Bot] 正文 [图片]"


@pytest.mark.parametrize(
    (
        "action_name",
        "action_result",
        "expected_text",
        "expected_components",
        "reply_to",
        "target_message_id",
        "target_user_id",
    ),
    [
        (
            "send_message",
            {
                "success": True,
                "action": "send_message",
                "content": "大家好",
                "mentions": ["10001"],
            },
            "[At:10001] 大家好",
            [
                {"type": "at", "user_id": "10001", "name": ""},
                {"type": "text", "text": "大家好"},
            ],
            None,
            None,
            None,
        ),
        (
            "reply_message",
            {
                "success": True,
                "action": "reply_message",
                "message_id": "msg-199",
                "content": "收到",
                "mentions": ["10001"],
            },
            "[At:10001] 收到",
            [
                {"type": "reply", "message_id": "msg-199", "sender_id": ""},
                {"type": "at", "user_id": "10001", "name": ""},
                {"type": "text", "text": "收到"},
            ],
            "msg-199",
            "msg-199",
            None,
        ),
        (
            "react_message",
            {
                "success": True,
                "action": "react_message",
                "message_id": "msg-199",
                "reaction": "赞",
            },
            "[消息表情 target=msg-199 reaction=赞]",
            [
                {
                    "type": "reaction",
                    "message_id": "msg-199",
                    "reaction": "赞",
                }
            ],
            None,
            "msg-199",
            None,
        ),
        (
            "poke_user",
            {
                "success": True,
                "action": "poke_user",
                "user_id": "10001",
            },
            "[戳一戳 target=10001]",
            [{"type": "poke", "target_id": "10001"}],
            None,
            None,
            "10001",
        ),
    ],
)
def test_build_agent_action_record_preserves_visible_action_fact(
    action_name,
    action_result,
    expected_text,
    expected_components,
    reply_to,
    target_message_id,
    target_user_id,
):
    record = build_agent_action_record(
        FakeEvent(),
        run_id="run-1",
        action_index=2,
        action_name=action_name,
        action_result=action_result,
    )

    assert record["record_kind"] == "agent_action"
    assert record["message_id"] == "agent-action:run-1:2"
    assert record["targetable"] is False
    assert record["flow_id"] == "NapCat-7:group:1"
    assert record["sender_id"] == record["self_id"] == "7"
    assert record["text"] == expected_text
    assert record["components"] == expected_components
    assert record["reply_to"] == reply_to
    assert record["target_message_id"] == target_message_id
    assert record["target_user_id"] == target_user_id
    assert record["action_name"] == action_name
    assert record["action_status"] == "succeeded"
    assert record["run_id"] == "run-1"
    assert record["action_index"] == 2


def test_build_agent_action_record_rejects_mismatched_gateway_action():
    with pytest.raises(ValueError, match="action result mismatch"):
        build_agent_action_record(
            FakeEvent(),
            run_id="run-1",
            action_index=1,
            action_name="send_message",
            action_result={"success": True, "action": "reply_message"},
        )


def test_extract_group_event_serializes_supported_components():
    record = extract_group_event(FakeEvent(), max_text_chars=4000)

    assert record["components"] == [
        {
            "type": "reply",
            "message_id": "msg-199",
            "sender_id": "10000",
            "sender_name": "Quoted",
            "timestamp": "1710000000",
            "text": "上一条",
        },
        {"type": "at", "user_id": "7", "name": "Bot"},
        {"type": "text", "text": "正文"},
        {"type": "image", "url": "https://example.com/image.jpg"},
    ]


def test_extract_group_event_keeps_raw_remote_image_url_for_later_download():
    event = FakeEvent()
    event._messages = [
        Comp.Image(file=r"D:\\AstrBot\\data\\temp\\expired-first.jpg"),
        Comp.Image(file=r"D:\\AstrBot\\data\\temp\\expired-second.jpg"),
    ]
    event.message_obj.raw_message = {
        "message_seq": 4321,
        "message": [
            {"type": "image", "data": {"file": "first.jpg"}},
            {
                "type": "image",
                "data": {"url": "https://cdn.example.com/second.jpg"},
            },
        ]
    }

    record = extract_group_event(event, max_text_chars=4000)

    assert record["components"] == [
        {
            "type": "image",
            "url": r"D:\\AstrBot\\data\\temp\\expired-first.jpg",
        },
        {
            "type": "image",
            "url": r"D:\\AstrBot\\data\\temp\\expired-second.jpg",
            "source_url": "https://cdn.example.com/second.jpg",
        },
    ]
    assert record["message_seq"] == "4321"


def test_extract_group_event_bounds_text_without_losing_original_components():
    event = FakeEvent()
    event.get_message_outline = lambda: "abcdef"

    record = extract_group_event(event, max_text_chars=4)

    assert record["text"] == "abcd"
    assert len(record["components"]) == 4


def test_extract_group_event_preserves_poke_actor_target_and_direction():
    event = FakeEvent()
    event._messages = [Comp.Poke(id="7")]
    event.get_message_outline = lambda: "[ComponentType.Poke]"
    event.get_message_str = lambda: ""

    record = extract_group_event(event, max_text_chars=4000)

    assert record["sender_id"] == "10001"
    assert record["components"] == [
        {"type": "poke", "target_id": "7"}
    ]
    assert record["text"] == "[戳一戳 target=7]"
    assert record["is_directed_at_bot"] is True
