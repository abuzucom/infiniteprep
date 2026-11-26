"""USB recovery retries, untrusted numbers, pipe draining, and Windows output encoding."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from infiniteprep.services.usb._shared import clean_int, win32_error
from infiniteprep.services.usb.macos import bounded_command_runner
from infiniteprep.services.usb.manifest import (
    MANIFEST_STATE_IN_PROGRESS,
    read_in_progress_manifest,
)
from infiniteprep.services.usb.recovery import (
    ReconnectChoice,
    UsbJournalEntry,
    UsbRecoveryJournalStore,
)
from infiniteprep.services.usb.targets import TargetCandidate
from infiniteprep.services.usb.windows import (
    DISK_COMMAND_TEXT,
    PARTITION_COMMAND_TEXT,
    UTF8_OUTPUT_PREFIX,
    VOLUME_COMMAND_TEXT,
)
from tests.fixtures.call_recorder import CallRecorder
from tests.unit.usb_manifest_support import build_journal_entry, build_manifest
from tests.unit.usb_preflight_support import build_inspection, build_partition

DEFAULT_SIZE = 7
RUNNER_TIMEOUT_SECONDS = 20.0
# Twice the usual 64 KiB pipe buffer and four times the stderr capture cap.
STDERR_FLOOD_BYTES = 256 * 1024
# A child that ran without a stall finishes far inside the timeout.
UNSTALLED_RUN_SECONDS = 10.0
# Win32 ERROR_SHARING_VIOLATION. A C errno 32 reads as "Broken pipe".
WIN32_SHARING_VIOLATION = 32


def select_export_id(item: UsbJournalEntry, _path: Path) -> str:
    """Return the export id that one recovery callback received."""
    return item.export_id


class FailOnceJournalStore(UsbRecoveryJournalStore):
    """A journal store whose next save fails once, as a full disk or a pulled drive would."""

    fail_next_save = False

    def save_entries(self, entries: list[UsbJournalEntry]) -> None:
        """Raise once when armed, then save as usual."""
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("journal disk full")
        super().save_entries(entries)


class UsbRecoveryRetryTests(unittest.TestCase):
    """A Resume interrupted by a journal failure can run again."""

    def test_resume_retries_after_journal_failure(self) -> None:
        """A Resume that fails on the journal save keeps the manifest and succeeds on retry."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir) / "usb"
            mount_root.mkdir()
            store = FailOnceJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
            entry = build_journal_entry(str(mount_root))
            in_prog = build_manifest(
                export_id=entry.export_id,
                state=MANIFEST_STATE_IN_PROGRESS,
                completed_at_utc=None,
            )
            store.start_export(entry, mount_root, in_prog)
            partition = build_partition(uuid=entry.volume_uuid, mount_points=(str(mount_root),))
            inspection = build_inspection(drive=entry.drive_identity, partitions=(partition,))
            match = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=inspection, error=None)
            )
            assert match is not None
            calls = CallRecorder(record=select_export_id)

            store.fail_next_save = True
            with self.assertRaises(OSError):
                store.apply_reconnect_choice(
                    match,
                    ReconnectChoice.RESUME,
                    mount_root,
                    resume_callback=calls,
                )
            self.assertIsNotNone(read_in_progress_manifest(mount_root))

            updated = store.apply_reconnect_choice(
                match,
                ReconnectChoice.RESUME,
                mount_root,
                resume_callback=calls,
            )

            self.assertEqual(updated.last_recovery_action, ReconnectChoice.RESUME.value)
            self.assertIsNone(read_in_progress_manifest(mount_root))
            self.assertEqual(calls, [entry.export_id, entry.export_id])


class CleanIntTests(unittest.TestCase):
    """Untrusted numbers that cannot become an int fall back to the default."""

    def test_infinite_or_invalid_gives_default(self) -> None:
        """Infinities, NaN, text, and None give the default."""
        for value in (float("inf"), float("-inf"), float("nan"), "abc", None):
            with self.subTest(value=value):
                self.assertEqual(clean_int(value, DEFAULT_SIZE), DEFAULT_SIZE)

    def test_finite_values_parse(self) -> None:
        """A numeric string parses to its value and a negative number clamps to zero."""
        self.assertEqual(clean_int("42"), 42)
        self.assertEqual(clean_int(-3), 0)


@unittest.skipIf(sys.platform == "win32", reason="The runner targets the POSIX inspector.")
class BoundedRunnerDrainTests(unittest.TestCase):
    """A child that floods stderr before it writes stdout finishes without a stall."""

    def test_early_stderr_flood_never_stalls(self) -> None:
        """The runner drains a stderr flood and still returns stdout in time."""
        script = (
            "import sys\n"
            f"sys.stderr.write('e' * {STDERR_FLOOD_BYTES})\n"
            "sys.stderr.flush()\n"
            "sys.stdout.write('done')\n"
        )
        started = time.monotonic()

        completed = bounded_command_runner([sys.executable, "-c", script], RUNNER_TIMEOUT_SECONDS)

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "done")
        self.assertLess(time.monotonic() - started, UNSTALLED_RUN_SECONDS)


class WindowsOutputEncodingTests(unittest.TestCase):
    """Each PowerShell command sets UTF-8 output before it runs."""

    def test_every_command_sets_utf8_output(self) -> None:
        """The disk, partition, and volume commands all start with the UTF-8 prefix."""
        for text in (DISK_COMMAND_TEXT, PARTITION_COMMAND_TEXT, VOLUME_COMMAND_TEXT):
            with self.subTest(text=text):
                self.assertTrue(text.startswith(UTF8_OUTPUT_PREFIX))


@unittest.skipUnless(sys.platform == "win32", reason="Win32 error codes exist only on Windows.")
class Win32ErrorTests(unittest.TestCase):
    """A Win32 code keeps its own meaning instead of a C errno meaning."""

    def test_sharing_violation_maps_permission_error(self) -> None:
        """A sharing violation becomes a PermissionError that keeps the Win32 code."""
        error = win32_error(WIN32_SHARING_VIOLATION, Path("C:/usb/meta.json"))

        self.assertIsInstance(error, PermissionError)
        self.assertEqual(error.winerror, WIN32_SHARING_VIOLATION)
        self.assertNotIn("Broken pipe", str(error))


if __name__ == "__main__":
    unittest.main()
