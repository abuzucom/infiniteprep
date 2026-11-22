"""Tests that each workflow bar stage button emits its own stage."""

from __future__ import annotations

from typing import Any

from infiniteprep.ui.workflow import STAGE_ORDER
from infiniteprep.ui.workflow_bar import WorkflowBar
from tests.fixtures.call_recorder import CallRecorder


def test_each_button_emits_its_stage(qtbot: Any) -> None:
    """A click on each stage button emits that stage, in bar order."""
    bar = WorkflowBar()
    qtbot.addWidget(bar)
    emitted = CallRecorder(0)
    bar.stage_triggered.connect(emitted)

    for stage in STAGE_ORDER:
        bar.stage_buttons[stage].click()

    assert emitted == [stage.value for stage in STAGE_ORDER]
