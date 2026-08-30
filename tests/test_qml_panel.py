from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "Panel.qml").read_text(encoding="utf-8")
INDICATOR = (ROOT / "Indicator.qml").read_text(encoding="utf-8")


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

    def test_accordion_uses_logical_sources_and_named_receivers(self) -> None:
        self.assertIn('property string activeSection: "sources"', PANEL)
        self.assertIn('return ["sources", "session", "profiles", "receiver", "connection", "privacy", "diagnostics", "removal"]', PANEL)
        self.assertIn('label: "1. Sources"', PANEL)
        self.assertIn('label: "2. Session Controls"', PANEL)
        self.assertIn('label: "3. Mix Profiles"', PANEL)
        self.assertIn('label: "8. Removal"', PANEL)
        self.assertIn("data.sourceChoices instanceof Array", PANEL)
        self.assertIn("sourceChoiceIds: selectedIds.slice()", PANEL)
        self.assertIn('proc.command = [backend, "source-pin"', PANEL)
        self.assertIn('proc.command = [backend, "connection-select"', PANEL)
        self.assertIn('proc.command = [backend, "connection-rename"', PANEL)
        self.assertIn('placeholderText: "Receiver name"', PANEL)

    def test_keyboard_navigation_scrolls_central_focus_and_keeps_horizontal_motion_local(self) -> None:
        self.assertIn("function focusTarget()", PANEL)
        self.assertIn("function scheduleFocusScroll()", PANEL)
        self.assertIn("onTriggered: root.ensureVisible(root.focusTarget())", PANEL)
        self.assertIn("focusColumn + dx", PANEL)
        self.assertNotIn("else switchView(dx)", PANEL)

    def test_every_stable_click_target_has_an_idle_border(self) -> None:
        stable = PANEL.split("component StableButton: Button", 1)[1]
        self.assertIn("bordered: true", stable)

    def test_active_bar_indicator_is_fixed_visible_and_opens_controls(self) -> None:
        self.assertIn("mx-streaming", INDICATOR)
        self.assertIn("mx-capture", INDICATOR)
        self.assertNotIn("receiverLabel", INDICATOR)
        self.assertIn("interactive: sessionActive", INDICATOR)
        self.assertIn("onPressed: function()", INDICATOR)
        self.assertIn('"summon"', INDICATOR)


if __name__ == "__main__":
    unittest.main()
