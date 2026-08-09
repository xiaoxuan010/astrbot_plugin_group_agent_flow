from context_renderers import build_renderer


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
def test_xml_delta_returns_no_context_for_an_empty_event_list():
    assert build_renderer().render([]) == []
