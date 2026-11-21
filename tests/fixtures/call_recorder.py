"""A callable list that stands in for callbacks, dialogs, and signal slots in tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class CallRecorder(list[Any]):
    """Record chosen positional arguments of each call and return a fixed reply.

    With no pick indexes, each entry is the whole argument tuple. One index keeps
    that argument alone. Several indexes keep a tuple of those arguments. A record
    function instead builds each entry from the call arguments. The recorder
    compares equal to a plain list of the same entries.
    """

    def __init__(
        self, *pick: int, reply: Any = None, record: Callable[..., Any] | None = None
    ) -> None:
        """Store the argument indexes or the record function, and the reply for every call."""
        if pick and record is not None:
            raise ValueError("Pass pick indexes or a record function, not both.")
        super().__init__()
        self._pick = pick
        self._reply = reply
        self._record = record

    def __call__(self, *args: Any, **_kwargs: Any) -> Any:
        """Record the chosen arguments and return the fixed reply."""
        self.append(self._select_arguments(args))
        return self._reply

    def _select_arguments(self, args: tuple[Any, ...]) -> Any:
        """Return the entry that one call records."""
        if self._record is not None:
            return self._record(*args)
        if not self._pick:
            return args
        if len(self._pick) == 1:
            return args[self._pick[0]]
        return tuple(args[index] for index in self._pick)
