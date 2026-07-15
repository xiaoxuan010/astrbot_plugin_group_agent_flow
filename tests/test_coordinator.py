from coordinator import GroupRunCoordinator


def test_normal_message_becomes_due_after_debounce():
    coordinator = GroupRunCoordinator(debounce_seconds=10, direct_delay_seconds=1)
    coordinator.enqueue("group:1", seq=10, received_at=100.0, directed=False)

    assert coordinator.begin_if_due("group:1", now=109.9) is None
    snapshot = coordinator.begin_if_due("group:1", now=110.0)
    assert snapshot is not None
    assert snapshot.snapshot_seq == 10


def test_directed_message_uses_short_delay_when_idle():
    coordinator = GroupRunCoordinator(debounce_seconds=10, direct_delay_seconds=1)
    coordinator.enqueue("group:1", seq=10, received_at=100.0, directed=True)

    assert coordinator.begin_if_due("group:1", now=100.9) is None
    assert coordinator.begin_if_due("group:1", now=101.0).snapshot_seq == 10


def test_messages_arriving_during_run_are_reserved_for_next_snapshot():
    coordinator = GroupRunCoordinator(debounce_seconds=10, direct_delay_seconds=1)
    coordinator.enqueue("group:1", seq=10, received_at=100.0, directed=False)
    first = coordinator.begin_if_due("group:1", now=110.0)

    coordinator.enqueue("group:1", seq=11, received_at=111.0, directed=True)
    assert coordinator.begin_if_due("group:1", now=112.0) is None

    coordinator.finish(first.run_id, finished_at=120.0)
    second = coordinator.begin_if_due("group:1", now=120.0)
    assert second is not None
    assert second.snapshot_seq == 11
    assert first.snapshot_seq == 10


def test_minimum_cycle_interval_limits_fast_restarts():
    coordinator = GroupRunCoordinator(
        debounce_seconds=1,
        direct_delay_seconds=0,
        min_cycle_interval_seconds=10,
    )
    coordinator.enqueue("group:1", seq=1, received_at=100.0, directed=True)
    first = coordinator.begin_if_due("group:1", now=100.0)
    coordinator.enqueue("group:1", seq=2, received_at=101.0, directed=True)
    coordinator.finish(first.run_id, finished_at=102.0)

    assert coordinator.begin_if_due("group:1", now=109.9) is None
    assert coordinator.begin_if_due("group:1", now=110.0).snapshot_seq == 2


def test_finish_rejects_unknown_run():
    coordinator = GroupRunCoordinator()

    try:
        coordinator.finish("missing", finished_at=1.0)
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("unknown run must raise KeyError")
