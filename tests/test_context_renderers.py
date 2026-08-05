from context_renderers import build_renderer

ACTION_EVENTS = [
    {
        "record_kind": "agent_action",
        "group_id": "1",
        "message_id": "agent-action:run-1:1",
        "timestamp": 1783836222,
        "text": "[At:10001] 大家好",
        "action_name": "send_message",
        "action_status": "succeeded",
    },
    {
        "record_kind": "agent_action",
        "group_id": "1",
        "message_id": "agent-action:run-1:2",
        "timestamp": 1783836223,
        "text": "收到",
        "action_name": "reply_message",
        "action_status": "succeeded",
        "target_message_id": "msg-101",
    },
    {
        "record_kind": "agent_action",
        "group_id": "1",
        "message_id": "agent-action:run-1:3",
        "timestamp": 1783836224,
        "text": "[消息表情 target=msg-102 reaction=赞]",
        "action_name": "react_message",
        "action_status": "succeeded",
        "target_message_id": "msg-102",
    },
    {
        "record_kind": "agent_action",
        "group_id": "1",
        "message_id": "agent-action:run-1:4",
        "timestamp": 1783836225,
        "text": "[戳一戳 target=10002]",
        "action_name": "poke_user",
        "action_status": "succeeded",
        "target_user_id": "10002",
    },
]


XML_EVENTS = [
    {
        "group_id": "1",
        "group_name": "测试 & 群",
        "message_id": "msg-201",
        "sender_id": "10001",
        "sender_name": "Alice <A>",
        "timestamp": 1783836222,
        "components": [
            {
                "type": "reply",
                "message_id": "msg-199",
                "sender_id": "10000",
                "sender_name": "Quoted",
                "timestamp": "1710000000",
                "text": "上一条 & <内容>",
            },
            {"type": "at", "user_id": "all", "name": ""},
            {"type": "at", "user_id": "10002", "name": "Bob"},
            {"type": "text", "text": "正文 <tag>&"},
            {
                "type": "image",
                "url": "https://example.com/a.jpg",
                "source_url": "https://signed.example.com/private/a.jpg",
            },
            {"type": "face", "id": "123"},
            {"type": "poke", "target_id": "10002"},
            {"type": "voice", "url": "https://example.com/a.mp3"},
            {"type": "video", "url": "https://example.com/a.mp4"},
            {"type": "file", "name": "a.txt", "url": "https://example.com/a.txt"},
            {"type": "forward"},
        ],
    },
    {
        "record_kind": "agent_action",
        "group_id": "1",
        "group_name": "测试 & 群",
        "message_id": "agent-action:run-1:1",
        "timestamp": 1783836223,
        "action_name": "reply_message",
        "action_status": "succeeded",
        "target_message_id": "msg-201",
        "components": [{"type": "text", "text": "收到"}],
    },
]


def test_xml_delta_aggregates_structured_components_into_one_user_block():
    messages = build_renderer().render(XML_EVENTS)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert content.startswith(
        '<group_messages_delta group_id="1" group_name="测试 &amp; 群">'
    )
    assert 'sender_name="Alice &lt;A&gt;"' in content
    assert (
        '<reply message_id="msg-199" sender_id="10000" sender_name="Quoted" '
        'timestamp="2024-03-10T00:00:00+08:00"><text>上一条 &amp; '
        '&lt;内容&gt;</text></reply>'
    ) in content
    assert '<mention all="true"/>' in content
    assert '<mention user_id="10002" name="Bob"/>' in content
    assert '<text>正文 &lt;tag&gt;&amp;</text>' in content
    assert '<image url="https://example.com/a.jpg"/>' in content
    assert "signed.example.com" not in content
    assert '<face id="123"/>' in content
    assert '<poke target_id="10002"/>' in content
    assert '<voice url="https://example.com/a.mp3"/>' in content
    assert '<video url="https://example.com/a.mp4"/>' in content
    assert '<file name="a.txt" url="https://example.com/a.txt"/>' in content
    assert '<component type="forward"/>' in content
    assert 'action_name="reply_message"' in content
    assert 'action_id="agent-action:run-1:1"' in content
    assert 'target_message_id="msg-201"' in content


def test_xml_delta_uses_later_group_name_when_replayed_action_has_none():
    events = [
        {
            "record_kind": "agent_action",
            "group_id": "1",
            "group_name": "",
            "message_id": "agent-action:run-1:1",
            "timestamp": 1783836222,
            "action_name": "send_message",
            "action_status": "succeeded",
            "components": [{"type": "text", "text": "早"}],
        },
        {
            "group_id": "1",
            "group_name": "测试群",
            "message_id": "msg-202",
            "sender_id": "10001",
            "sender_name": "Alice",
            "timestamp": 1783836223,
            "components": [{"type": "text", "text": "早"}],
        },
    ]

    content = build_renderer().render(events)[0]["content"]

    assert content.startswith(
        '<group_messages_delta group_id="1" group_name="测试群">'
    )


def test_xml_delta_returns_no_context_for_an_empty_event_list():
    assert build_renderer().render([]) == []


def test_xml_delta_exposes_completed_agent_actions():
    messages = build_renderer().render(ACTION_EVENTS)

    content = "\n".join(message["content"] for message in messages)
    assert 'action_name="send_message"' in content
    assert "[At:10001] 大家好" in content
    assert 'action_name="reply_message"' in content
    assert 'target_message_id="msg-101"' in content
    assert 'action_name="react_message"' in content
    assert 'target_message_id="msg-102"' in content
    assert "reaction=赞" in content
    assert 'action_name="poke_user"' in content
    assert 'target_user_id="10002"' in content
