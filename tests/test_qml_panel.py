from __future__ import annotations

import unittest
from pathlib import Path


PANEL = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text(encoding="utf-8")


class PanelRuntimeSafetyTest(unittest.TestCase):
    def test_generic_action_hover_does_not_select_every_sentinel_button(self) -> None:
        self.assertIn(
            'hasCursor: rowIndex >= 0 && root.cursorActive && root.focusSection === "actions"',
            PANEL,
        )
        self.assertIn("if (on && rowIndex >= 0)", PANEL)

    def test_hidden_setup_and_connection_bindings_remain_null_safe(self) -> None:
        self.assertIn("var connection = root.connection || ({})", PANEL)
        self.assertIn("enabled: !!root.windowsSetupPlan", PANEL)


if __name__ == "__main__":
    unittest.main()
