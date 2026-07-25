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


def test_include_pending_seq_extends_existing_snapshot_without_scheduling():
    coordinator = GroupRunCoordinator(debounce_seconds=0, direct_delay_seconds=0)

    assert coordinator.include_pending_seq("group:1", 12) is False
    assert coordinator.next_due_at("group:1") is None

    coordinator.enqueue("group:1", seq=11, received_at=100.0, directed=False)

    assert coordinator.include_pending_seq("group:1", 12) is True
    assert coordinator.begin_if_due("group:1", now=100.0).snapshot_seq == 12


def test_action_seq_extends_messages_reserved_during_active_run():
    coordinator = GroupRunCoordinator(
        debounce_seconds=0,
        direct_delay_seconds=0,
        min_cycle_interval_seconds=0,
    )
    coordinator.enqueue("group:1", seq=10, received_at=100.0, directed=False)
    first = coordinator.begin_if_due("group:1", now=100.0)
    coordinator.enqueue("group:1", seq=11, received_at=101.0, directed=False)

    assert coordinator.include_pending_seq("group:1", 12) is True

    coordinator.finish(first.run_id, finished_at=102.0)
    second = coordinator.begin_if_due("group:1", now=102.0)
    assert second.snapshot_seq == 12


def test_message_arriving_after_an_unscheduled_action_covers_its_seq():
    coordinator = GroupRunCoordinator(debounce_seconds=0, direct_delay_seconds=0)

    assert coordinator.include_pending_seq("group:1", 12) is False
    coordinator.enqueue("group:1", seq=13, received_at=100.0, directed=False)

    assert coordinator.begin_if_due("group:1", now=100.0).snapshot_seq == 13


def test_run_snapshot_freezes_generation_and_validates_active_identity():
    coordinator = GroupRunCoordinator(debounce_seconds=0, direct_delay_seconds=0)
    coordinator.enqueue("group:1", seq=1, received_at=100.0, directed=False)

    snapshot = coordinator.begin_if_due("group:1", now=100.0)

    assert snapshot.generation == 0
    assert coordinator.is_run_current(
        snapshot.run_id,
        flow_id="group:1",
        generation=0,
    )
    assert not coordinator.is_run_current(
        snapshot.run_id,
        flow_id="group:1",
        generation=1,
    )


def test_clear_flow_invalidates_active_pending_and_frequency_state():
    coordinator = GroupRunCoordinator(
        debounce_seconds=0,
        direct_delay_seconds=0,
        min_cycle_interval_seconds=100,
    )
    coordinator.enqueue("group:1", seq=1, received_at=100.0, directed=False)
    old = coordinator.begin_if_due("group:1", now=100.0)
    coordinator.enqueue("group:1", seq=2, received_at=101.0, directed=False)

    generation = coordinator.clear_flow("group:1")

    assert generation == 1
    assert coordinator.next_due_at("group:1") is None
    assert not coordinator.is_run_current(
        old.run_id,
        flow_id="group:1",
        generation=old.generation,
    )
    assert coordinator.finish_if_active(old.run_id, finished_at=102.0) is False

    coordinator.enqueue("group:1", seq=1, received_at=103.0, directed=False)
    fresh = coordinator.begin_if_due("group:1", now=103.0)
    assert fresh is not None
    assert fresh.snapshot_seq == 1
    assert fresh.generation == 1
