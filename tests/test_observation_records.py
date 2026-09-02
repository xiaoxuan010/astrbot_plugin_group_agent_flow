from observation import prepare_observation_records


def test_prepare_observation_records_uses_only_supplied_batch_rows():
    prepared = prepare_observation_records(
        [
            {
                "seq": 7,
                "record_kind": "group_message",
                "message_id": "batch-1",
                "group_id": "1",
                "sender_id": "2",
                "sender_name": "Alice",
                "timestamp": 1710000000,
                "text": "batch message",
            },
        ],
        snapshot_seq=9,
        max_context_tokens=8192,
    )

    assert prepared.source_seqs == (7,)
    assert "batch message" in prepared.contexts[0]["content"]


def test_prepare_observation_records_renderer_name_line_messages():
    """renderer_name='line_messages' 时输出行式消息上下文而非 XML。"""
    prepared = prepare_observation_records(
        [
            {
                "seq": 7,
                "record_kind": "group_message",
                "message_id": "batch-1",
                "group_id": "1",
                "group_name": "测试群",
                "sender_id": "2",
                "sender_name": "Alice",
                "timestamp": 1710000000,
                "text": "batch message",
                "components": [{"type": "text", "text": "batch message"}],
            },
        ],
        snapshot_seq=9,
        max_context_tokens=8192,
        renderer_name="line_messages",
    )

    content = prepared.contexts[0]["content"]
    assert "【测试群】新消息：" in content
    assert "(2)Alice:" in content
    assert "#batch-1" in content
    assert "batch message" in content


def test_prepare_observation_records_unknown_renderer_falls_back_to_xml():
    """未知 renderer_name 回退到 XML 增量块（向后兼容）。"""
    prepared = prepare_observation_records(
        [
            {
                "seq": 7,
                "record_kind": "group_message",
                "message_id": "batch-1",
                "group_id": "1",
                "sender_id": "2",
                "sender_name": "Alice",
                "timestamp": 1710000000,
                "text": "batch message",
            },
        ],
        snapshot_seq=9,
        max_context_tokens=8192,
        renderer_name="unknown",
    )

    content = prepared.contexts[0]["content"]
    assert "<group_messages_delta" in content
    assert 'message_id="batch-1"' in content
