"""The shared CallRecorder keeps the chosen call arguments and returns its fixed reply."""

from __future__ import annotations

import pytest

from tests.fixtures.call_recorder import CallRecorder

FIXED_REPLY = "answer"


def join_first_two(first: str, second: str, *_rest: str) -> str:
    """Return one entry built from the first two call arguments."""
    return f"{first}+{second}"


def test_recorder_without_picks_keeps_each_argument_tuple() -> None:
    """With no pick indexes, every call records its whole positional argument tuple."""
    recorder = CallRecorder()
    recorder("a", "b")
    recorder("c")
    assert recorder == [("a", "b"), ("c",)]


def test_recorder_with_one_pick_keeps_that_argument_alone() -> None:
    """One pick index records the argument at that position without a tuple."""
    recorder = CallRecorder(1)
    recorder("a", "b", "c")
    assert recorder == ["b"]


def test_recorder_with_several_picks_keeps_a_tuple_in_pick_order() -> None:
    """Several pick indexes record a tuple of those arguments in the order given."""
    recorder = CallRecorder(2, 0)
    recorder("a", "b", "c")
    assert recorder == [("c", "a")]


def test_recorder_with_a_record_function_keeps_its_result() -> None:
    """A record function builds each entry from the call arguments."""
    recorder = CallRecorder(record=join_first_two)
    recorder("a", "b", "c")
    assert recorder == ["a+b"]


def test_recorder_returns_the_fixed_reply_and_ignores_keywords() -> None:
    """Every call returns the reply, and keyword arguments stay out of the entry."""
    recorder = CallRecorder(reply=FIXED_REPLY)
    assert recorder("a", flag=True) == FIXED_REPLY
    assert recorder == [("a",)]


def test_recorder_rejects_picks_together_with_a_record_function() -> None:
    """Pick indexes and a record function together raise before any call records."""
    with pytest.raises(ValueError, match="not both"):
        CallRecorder(0, record=join_first_two)
