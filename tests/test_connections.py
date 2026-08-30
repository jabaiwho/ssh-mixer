from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.bootstrap import LinuxBootstrap
from ssh_mixer.connections import (
    ConnectionError,
    TrustStore,
    connection_id,
    normalize_connection,
    parse_tailscale_status,
    verify_tailscale_peer,
)
from ssh_mixer.config import config_from_payload, save_config
from ssh_mixer.diagnostics import DiagnosticStore


def host_key(host: str, material: bytes) -> str:
    encoded = base64.b64encode(material).decode("ascii")
    return f"{host} ssh-ed25519 {encoded}"


class ConnectionTest(unittest.TestCase):
    def test_discovers_online_tailscale_peers_without_hostname_assumptions(self) -> None:
        status = {
            "Peer": {
                "node-key": {
                    "ID": "peer-id",
                    "HostName": "music-mac",
                    "DNSName": "music-mac.example.ts.net.",
                    "TailscaleIPs": ["100.70.80.90", "fd7a:115c:a1e0::10"],
                    "Online": True,
                },
                "offline-key": {
                    "ID": "offline-id",
                    "HostName": "ordinary-name",
                    "TailscaleIPs": ["100.1.2.3"],
                    "Online": False,
                },
            }
        }

        peers = parse_tailscale_status(json.dumps(status))

        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["id"], "peer-id")
        self.assertEqual(peers[0]["host"], "music-mac.example.ts.net")
        self.assertEqual(peers[0]["addresses"], ["100.70.80.90", "fd7a:115c:a1e0::10"])

    def test_tailscale_connection_requires_resolution_to_the_selected_peer(self) -> None:
        connection = normalize_connection(
            {
                "type": "tailscale",
                "peerId": "peer-id",
                "host": "music-mac.example.ts.net",
                "user": "listener",
                "port": 22,
            }
        )
        peers = [
            {
                "id": "peer-id",
                "host": "music-mac.example.ts.net",
                "addresses": ["100.70.80.90"],
                "online": True,
            }
        ]

        verified = verify_tailscale_peer(
            connection,
            peers,
            resolve=lambda _host: ["100.70.80.90"],
        )

        self.assertEqual(verified["address"], "100.70.80.90")
        with self.assertRaises(ConnectionError):
            verify_tailscale_peer(
                connection,
                peers,
                resolve=lambda _host: ["203.0.113.10"],
            )

    def test_rejects_values_that_could_become_ssh_options(self) -> None:
        invalid = (
            {"type": "direct", "host": "receiver", "user": "-oProxyCommand=false"},
            {"type": "direct", "host": "receiver name", "user": "listener"},
            {"type": "direct", "host": "-receiver", "user": "listener"},
            {"type": "direct", "host": "receiver", "user": "listener\nother"},
            {"type": "direct", "host": "receiver", "user": "listener", "port": 70000},
        )

        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ConnectionError):
                normalize_connection(value)

    def test_receiver_can_be_renamed_without_changing_connection_identity(self) -> None:
        connection = normalize_connection(
            {
                "type": "direct",
                "host": "receiver.example",
                "user": "listener",
                "receiverName": "Receiver",
            }
        )
        original_id = connection_id(connection)
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
            save_config(config_from_payload({"connection": connection}))
            app = MixerApplication(
                discover_sources=lambda: [],
                read_status=lambda: {"state": "stopped", "active": False},
                discover_tailscale_peers=lambda: [],
                discover_profiles=lambda: [],
            )
            renamed = app.execute(
                {
                    "operation": "connection.rename",
                    "payload": {
                        "connectionId": original_id,
                        "receiverName": "Gaming PC",
                    },
                }
            )
            inspected = app.execute({"operation": "inspect"})

        self.assertTrue(renamed["ok"])
        self.assertEqual(renamed["connectionId"], original_id)
        self.assertEqual(renamed["connection"]["receiverName"], "Gaming PC")
        self.assertEqual(connection_id(renamed["connection"]), original_id)
        self.assertEqual(inspected["config"]["schemaVersion"], 4)
        self.assertEqual(len(inspected["config"]["connections"]), 1)
        self.assertEqual(
            inspected["config"]["connections"][0]["receiverName"], "Gaming PC"
        )

    def test_user_can_select_which_saved_connection_receives_the_next_session(self) -> None:
        first = normalize_connection(
            {
                "type": "direct",
                "host": "first.example",
                "user": "listener",
                "receiverName": "Office PC",
            }
        )
        second = normalize_connection(
            {
                "type": "direct",
                "host": "second.example",
                "user": "listener",
                "receiverName": "Gaming PC",
            }
        )
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
            save_config(
                config_from_payload(
                    {"connection": first, "connections": [first, second]}
                )
            )
            app = MixerApplication(
                discover_sources=lambda: [],
                read_status=lambda: {"state": "stopped", "active": False},
                discover_tailscale_peers=lambda: [],
                discover_profiles=lambda: [],
            )
            selected = app.execute(
                {
                    "operation": "connection.select",
                    "payload": {"connectionId": connection_id(second)},
                }
            )

        self.assertTrue(selected["ok"])
        self.assertEqual(selected["connection"]["receiverName"], "Gaming PC")
        self.assertEqual(selected["config"]["remote"]["host"], "second.example")
        self.assertEqual(len(selected["config"]["connections"]), 2)

    def test_application_approves_only_the_fingerprints_the_user_saw(self) -> None:
        connection = normalize_connection(
            {"type": "direct", "host": "receiver.example", "user": "listener"}
        )
        candidates = [[host_key("receiver.example", b"first-key")]]
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
            root = Path(temp)
            app = MixerApplication(
                discover_sources=lambda: [],
                discover_tailscale_peers=lambda: [],
                scan_host_keys=lambda _connection, **_kwargs: candidates[0],
                diagnostic_store=DiagnosticStore(root / "logs"),
                trust_store=TrustStore(root / "trust"),
            )
            inspected = app.execute(
                {"operation": "trust.inspect", "payload": {"connection": connection}}
            )
            fingerprints = inspected["trust"]["candidateFingerprints"]
            approved = app.execute(
                {
                    "operation": "trust.approve",
                    "payload": {
                        "connection": connection,
                        "expectedFingerprints": fingerprints,
                    },
                }
            )
            saved = app.execute(
                {"operation": "connection.save", "payload": {"connection": connection}}
            )
            candidates[0] = [host_key("receiver.example", b"replacement-key")]
            rejected = app.execute(
                {
                    "operation": "trust.approve",
                    "payload": {
                        "connection": connection,
                        "expectedFingerprints": fingerprints,
                    },
                }
            )

        self.assertEqual(inspected["trust"]["status"], "unknown")
        self.assertEqual(approved["trust"]["status"], "trusted")
        self.assertTrue(saved["ok"])
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["diagnostic"]["code"], "candidate-changed")

    def test_known_hosts_binds_approved_keys_to_session_and_bootstrap_aliases(self) -> None:
        connection = normalize_connection(
            {"type": "direct", "host": "receiver.example", "user": "listener", "port": 22}
        )
        approved = [host_key("receiver.example", b"approved-key")]

        commands: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            output = (
                "platform=linux\nuser=listener\nhome=/home/listener\n"
                "command.python3=true\ncommand.ffplay=true\n"
                "command.apt-get=false\ncommand.dnf=false\n"
                "command.pacman=false\ncommand.zypper=false\n"
            )
            return subprocess.CompletedProcess(command, 0, output, "")

        with tempfile.TemporaryDirectory() as temp:
            store = TrustStore(Path(temp))
            store.approve(connection, approved)
            LinuxBootstrap(
                connection,
                known_hosts=store.known_hosts_path,
                runner=runner,
            ).probe()
            rendered = [
                line.split()
                for line in store.known_hosts_path.read_text(encoding="utf-8").splitlines()
            ]

        requested_alias = next(
            value.removeprefix("HostKeyAlias=")
            for value in commands[0]
            if value.startswith("HostKeyAlias=")
        )
        self.assertEqual(requested_alias, connection_id(connection))
        self.assertEqual(
            {parts[0] for parts in rendered},
            {"receiver.example", requested_alias},
        )
        self.assertEqual(len(rendered), 2)
        self.assertEqual({tuple(parts[1:]) for parts in rendered}, {tuple(approved[0].split()[1:])})

    def test_host_trust_requires_approval_and_detects_replacement(self) -> None:
        connection = normalize_connection(
            {"type": "direct", "host": "receiver.example", "user": "listener", "port": 22}
        )
        first = [host_key("receiver.example", b"first-key")]
        replacement = [host_key("receiver.example", b"replacement-key")]

        with tempfile.TemporaryDirectory() as temp:
            store = TrustStore(Path(temp))
            unknown = store.inspect(connection, first)
            store.approve(connection, first)
            trusted = store.inspect(connection, first)
            changed = store.inspect(connection, replacement)

            self.assertEqual(unknown["status"], "unknown")
            self.assertEqual(trusted["status"], "trusted")
            self.assertEqual(changed["status"], "changed")
            self.assertNotEqual(changed["approvedFingerprints"], changed["candidateFingerprints"])
            self.assertEqual(store.known_hosts_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(temp).stat().st_mode & 0o777, 0o700)
            self.assertNotIn("first-key", store.known_hosts_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
