from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ssh_mixer.config import DEFAULT_RECEIVER_COMMAND, config_from_payload, lock_path, state_path
from ssh_mixer.diagnostics import DiagnosticStore
from ssh_mixer.session import (
    SessionError,
    SessionWorker,
    cleanup_resources,
    process_identity,
    process_matches_identity,
    session_lock,
    ssh_base_command,
    start_session,
    stopped_state,
    test_connection,
    write_state,
)
import ssh_mixer.session as session_module



class SessionSecurityTest(unittest.TestCase):
    def test_stream_pipeline_processes_are_detached_and_headless(self) -> None:
        ffmpeg = Mock(pid=10, stdout=Mock(), stderr=Mock())
        ssh = Mock(pid=11)
        resolved = {
            "bitrate": "128k",
            "receiverCommand": DEFAULT_RECEIVER_COMMAND,
        }

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"XDG_STATE_HOME": str(Path(temp) / "state")},
            clear=False,
        ):
            worker = SessionWorker({}, "test-session")
            with patch("ssh_mixer.session.require_commands"), patch(
                "ssh_mixer.session.resolve_remote", return_value=resolved
            ), patch(
                "ssh_mixer.session.ssh_base_command", return_value=["ssh"]
            ), patch(
                "ssh_mixer.session.os.set_blocking"
            ), patch(
                "ssh_mixer.session.process_identity",
                side_effect=[
                    {"pid": 10, "startTimeTicks": "1", "executable": "/usr/bin/ffmpeg"},
                    {"pid": 11, "startTimeTicks": "2", "executable": "/usr/bin/ssh"},
                ],
            ), patch(
                "ssh_mixer.session.subprocess.Popen", side_effect=[ffmpeg, ssh]
            ) as popen:
                worker._start_pipeline("mix.monitor", {})

        encoder_launch = popen.call_args_list[0]
        transport_launch = popen.call_args_list[1]
        self.assertIs(encoder_launch.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(encoder_launch.kwargs["stdout"], subprocess.PIPE)
        self.assertIs(encoder_launch.kwargs["stderr"], subprocess.PIPE)
        self.assertTrue(encoder_launch.kwargs["start_new_session"])
        self.assertIs(transport_launch.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(transport_launch.kwargs["stderr"], subprocess.PIPE)
        self.assertTrue(transport_launch.kwargs["start_new_session"])
        self.assertNotIn("shell", encoder_launch.kwargs)
        self.assertNotIn("shell", transport_launch.kwargs)

    def test_transport_failure_surfaces_bounded_receiver_protocol_message(self) -> None:
        worker = SessionWorker({}, "test-session")
        ffmpeg = Mock(pid=10, stderr=object())
        ffmpeg.poll.return_value = None
        ssh = Mock(pid=11, stderr=object())
        ssh.poll.return_value = 1
        worker.children = [ffmpeg, ssh]
        receiver_error = (
            '#< CLIXML\n'
            '{"ok":false,"message":"ffplay.exe is unavailable or not executable"}\n'
        )

        with patch(
            "ssh_mixer.session.read_encoder_diagnostics",
            side_effect=lambda stream: receiver_error if stream is ssh.stderr else "",
        ):
            result = worker._wait_for_pipeline()

        self.assertEqual(
            result,
            (1, "ffplay.exe is unavailable or not executable", ""),
        )

    def test_failed_refresh_never_records_replacement_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"XDG_STATE_HOME": str(Path(temp) / "state")},
            clear=False,
        ):
            worker = SessionWorker({}, "test-session")
            worker.diagnostics.record = Mock()
            with patch.object(
                worker,
                "_start_pipeline",
                side_effect=SessionError("replacement failed"),
            ), patch.object(
                worker, "_wait_for_pipeline", return_value=(0, "", "deadline")
            ), patch.object(worker, "_terminate_children"), patch.object(
                worker, "_forget_pipeline_processes"
            ):
                with self.assertRaisesRegex(SessionError, "replacement failed"):
                    worker._run_pipeline_epochs("mix.monitor", {})

        codes = [call.kwargs["code"] for call in worker.diagnostics.record.call_args_list]
        self.assertEqual(codes, ["refresh-deadline-starting"])

    def test_failed_replacement_launch_terminates_the_partial_pipeline(self) -> None:
        worker = SessionWorker({}, "test-session")
        ffmpeg = Mock(pid=10, stdout=Mock(), stderr=Mock())
        ffmpeg.poll.return_value = None
        resolved = {
            "bitrate": "128k",
            "receiverCommand": DEFAULT_RECEIVER_COMMAND,
        }

        with patch("ssh_mixer.session.require_commands"), patch(
            "ssh_mixer.session.resolve_remote", return_value=resolved
        ), patch("ssh_mixer.session.ssh_base_command", return_value=["ssh"]), patch(
            "ssh_mixer.session.os.set_blocking"
        ), patch(
            "ssh_mixer.session.subprocess.Popen",
            side_effect=[ffmpeg, OSError("replacement failed")],
        ), patch("ssh_mixer.session.kill_process_group") as kill:
            with self.assertRaisesRegex(OSError, "replacement failed"):
                worker._start_pipeline("mix.monitor", {})

        kill.assert_called_once_with(10, session_module.signal.SIGTERM)

    def test_session_requests_refresh_when_silence_follows_fifteen_minutes(self) -> None:
        worker = SessionWorker({}, "test-session")
        ffmpeg = Mock(pid=10, stderr=object())
        ffmpeg.poll.side_effect = [None, 0]
        ssh = Mock(pid=11)
        ssh.poll.return_value = None
        worker.children = [ffmpeg, ssh]

        with patch(
            "ssh_mixer.session.read_encoder_diagnostics",
            return_value="[silencedetect] silence_start: 12.5\n",
            create=True,
        ), patch("ssh_mixer.session.time.monotonic", side_effect=[100.0, 1_000.0]), patch(
            "ssh_mixer.session.time.sleep"
        ):
            result = worker._wait_for_pipeline()

        self.assertEqual(result, (0, "", "silence"))

    def test_qualifying_silence_is_not_missed_when_audio_resumes_between_polls(self) -> None:
        worker = SessionWorker({}, "test-session")
        ffmpeg = Mock(pid=10, stderr=object())
        ffmpeg.poll.side_effect = [None, 0]
        ssh = Mock(pid=11)
        ssh.poll.return_value = None
        worker.children = [ffmpeg, ssh]
        events = (
            "[silencedetect] silence_start: 12.5\n"
            "[silencedetect] silence_end: 13.6 | silence_duration: 1.1\n"
        )

        with patch(
            "ssh_mixer.session.read_encoder_diagnostics", return_value=events
        ), patch("ssh_mixer.session.time.monotonic", side_effect=[100.0, 1_000.0]), patch(
            "ssh_mixer.session.time.sleep"
        ):
            result = worker._wait_for_pipeline()

        self.assertEqual(result, (0, "", "silence"))

    def test_cleanup_changes_only_resources_still_owned_by_the_session(self) -> None:
        calls: list[list[str]] = []

        def pactl(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            outputs = {
                ("list", "sinks"): "Sink #20\n    Name: ssh_mixer_mix\nSink #30\n    Name: other\n",
                ("list", "sink-inputs"): "Sink Input #100\n    Sink: 20\nSink Input #101\n    Sink: 30\n",
                ("list", "modules"): (
                    "Module #5\n    Name: module-null-sink\n    Argument: sink_name=ssh_mixer_mix\n"
                    "Module #7\n    Name: module-null-sink\n    Argument: sink_name=unrelated\n"
                ),
            }
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=outputs.get(tuple(args), ""),
                stderr="",
            )

        state = {
            "resources": {
                "mixSink": "ssh_mixer_mix",
                "movedSinkInputs": [
                    {"sinkInputId": "100", "originalSink": "original"},
                    {"sinkInputId": "101", "originalSink": "original"},
                ],
                "modules": [{"id": "5"}, {"id": "7"}],
            }
        }
        with patch("ssh_mixer.session.run_pactl", side_effect=pactl):
            cleanup_resources(state)

        self.assertIn(["move-sink-input", "100", "original"], calls)
        self.assertNotIn(["move-sink-input", "101", "original"], calls)
        self.assertIn(["unload-module", "5"], calls)
        self.assertNotIn(["unload-module", "7"], calls)

    def test_reused_pid_does_not_match_a_tracked_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            proc_root = Path(temp)
            process_dir = proc_root / "4321"
            process_dir.mkdir()
            stat = process_dir / "stat"
            stat.write_text(
                "4321 (ssh) " + " ".join(["S", *(["0"] * 18), "100", "0"]),
                encoding="utf-8",
            )
            (process_dir / "exe").symlink_to("/usr/bin/ssh")
            tracked = process_identity(4321, proc_root=proc_root)

            stat.write_text(
                "4321 (ssh) " + " ".join(["S", *(["0"] * 18), "200", "0"]),
                encoding="utf-8",
            )

            self.assertIsNotNone(tracked)
            self.assertFalse(process_matches_identity(tracked or {}, proc_root=proc_root))

    def test_unreadable_worker_identity_is_not_treated_as_a_match(self) -> None:
        with patch("ssh_mixer.session.pid_alive", return_value=True), patch.object(
            Path, "read_text", side_effect=OSError("unreadable")
        ):
            matched = session_module.pid_matches_session(4321, "session-id")

        self.assertFalse(matched)

    def test_direct_ssh_ignores_user_config_and_enforces_safe_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"XDG_DATA_HOME": str(Path(temp) / "data")},
            clear=False,
        ):
            key = Path(temp) / "identity"
            key.write_text("test identity", encoding="utf-8")

            command = ssh_base_command(
                {
                    "host": "receiver.example",
                    "user": "listener",
                    "keyPath": str(key),
                    "port": 2222,
                    "connectTimeoutSeconds": 5,
                }
            )

        rendered = " ".join(command)
        self.assertEqual(command[1:3], ["-F", "/dev/null"])
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("ClearAllForwardings=yes", command)
        self.assertIn("PermitLocalCommand=no", command)
        self.assertIn("RequestTTY=no", command)
        self.assertIn("PasswordAuthentication=no", command)
        self.assertIn("--", command)
        self.assertNotIn("ProxyCommand", rendered)

    def test_receiver_command_cannot_be_replaced_with_arbitrary_shell_text(self) -> None:
        configured = config_from_payload(
            {"remote": {"receiverCommand": "touch /tmp/not-allowed"}},
            base={"schemaVersion": 1},
        )
        self.assertEqual(
            configured["remote"]["receiverCommand"], DEFAULT_RECEIVER_COMMAND
        )

    def test_ssh_destination_cannot_be_interpreted_as_an_option(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "identity"
            key.write_text("test identity", encoding="utf-8")

            with self.assertRaises(session_module.SessionError):
                ssh_base_command(
                    {
                        "host": "receiver.example",
                        "user": "-oProxyCommand=/usr/bin/false",
                        "keyPath": str(key),
                        "connectTimeoutSeconds": 5,
                    }
                )

    def test_connection_failure_is_structured_and_redacted(self) -> None:
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
            key = Path(temp) / "identity"
            key.write_text("test identity", encoding="utf-8")
            failed = subprocess.CompletedProcess(
                args=["ssh"],
                returncode=255,
                stdout="",
                stderr="listener@receiver.example refused /tmp/identity",
            )
            with patch("ssh_mixer.session.subprocess.run", return_value=failed):
                result = test_connection(
                    {
                        "host": "receiver.example",
                        "user": "listener",
                        "keyPath": str(key),
                        "connectTimeoutSeconds": 5,
                    }
                )

            persisted = "".join(
                path.read_text(encoding="utf-8")
                for path in (Path(temp) / "state" / "ssh-mixer" / "logs").glob("*.jsonl")
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["diagnostic"]["stage"], "connection.preflight")
        self.assertNotIn("listener", result["error"])
        self.assertNotIn("receiver.example", persisted)
        self.assertNotIn(str(key), persisted)
        self.assertNotIn("/tmp/identity", persisted)

    def test_protocol_negotiation_keeps_compatible_v1_and_guides_incompatible_helpers(self) -> None:
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
            key = Path(temp) / "identity"
            key.write_text("test identity", encoding="utf-8")
            remote = {
                "host": "receiver.example",
                "user": "listener",
                "keyPath": str(key),
                "connectTimeoutSeconds": 5,
            }
            compatible = subprocess.CompletedProcess(
                args=["ssh"],
                returncode=0,
                stdout='{"protocol":"v1","platform":"linux"}',
                stderr="",
            )
            incompatible = subprocess.CompletedProcess(
                args=["ssh"],
                returncode=0,
                stdout='{"protocol":"v2","protocolVersion":2,"platform":"linux"}',
                stderr="",
            )
            with patch("ssh_mixer.session.subprocess.run", return_value=compatible):
                accepted = test_connection(remote)
            with patch("ssh_mixer.session.subprocess.run", return_value=incompatible):
                rejected = test_connection(remote)

        self.assertTrue(accepted["ok"])
        self.assertEqual(accepted["capabilities"]["protocolVersion"], 1)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["diagnostic"]["code"], "protocol-incompatible")
        self.assertIn("signed compatible Receiver update", rejected["error"])

    def test_runtime_state_lock_and_logs_are_private(self) -> None:
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
            write_state(stopped_state())
            store = DiagnosticStore(Path(temp) / "state" / "ssh-mixer" / "logs")
            store.record(
                stage="session.start",
                code="test",
                message="test",
                session_id="test-session",
            )
            log_path = next(store.directory.glob("*.jsonl"))
            with session_lock():
                pass

            for path in (state_path(), log_path, lock_path()):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600, str(path))

    def test_detached_worker_configuration_is_not_exposed_in_argv(self) -> None:
        config = {
            "schemaVersion": 1,
            "sourceIds": [],
            "destination": "local",
            "remote": {
                "host": "private-receiver.example",
                "user": "private-user",
                "keyPath": "/private/identity",
            },
        }
        stopped = {"state": "stopped", "active": False, "resources": {}}
        streaming = {
            "state": "local",
            "active": True,
            "sessionId": "fixed-session",
            "resources": {},
        }
        process = Mock()
        process.poll.return_value = None
        inherited_fds: list[int] = []

        def launch_worker(*_args: object, **kwargs: object) -> Mock:
            pass_fds = kwargs.get("pass_fds", ())
            inherited_fds.extend(os.dup(fd) for fd in pass_fds)
            return process

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ), patch("ssh_mixer.session.require_lifecycle_monitor"), patch(
            "ssh_mixer.session.require_commands"
        ), patch(
            "ssh_mixer.session.cleanup_resources"
        ), patch("ssh_mixer.session.cleanup_named_mix"), patch(
            "ssh_mixer.session.normalize_status", side_effect=[stopped, streaming]
        ), patch("ssh_mixer.session.time.sleep"), patch(
            "ssh_mixer.session.uuid.uuid4", return_value=SimpleNamespace(hex="fixed-session")
        ), patch("ssh_mixer.session.subprocess.Popen", side_effect=launch_worker) as popen:
            result = start_session(config)

        for fd in inherited_fds:
            os.close(fd)
        command = popen.call_args.args[0]
        command_text = " ".join(map(str, command))
        self.assertEqual(result["sessionId"], "fixed-session")
        self.assertIn("--config-fd", command)
        self.assertNotIn("--json", command)
        self.assertNotIn("private-receiver.example", command_text)
        self.assertNotIn("private-user", command_text)
        self.assertNotIn("/private/identity", command_text)


if __name__ == "__main__":
    unittest.main()
