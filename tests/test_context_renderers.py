from context_renderers import build_renderer


EVENTS = [
    {
        "seq": 101,
        "group_id": "1",
        "message_id": "msg-101",
        "sender_id": "10001",
        "sender_name": "Alice",
        "timestamp": 1783836222,
        "text": "第一条消息",
        "reply_to": None,
    },
    {
        "seq": 102,
        "group_id": "1",
        "message_id": "msg-102",
        "sender_id": "10002",
        "sender_name": "Bob",
        "timestamp": 1783836223,
        "text": "回复上一条",
        "reply_to": "msg-101",
    },
]


def test_legacy_delta_preserves_layout_and_exposes_message_ids():
    messages = build_renderer("legacy_delta").render(EVENTS)

    assert messages == [
        {
            "role": "user",
            "content": (
                "<group_messages_delta>\n"
                "[Alice/14:03:42 msg=msg-101]: 第一条消息\n"
                "---\n"
                "[Bob/14:03:43 msg=msg-102]: 回复上一条\n"
                "</group_messages_delta>"
            ),
        }
    ]


def test_plain_lines_uses_newlines_without_markdown_separator():
    messages = build_renderer("plain_lines").render(EVENTS)

    assert len(messages) == 1
    assert "\n---\n" not in messages[0]["content"]
    assert messages[0]["content"].splitlines() == [
        "[QQ group=1 msg=msg-101 sender=10001 name=Alice time=2026-07-12T14:03:42+08:00] 第一条消息",
        "[QQ group=1 msg=msg-102 sender=10002 name=Bob time=2026-07-12T14:03:43+08:00 reply_to=msg-101] 回复上一条",
    ]


def test_native_messages_preserves_one_user_message_per_group_event():
    messages = build_renderer("native_messages").render(EVENTS)

    assert messages == [
        {
            "role": "user",
            "content": "[QQ group=1 msg=msg-101 sender=10001 name=Alice time=2026-07-12T14:03:42+08:00] 第一条消息",
        },
        {
            "role": "user",
            "content": "[QQ group=1 msg=msg-102 sender=10002 name=Bob time=2026-07-12T14:03:43+08:00 reply_to=msg-101] 回复上一条",
        },
    ]


def test_all_renderers_return_no_context_for_an_empty_event_list():
    for renderer_name in ("legacy_delta", "plain_lines", "native_messages"):
        assert build_renderer(renderer_name).render([]) == []


def test_unknown_renderer_is_rejected():
    try:
        build_renderer("unknown")
    except ValueError as exc:
        assert "unknown context renderer" in str(exc)
    else:
        raise AssertionError("unknown renderer must raise ValueError")
