"""Keep privileged workflow pins compatible with the trusted base schema."""

import unittest
from pathlib import Path

import yaml

from scripts import check_compliance_tree

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github/workflows/immutable-conflict-check.yml"
)
TRUSTED_CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
TRUSTED_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
UPGRADED_CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
UPGRADED_PYTHON = "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0"


class PrivilegedActionPinsTest(unittest.TestCase):
    """Exercise the real workflow against the exact privileged schema."""

    def test_workflow_retains_base_trusted_pins(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        document = yaml.safe_load(text)
        steps = document["jobs"]["immutable-compliance"]["steps"]
        actions = [step["uses"] for step in steps if "uses" in step]
        self.assertEqual(actions, [TRUSTED_CHECKOUT, TRUSTED_CHECKOUT, TRUSTED_PYTHON])
        self.assertEqual(
            check_compliance_tree._pull_target_violations(document, text, str(WORKFLOW_PATH)),
            [],
        )

    def test_untrusted_pin_upgrades_remain_rejected(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        text = text.replace(TRUSTED_CHECKOUT, UPGRADED_CHECKOUT)
        text = text.replace(TRUSTED_PYTHON, UPGRADED_PYTHON)
        document = yaml.safe_load(text)
        violations = check_compliance_tree._pull_target_violations(
            document, text, str(WORKFLOW_PATH)
        )
        self.assertTrue(any("job schema is not trusted" in item for item in violations))
