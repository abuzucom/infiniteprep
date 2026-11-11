"""Behavioral tests for the in-memory batch queue: ordering, reorder, and cancel."""

from __future__ import annotations

import pytest

from infiniteprep.services.job_orchestrator import (
    BatchKind,
    BatchQueue,
    BatchState,
    QueuedBatch,
)


def enqueue_three(queue: BatchQueue) -> tuple[QueuedBatch, QueuedBatch, QueuedBatch]:
    """Add an import, an analyze batch, and a write batch, in that order."""
    return (
        queue.enqueue(BatchKind.IMPORT, "first"),
        queue.enqueue(BatchKind.ANALYZE, "second"),
        queue.enqueue(BatchKind.WRITE, "third"),
    )


def test_batches_start_in_arrival_order_one_at_a_time() -> None:
    """Only one batch runs. The next starts after the running one ends, in FIFO order."""
    queue = BatchQueue()
    first, second, third = enqueue_three(queue)

    assert queue.start_next() is first
    assert first.state is BatchState.RUNNING
    assert queue.start_next() is None
    assert [batch.label for batch in queue.waiting()] == ["second", "third"]

    queue.finish_running(BatchState.COMPLETED, "report")
    assert first.state is BatchState.COMPLETED
    assert first.result == "report"
    assert queue.start_next() is second
    queue.finish_running(BatchState.COMPLETED)
    assert queue.start_next() is third
    queue.finish_running(BatchState.COMPLETED)

    assert queue.start_next() is None
    assert not queue.is_busy()
    assert queue.finished() == [first, second, third]


def test_display_order_is_running_then_waiting_then_finished() -> None:
    """batches lists the running batch first, then the waiting line, then finished ones."""
    queue = BatchQueue()
    first, second, third = enqueue_three(queue)
    queue.start_next()
    queue.finish_running(BatchState.COMPLETED)
    queue.start_next()

    assert queue.batches() == [second, third, first]
    assert queue.running is second


def test_move_reorders_waiting_batches_only() -> None:
    """A waiting batch moves by one place. The running batch and the ends refuse to move."""
    queue = BatchQueue()
    first, second, third = enqueue_three(queue)
    queue.start_next()

    assert queue.move(third.id, -1) is True
    assert queue.waiting() == [third, second]
    assert queue.move(third.id, -1) is False
    assert queue.move(second.id, 1) is False
    assert queue.move(first.id, 1) is False
    assert queue.move("no-such-batch", 1) is False

    queue.finish_running(BatchState.COMPLETED)
    assert queue.start_next() is third


def test_reorder_waiting_applies_a_full_order_and_refuses_a_mismatch() -> None:
    """A dragged order applies only when it names exactly the waiting batches."""
    queue = BatchQueue()
    first, second, third = enqueue_three(queue)

    assert queue.reorder_waiting([third.id, first.id, second.id]) is True
    assert queue.waiting() == [third, first, second]
    assert queue.reorder_waiting([third.id, first.id]) is False
    assert queue.reorder_waiting([third.id, first.id, first.id]) is False
    assert queue.reorder_waiting([third.id, first.id, "no-such-batch"]) is False
    assert queue.waiting() == [third, first, second]


def test_cancel_removes_a_waiting_batch_and_never_starts_it() -> None:
    """A cancelled waiting batch leaves the line. The running batch is not cancelled here."""
    queue = BatchQueue()
    first, second, third = enqueue_three(queue)
    queue.start_next()

    assert queue.cancel_waiting(second.id) is second
    assert second.state is BatchState.CANCELLED
    assert queue.waiting() == [third]
    assert queue.cancel_waiting(first.id) is None
    assert first.state is BatchState.RUNNING

    queue.finish_running(BatchState.COMPLETED)
    assert queue.start_next() is third


def test_cancel_all_waiting_empties_the_line_and_keeps_the_running_batch() -> None:
    """Cancel all drops every waiting batch and leaves the running one alone."""
    queue = BatchQueue()
    first, second, third = enqueue_three(queue)
    queue.start_next()

    assert queue.cancel_all_waiting() == [second, third]
    assert queue.waiting() == []
    assert queue.running is first
    assert queue.is_busy()


def test_clear_finished_forgets_only_finished_batches() -> None:
    """Clear Finished keeps the running and waiting batches."""
    queue = BatchQueue()
    first, second, third = enqueue_three(queue)
    queue.start_next()
    queue.finish_running(BatchState.FAILED)
    queue.start_next()

    queue.clear_finished()

    assert queue.finished() == []
    assert queue.batches() == [second, third]
    assert first.state is BatchState.FAILED


def test_finish_running_rejects_misuse() -> None:
    """Finishing with no running batch, or with a state that is not an end state, raises."""
    queue = BatchQueue()
    with pytest.raises(RuntimeError, match="No batch is running"):
        queue.finish_running(BatchState.COMPLETED)

    queue.enqueue(BatchKind.ANALYZE, "only")
    queue.start_next()
    with pytest.raises(ValueError, match="not an end state"):
        queue.finish_running(BatchState.WAITING)
