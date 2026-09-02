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
        "&lt;内容&gt;</text></reply>"
    ) in content
    assert '<mention all="true"/>' in content
    assert '<mention user_id="10002" name="Bob"/>' in content
    assert "<text>正文 &lt;tag&gt;&amp;</text>" in content
    assert '<image url="https://example.com/a.jpg"/>' in content
    assert "signed.example.com" not in content
    assert '<face id="123"/>' in content
    assert '<poke target_id="10002"/>' in content
    assert "https://example.com/a.mp3" not in content
    assert "<voice/>" in content
    assert '<video url="https://example.com/a.mp4"/>' in content
    assert '<file name="a.txt" url="https://example.com/a.txt"/>' in content
    assert '<component type="forward"/>' in content


def test_xml_delta_returns_no_context_for_an_empty_event_list():
    assert build_renderer().render([]) == []


def _line_msg_event(
    message_id: str,
    sender_id: str,
    sender_name: str,
    timestamp: int,
    text: str = "hi",
) -> dict:
    return {
        "group_id": "1",
        "group_name": "测试群",
        "message_id": message_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "timestamp": timestamp,
        "components": [{"type": "text", "text": text}],
    }


def test_line_messages_basic_layout():
    """行式消息：完整日期独占一行 + (QQ号)昵称: + 行尾 #ID。"""
    content = build_renderer("line_messages").render(
        [_line_msg_event("m1", "u1", "甲", 1783836222)]
    )[0]["content"]
    lines = content.splitlines()
    assert lines[0] == "【测试群】新消息："
    assert lines[1] == "[2026/07/12 14:03]"
    assert lines[2] == "(u1)甲: hi  #m1"


def test_line_messages_same_minute_and_same_sender_omissions():
    """同分钟省略时间；同人连发省略「(QQ号)昵称: 」段；ID 永不省略。"""
    events = [
        _line_msg_event("m1", "u1", "甲", 1783836222, "第一条"),
        _line_msg_event("m2", "u1", "甲", 1783836222 + 10, "第二条"),
    ]
    lines = build_renderer("line_messages").render(events)[0]["content"].splitlines()
    assert lines[1] == "[2026/07/12 14:03]"
    assert lines[2] == "(u1)甲: 第一条  #m1"
    assert lines[3] == "第二条  #m2"


def test_line_messages_new_minute_and_new_sender_restore():
    """新分钟只显示 [HH:MM]；换人恢复昵称段。"""
    events = [
        _line_msg_event("m1", "u1", "甲", 1783836222, "甲一"),
        _line_msg_event("m2", "u2", "乙", 1783836222 + 600, "乙一"),
        _line_msg_event("m3", "u2", "乙", 1783836222 + 660, "乙二"),
    ]
    lines = build_renderer("line_messages").render(events)[0]["content"].splitlines()
    assert lines[1] == "[2026/07/12 14:03]"
    assert lines[2] == "(u1)甲: 甲一  #m1"
    assert lines[3] == "[14:13]"
    assert lines[4] == "(u2)乙: 乙一  #m2"
    # 第三条同人发言但跨分钟：显示 [14:14]，昵称段仍省略
    assert lines[5] == "[14:14]"
    assert lines[6] == "乙二  #m3"


def test_line_messages_reply_prefix_carries_full_date_and_quoted_id():
    """引用前缀：> [日期] #被引ID: (QQ号)昵称: 预览 /。"""
    events = [
        {
            **_line_msg_event("m1", "u1", "甲", 1783836222),
            "components": [
                {
                    "type": "reply",
                    "message_id": "m0",
                    "sender_id": "u0",
                    "sender_name": "乙",
                    "timestamp": 1783836000,
                    "text": "被引用的消息",
                },
                {"type": "text", "text": "回复内容"},
            ],
        },
    ]
    lines = build_renderer("line_messages").render(events)[0]["content"].splitlines()
    assert lines[1] == "[2026/07/12 14:03]"
    # 引用前缀（> 2026/07/12 14:00 #m0: (u0)乙: 被引用的消息 /）之后是本条发送者段 (u1)甲:
    assert lines[2] == (
        "> 2026/07/12 14:00 #m0: (u0)乙: 被引用的消息 / (u1)甲: 回复内容  #m1"
    )


def test_line_messages_mention_image_face_and_file():
    """@、图片、表情、戳一戳、语音、视频、文件占位符保持与研究一致。"""
    events = [
        {
            **_line_msg_event("m1", "u1", "甲", 1783836222, ""),
            "components": [
                {"type": "at", "user_id": "all", "name": ""},
                {"type": "at", "user_id": "u2", "name": "丙"},
                {"type": "image", "url": "/x/y/media_abc123.jpg"},
                {"type": "face", "id": "325"},
                {"type": "poke", "target_id": "u3"},
                {"type": "voice", "url": "https://example.com/a.mp3"},
                {"type": "video", "url": "https://example.com/a.mp4"},
                {"type": "file", "name": "a.txt", "url": "https://example.com/a.txt"},
                {"type": "text", "text": "看图"},
            ],
        },
    ]
    lines = build_renderer("line_messages").render(events)[0]["content"].splitlines()
    # 时间戳独占一行 [2026/07/12 14:03]，正文在下一行
    line = lines[2]
    assert "@全体成员" in line
    assert "@丙" in line
    assert "[图片:media_abc123" in line
    assert "[表情:325]" in line
    assert "[戳一戳]" in line
    assert "[语音]" in line
    assert "[视频]" in line
    assert "[文件:a.txt]" in line
    assert "看图" in line


def test_line_messages_state_resets_between_blocks():
    """每个块从头初始化状态，重复渲染结果一致。"""
    event = _line_msg_event("m1", "u1", "甲", 1783836222)
    renderer = build_renderer("line_messages")
    assert renderer.render([event]) == renderer.render([event])


def test_line_messages_empty_events_returns_no_context():
    assert build_renderer("line_messages").render([]) == []


def test_build_renderer_unknown_name_falls_back_to_xml():
    """未知渲染器名称回退到 XML 增量块。"""
    messages = build_renderer("nope").render(XML_EVENTS)
    assert messages[0]["content"].startswith("<group_messages_delta")
