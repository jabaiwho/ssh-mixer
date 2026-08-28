from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.connections import (
    ConnectionError,
    TrustStore,
    normalize_connection,
    parse_tailscale_status,
    verify_tailscale_peer,
)
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
