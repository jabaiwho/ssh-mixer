from __future__ import annotations

import unittest
from pathlib import Path


PANEL = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text(encoding="utf-8")


class PanelRuntimeSafetyTest(unittest.TestCase):
    def test_button_visuals_do_not_change_with_pointer_position(self) -> None:
        self.assertIn("component StableButton: Button", PANEL)
        self.assertIn("color: hasCursor ? Style.hoverFillFor", PANEL)
        self.assertIn("component ActionButton: StableButton", PANEL)
        self.assertIn("component DestinationButton: StableButton", PANEL)
        self.assertIn("component RetentionButton: StableButton", PANEL)
        self.assertNotIn("onHovered: function(on)", PANEL)

    def test_hidden_setup_and_connection_bindings_remain_null_safe(self) -> None:
        self.assertIn("var connection = root.connection || ({})", PANEL)
        self.assertIn("enabled: !!root.windowsSetupPlan", PANEL)


if __name__ == "__main__":
    unittest.main()
