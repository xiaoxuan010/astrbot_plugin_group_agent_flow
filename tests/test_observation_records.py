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
