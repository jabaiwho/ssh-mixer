import QtQuick
import QtTest
import "../../PanelAlerts.js" as PanelAlerts
import "../../PanelSession.js" as PanelSession

TestCase {
  name: "PanelSessionLifecycle"

  function test_stopped_selection_starts() {
    const command = PanelSession.nextCommand(false, ["chromium"], "both")

    compare(command.action, "start")
    compare(command.payload.destination, "both")
    compare(command.payload.sourceChoiceIds, ["chromium"])
  }

  function test_active_selection_change_stops_before_restarting() {
    const command = PanelSession.nextCommand(true, ["chromium", "spotify"], "ssh", true)

    compare(command.action, "stop")
    compare(command.payload.sourceChoiceIds, ["chromium", "spotify"])
  }

  function test_active_route_change_reuses_persisted_matchers() {
    const stopping = PanelSession.nextCommand(true, ["stale-choice"], "both", true, true)
    const restarting = PanelSession.nextCommand(false, ["stale-choice"], "both", true, true)

    compare(stopping.action, "stop")
    compare(restarting.action, "start")
    compare(restarting.payload.destination, "both")
    verify(!restarting.payload.hasOwnProperty("sourceChoiceIds"))
  }

  function test_stopped_route_change_saves_without_starting_selected_inputs() {
    const command = PanelSession.nextCommand(false, ["stale-choice"], "local", false, true)

    compare(command.action, "selectionSave")
    compare(command.payload.destination, "local")
    verify(!command.payload.hasOwnProperty("sourceChoiceIds"))
  }

  function test_final_deselection_stops_then_saves_empty_selection() {
    const active = PanelSession.nextCommand(true, [], "both", true)
    const stopped = PanelSession.nextCommand(false, [], "both", true)

    compare(active.action, "stop")
    compare(stopped.action, "selectionSave")
    compare(stopped.payload.sourceChoiceIds, [])
  }

  function test_capture_requires_separate_confirmation() {
    verify(PanelSession.requiresCaptureConfirmation(true, false))
    verify(!PanelSession.requiresCaptureConfirmation(true, true))
    verify(!PanelSession.requiresCaptureConfirmation(false, false))
  }

  function test_new_error_reveals_once_until_cleared() {
    const first = PanelAlerts.revealDecision("", "replacement failed", true)
    const unchanged = PanelAlerts.revealDecision(first.revealedError, "replacement failed", true)
    const cleared = PanelAlerts.revealDecision(unchanged.revealedError, "", true)
    const reopened = PanelAlerts.revealDecision("", "replacement failed", true)

    verify(first.scrollTop)
    compare(first.revealedError, "replacement failed")
    verify(!unchanged.scrollTop)
    compare(cleared.revealedError, "")
    verify(reopened.scrollTop)
  }

  function test_error_does_not_scroll_a_closed_panel() {
    const result = PanelAlerts.revealDecision("", "replacement failed", false)

    verify(!result.scrollTop)
    compare(result.revealedError, "replacement failed")
  }

  function test_numbered_sections_are_bounded() {
    const sections = ["sources", "profiles", "receiver", "connection", "privacy", "diagnostics", "removal"]

    compare(PanelSession.numberedSection(1, sections), "sources")
    compare(PanelSession.numberedSection(4, sections), "connection")
    compare(PanelSession.numberedSection(7, sections), "removal")
    compare(PanelSession.numberedSection(0, sections), "")
    compare(PanelSession.numberedSection(8, sections), "")
  }
}
