from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.diagnostics import DiagnosticStore
from ssh_mixer.audio import matcher_for_source
from ssh_mixer.lifecycle import (
    handle_lifecycle_event,
    indicator_heartbeat_path,
    indicator_status,
    lifecycle_heartbeat_path,
    lifecycle_monitor_ready,
    privacy_services_ready,
    write_indicator_heartbeat,
    write_lifecycle_heartbeat,
)
from ssh_mixer.session import (
    SessionError,
    SessionWorker,
    read_state,
    require_lifecycle_monitor,
)


ACTIVE_PLAYBACK = {
    "state": "streaming",
    "active": True,
    "captureActive": False,
    "selectedInputs": [{"type": "playback"}],
}
ACTIVE_CAPTURE = {
    "state": "streaming",
    "active": True,
    "captureActive": True,
    "selectedInputs": [{"type": "playback"}, {"type": "capture"}],
}


class LifecyclePolicyTest(unittest.TestCase):
    def test_session_start_fails_closed_without_indicator_and_lifecycle_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ):
            with self.assertRaises(SessionError):
                require_lifecycle_monitor()

    def test_protected_fresh_heartbeat_is_required_for_session_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ), patch("ssh_mixer.lifecycle.time.time", return_value=100.0):
            from ssh_mixer.config import ensure_dirs

            ensure_dirs()
            write_lifecycle_heartbeat()
            write_indicator_heartbeat()
            heartbeat = lifecycle_heartbeat_path()
            indicator_heartbeat = indicator_heartbeat_path()
            ready = privacy_services_ready(now=104.0)
            stale = lifecycle_monitor_ready(now=111.0)
            heartbeat_mode = heartbeat.stat().st_mode & 0o777
            indicator_mode = indicator_heartbeat.stat().st_mode & 0o777

        self.assertTrue(ready)
        self.assertFalse(stale)
        self.assertEqual(heartbeat_mode, 0o600)
        self.assertEqual(indicator_mode, 0o600)

    def test_session_refreshes_pipeline_after_streaming_without_reapplying_routing(self) -> None:
        source = {
            "id": "sink-input:7",
            "type": "playback",
            "pulseId": "7",
            "name": "player.node",
            "applicationName": "Player",
            "processBinary": "player",
            "label": "Player",
            "sinkName": "output",
        }

        class ObservingWorker(SessionWorker):
            launch_states: list[str] = []
            operations: list[str] = []
            pipeline_events: list[str] = []
            wait_count = 0
            refresh_terminations = 0

            def _apply_operation(self, operation: dict) -> None:
                self.operations.append(str(operation.get("op", "")))

            def _start_pipeline(self, capture_source: str, remote: dict) -> None:
                self.launch_states.append(str(self.current_state.get("state", "")))
                self.pipeline_events.append("start")

            def _wait_for_pipeline(self) -> tuple[int, str, str]:
                self.pipeline_events.append("wait")
                self.wait_count += 1
                if self.wait_count == 1:
                    return 0, "", "silence"
                return 0, "", ""

            def _terminate_children(self) -> None:
                self.pipeline_events.append("terminate")
                self.refresh_terminations += 1

            def _forget_pipeline_processes(self) -> None:
                self.pipeline_events.append("forget")

            def _cleanup(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ), patch("ssh_mixer.session.signal.signal"), patch(
            "ssh_mixer.session.require_commands"
        ), patch(
            "ssh_mixer.session.discover_sources", return_value=[source]
        ), patch(
            "ssh_mixer.session.default_sink_name", return_value="output"
        ):
            worker = ObservingWorker(
                {
                    "sourceIds": ["sink-input:7"],
                    "sourceMatchers": [matcher_for_source(source)],
                    "destination": "ssh",
                    "remote": {},
                },
                "observed-session",
            )
            worker.run()

        self.assertEqual(worker.launch_states, ["starting", "streaming"])
        self.assertEqual(
            worker.operations,
            ["load-null-sink", "move-sink-input"],
        )
        self.assertEqual(worker.refresh_terminations, 1)
        self.assertEqual(
            worker.pipeline_events,
            ["start", "wait", "terminate", "forget", "start", "wait"],
        )

    def test_fatal_receiver_pipeline_exit_cleans_up_and_stays_stopped(self) -> None:
        source = {
            "id": "sink-input:7",
            "type": "playback",
            "pulseId": "7",
            "name": "player.node",
            "applicationName": "Player",
            "processBinary": "player",
            "label": "Player",
            "sinkName": "output",
        }

        class FailingWorker(SessionWorker):
            cleaned = False

            def _apply_operation(self, operation: dict) -> None:
                return None

            def _start_pipeline(self, capture_source: str, remote: dict) -> None:
                return None

            def _wait_for_pipeline(self) -> tuple[int, str, str]:
                return 255, "receiver disconnected", ""

            def _cleanup(self) -> None:
                self.cleaned = True

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ), patch("ssh_mixer.session.signal.signal"), patch(
            "ssh_mixer.session.require_commands"
        ), patch(
            "ssh_mixer.session.discover_sources", return_value=[source]
        ), patch(
            "ssh_mixer.session.default_sink_name", return_value="output"
        ):
            worker = FailingWorker(
                {
                    "sourceIds": ["sink-input:7"],
                    "sourceMatchers": [matcher_for_source(source)],
                    "destination": "ssh",
                    "remote": {},
                },
                "failing-session",
            )
            exit_code = worker.run()
            final = read_state()

        self.assertEqual(exit_code, 255)
        self.assertTrue(worker.cleaned)
        self.assertFalse(final["active"])
        self.assertEqual(final["state"], "error")

    def test_application_lifecycle_seam_stops_capture_and_exposes_safe_indicator(self) -> None:
        stops: list[str] = []
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ):
            app = MixerApplication(
                read_status=lambda: ACTIVE_CAPTURE,
                stop_session=lambda reason="": stops.append(reason)
                or {"state": "stopped", "active": False},
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            indicator = app.execute({"operation": "indicator.inspect"})
            locked = app.execute(
                {"operation": "lifecycle.event", "payload": {"event": "lock"}}
            )

        self.assertTrue(indicator["active"])
        self.assertTrue(indicator["capture"])
        self.assertEqual(indicator["receiverLabel"], "")
        self.assertEqual(locked["action"], "stopped")
        self.assertEqual(stops, ["capture-screen-lock"])

    def apply(
        self,
        event: str,
        *,
        status: dict = ACTIVE_PLAYBACK,
        lock_behavior: str = "stop-all",
    ) -> tuple[dict, list[str]]:
        stopped: list[str] = []
        result = handle_lifecycle_event(
            event,
            status_reader=lambda: status,
            config_loader=lambda: {
                "privacy": {
                    "lockBehavior": lock_behavior,
                    "showReceiverLabel": False,
                }
            },
            stopper=lambda reason="": stopped.append(reason)
            or {"state": "stopped", "active": False, "stopReason": reason},
        )
        return result, stopped

    def test_lock_defaults_to_stop_all(self) -> None:
        result, stopped = self.apply("lock")

        self.assertEqual(result["action"], "stopped")
        self.assertEqual(stopped, ["screen-lock"])

    def test_continue_playback_lock_policy_keeps_only_non_capture_session(self) -> None:
        result, stopped = self.apply(
            "lock", lock_behavior="continue-playback"
        )

        self.assertEqual(result["action"], "continued-playback")
        self.assertEqual(stopped, [])

    def test_capture_always_stops_on_lock_even_when_playback_may_continue(self) -> None:
        result, stopped = self.apply(
            "lock",
            status=ACTIVE_CAPTURE,
            lock_behavior="continue-playback",
        )

        self.assertEqual(result["action"], "stopped")
        self.assertEqual(result["reason"], "capture-stops-on-lock")
        self.assertEqual(stopped, ["capture-screen-lock"])

    def test_suspend_logout_disconnect_and_fatal_network_loss_stop(self) -> None:
        for event in (
            "suspend",
            "logout",
            "receiver-disconnect",
            "fatal-network-loss",
            "privacy-monitor-failure",
        ):
            with self.subTest(event=event):
                result, stopped = self.apply(event)
                self.assertEqual(result["action"], "stopped")
                self.assertEqual(stopped, [event])

    def test_unlock_wake_reconnection_login_discovery_and_panel_open_never_start(self) -> None:
        for event in (
            "unlock",
            "wake",
            "network-reconnected",
            "login",
            "discovery",
            "panel-open",
        ):
            with self.subTest(event=event):
                result, stopped = self.apply(event)
                self.assertEqual(result["action"], "none")
                self.assertFalse(result["automaticStart"])
                self.assertEqual(stopped, [])

    def test_active_indicator_uses_fixed_non_identifying_labels(self) -> None:
        hidden = indicator_status(
            ACTIVE_CAPTURE,
            {
                "privacy": {"showReceiverLabel": False},
                "remote": {"host": "private-receiver.example"},
            },
        )
        shown = indicator_status(
            ACTIVE_PLAYBACK,
            {
                "privacy": {"showReceiverLabel": True},
                "connection": {
                    "receiverName": "Gaming PC",
                    "host": "gaming-pc.tailnet-name.ts.net",
                },
                "remote": {"host": "gaming-pc.tailnet-name.ts.net"},
            },
        )

        self.assertTrue(hidden["active"])
        self.assertTrue(hidden["capture"])
        self.assertEqual(hidden["kind"], "recording")
        self.assertEqual(hidden["displayLabel"], "mx-capture")
        self.assertEqual(hidden["receiverLabel"], "")
        self.assertEqual(shown["kind"], "playback")
        self.assertEqual(shown["displayLabel"], "mx-streaming")
        self.assertEqual(shown["receiverLabel"], "")
        self.assertNotIn("Gaming PC", str(shown))
        self.assertNotIn("ts.net", str(shown))

    def test_indicator_never_uses_host_as_its_label(self) -> None:
        for host in ("gaming-pc.tailnet-name.ts.net", "100.72.18.44"):
            with self.subTest(host=host):
                result = indicator_status(
                    ACTIVE_PLAYBACK,
                    {
                        "privacy": {"showReceiverLabel": True},
                        "remote": {"host": host},
                    },
                )
                self.assertEqual(result["displayLabel"], "mx-streaming")
                self.assertEqual(result["receiverLabel"], "")
                self.assertNotIn(host, str(result))

    def test_inactive_indicator_never_reveals_receiver_label(self) -> None:
        result = indicator_status(
            {"state": "stopped", "active": False},
            {
                "privacy": {"showReceiverLabel": True},
                "remote": {"host": "private-receiver.example"},
            },
        )

        self.assertFalse(result["active"])
        self.assertEqual(result["displayLabel"], "")
        self.assertEqual(result["receiverLabel"], "")


if __name__ == "__main__":
    unittest.main()
