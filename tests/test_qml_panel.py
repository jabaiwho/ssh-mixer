from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "Panel.qml").read_text(encoding="utf-8")
INDICATOR = (ROOT / "Indicator.qml").read_text(encoding="utf-8")


class PanelRuntimeSafetyTest(unittest.TestCase):
    def test_button_visuals_do_not_change_with_pointer_position(self) -> None:
        self.assertIn("component StableButton: Button", PANEL)
        self.assertIn("color: hasCursor ? Util.alpha(root.keyboardCursorColor, 0.28)", PANEL)
        self.assertIn("component ActionButton: StableButton", PANEL)
        self.assertIn("component DestinationButton: StableButton", PANEL)
        self.assertIn("component RetentionButton: StableButton", PANEL)
        self.assertNotIn("onHovered: function(on)", PANEL)

    def test_hidden_setup_and_connection_bindings_remain_null_safe(self) -> None:
        self.assertIn("var connection = root.connection || ({})", PANEL)
        self.assertIn("enabled: !!root.windowsSetupPlan", PANEL)

    def test_inputs_are_always_visible_above_the_renumbered_accordions(self) -> None:
        self.assertIn('property string activeSection: "sources"', PANEL)
        self.assertIn('return ["sources", "profiles", "receiver", "connection", "privacy", "diagnostics", "removal"]', PANEL)
        self.assertIn('label: "1. Inputs"', PANEL)
        self.assertIn('label: "2. Mix Profiles"', PANEL)
        self.assertIn('label: "7. Removal"', PANEL)
        self.assertNotIn('Session Controls', PANEL)
        self.assertIn('visible: true // Inputs are never collapsed.', PANEL)
        self.assertIn("data.sourceChoices instanceof Array", PANEL)
        self.assertIn("sourceChoiceIds: selectedIds.slice()", PANEL)
        self.assertIn('proc.command = [backend, "source-pin"', PANEL)
        self.assertIn('proc.command = [backend, "connection-select"', PANEL)
        self.assertIn('proc.command = [backend, "connection-rename"', PANEL)
        self.assertIn('placeholderText: "Receiver name"', PANEL)

    def test_source_selection_drives_sessions_but_capture_requires_confirmation(self) -> None:
        self.assertIn('import "PanelSession.js" as PanelSession', PANEL)
        self.assertIn("function requestSessionApply(captureConfirmed, startWhenStopped, reuseConfiguredSelection)", PANEL)
        self.assertIn("PanelSession.nextCommand(", PANEL)
        self.assertIn("var reuseConfiguredSelection = !configurationDirty", PANEL)
        self.assertIn("requestSessionApply(false, activeSession, reuseConfiguredSelection)", PANEL)
        self.assertIn("function confirmCaptureSource()", PANEL)
        self.assertIn("property var pendingCaptureSource: null", PANEL)
        self.assertIn("captureSession && sources[i].recentChoice === true", PANEL)
        self.assertIn('label: "End Stream"', PANEL)
        self.assertIn('text: "Choosing an Input starts streaming. Turning off the final Input ends it."', PANEL)
        self.assertIn('proc.command = [backend, "configure", "--json", JSON.stringify(payload || {})]', PANEL)
        self.assertNotIn('label: root.activeSession ? "Stop" : "Start"', PANEL)

    def test_errors_are_prominent_above_inputs_and_revealed_once(self) -> None:
        self.assertIn('import "PanelAlerts.js" as PanelAlerts', PANEL)
        self.assertIn("property string operationError: \"\"", PANEL)
        self.assertIn("readonly property string prominentError:", PANEL)
        self.assertIn("function revealProminentError()", PANEL)
        self.assertIn("PanelAlerts.revealDecision(", PANEL)
        self.assertIn("id: sessionErrorBanner", PANEL)
        self.assertIn('text: "SSH-MIXER ERROR"', PANEL)
        self.assertIn('text: "Press 6 for detailed, locally redacted diagnostics."', PANEL)
        self.assertLess(PANEL.index("id: sessionErrorBanner"), PANEL.index("id: sourcesHeader"))
        self.assertIn('visible: root.message !== "" && root.prominentError === ""', PANEL)
        self.assertNotIn('text: root.status.error ? root.status.error : root.message', PANEL)

    def test_number_keys_jump_to_corresponding_sections(self) -> None:
        self.assertIn("function activateNumberedSection(number)", PANEL)
        self.assertIn('if (/^[1-7]$/.test(t)) root.activateNumberedSection(Number(t))', PANEL)

    def test_keyboard_navigation_scrolls_sections_and_keeps_horizontal_motion_local(self) -> None:
        self.assertIn('import "PanelNavigation.js" as PanelNavigation', PANEL)
        self.assertIn('readonly property color keyboardCursorColor: "#FFD700"', PANEL)
        self.assertIn("property var keyboardTarget: null", PANEL)
        self.assertIn("function navigationItems()", PANEL)
        self.assertIn("function selectFirstKeyboardTarget(section)", PANEL)
        self.assertIn("PanelNavigation.nextIndex(", PANEL)
        self.assertIn("keyboardTarget.keyboardActivate()", PANEL)
        self.assertIn("property bool keyboardNavigable: true", PANEL)
        self.assertIn("Border.flat(root.keyboardCursorColor", PANEL)
        self.assertNotIn("hasCursor: false", PANEL)
        self.assertIn("function focusTarget()", PANEL)
        self.assertIn("function scheduleFocusScroll()", PANEL)
        self.assertIn("function scheduleSectionScroll(section)", PANEL)
        self.assertIn("function ensureSectionVisible(section)", PANEL)
        self.assertIn("PanelScroll.sectionTargetY(", PANEL)
        self.assertIn("root.ensureSectionVisible(section)", PANEL)
        self.assertIn("scheduleSectionScroll(nextSection)", PANEL)
        self.assertNotIn("focusColumn + dx", PANEL)
        self.assertNotIn("else switchView(dx)", PANEL)

    def test_desktop_all_disables_other_source_selection_without_disabling_pins(self) -> None:
        self.assertIn("function desktopAllSelected()", PANEL)
        self.assertIn("function sourceSelectionEnabled(source)", PANEL)
        self.assertIn("source.exclusiveSelection === true) list = [id]", PANEL)
        source_row = PANEL.split("component SourceRow: CursorSurface", 1)[1].split(
            "component ReceiverButton", 1
        )[0]
        self.assertIn("enabled: row.selectionEnabled", source_row)
        self.assertIn("onClicked: root.pinSource(sourceData)", source_row)

    def test_input_pin_control_uses_an_explicit_icon_and_tooltip(self) -> None:
        source_row = PANEL.split("component SourceRow: CursorSurface", 1)[1].split(
            "component ReceiverButton", 1
        )[0]
        self.assertIn(r'iconText: "\uf08d"', source_row)
        self.assertIn(
            'tooltipText: sourceData.pinned ? "Unpin Input" : "Pin Input"',
            source_row,
        )
        self.assertNotIn('text: sourceData.pinned ? "Pinned" : "Pin"', source_row)

    def test_every_stable_click_target_has_an_idle_border(self) -> None:
        stable = PANEL.split("component StableButton: Button", 1)[1]
        self.assertIn("bordered: true", stable)

    def test_section_headers_are_tinted_borderless_bands_not_option_boxes(self) -> None:
        accordion = PANEL.split("component AccordionHeader: Button", 1)[1].split(
            "component SourceRow", 1
        )[0]
        self.assertIn(": Border.none()", accordion)
        self.assertIn("expanded ? 0.10 : 0.035", accordion)
        self.assertIn("fontSize: Style.font.title", accordion)
        self.assertIn("width: parent.width - Style.space(12)", PANEL)

    def test_active_bar_indicator_is_fixed_visible_and_opens_controls(self) -> None:
        self.assertIn("mx-streaming", INDICATOR)
        self.assertIn("mx-capture", INDICATOR)
        self.assertNotIn("receiverLabel", INDICATOR)
        self.assertIn("interactive: sessionActive", INDICATOR)
        self.assertIn("onPressed: function()", INDICATOR)
        self.assertIn('"summon"', INDICATOR)


if __name__ == "__main__":
    unittest.main()
