"""A stand-in for urlopen responses in the provider and model list tests."""

from __future__ import annotations


class FakeResponse:
    """Stands in for a urlopen response and serves fixed bytes."""

    def __init__(self, body: bytes) -> None:
        """Store the body that read returns."""
        self._body = body

    def __enter__(self) -> FakeResponse:
        """Return the response itself, as urlopen does."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Hold no resources. Nothing to release."""
        return None

    def read(self, size: int = -1) -> bytes:
        """Return at most size bytes of the body."""
        return self._body if size < 0 else self._body[:size]
