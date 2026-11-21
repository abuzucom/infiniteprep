"""Behavioral tests for the hidden --forget-credentials flag (D266)."""

from __future__ import annotations

import keyring
import pytest

from infiniteprep.config.credentials import ENV_VAR_MAP, FORGET_FAILED_EXIT_CODE, SERVICE_NAME
from infiniteprep.main import FORGET_CREDENTIALS_FLAG, main
from tests.fixtures.memory_keyring import FailingKeyring, MemoryKeyring

OTHER_SERVICE = "SomeOtherApp"


@pytest.mark.usefixtures("restore_keyring")
def test_flag_deletes_every_infiniteprep_key() -> None:
    """All InfinitePrep keys go, a missing key is no error, and other apps keep theirs."""
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    saved = sorted(ENV_VAR_MAP)[:-1]
    for key_name in saved:
        backend.set_password(SERVICE_NAME, key_name, "placeholder-value")
    backend.set_password(OTHER_SERVICE, "acoustid_api_key", "kept")

    assert main([FORGET_CREDENTIALS_FLAG]) == 0
    assert backend.store == {(OTHER_SERVICE, "acoustid_api_key"): "kept"}


@pytest.mark.usefixtures("restore_keyring")
def test_flag_reports_a_keyring_failure() -> None:
    """A keyring that rejects deletes returns the failure exit code."""
    keyring.set_keyring(FailingKeyring())

    assert main([FORGET_CREDENTIALS_FLAG]) == FORGET_FAILED_EXIT_CODE
