from __future__ import annotations

import base64
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.cli import main
from ssh_mixer.config import (
    config_path,
    ensure_dirs,
    load_config,
    save_config,
    secure_write_text,
    trust_dir,
)
from ssh_mixer.connections import TrustStore, connection_id


class TailscaleRenameTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_STATE_HOME": str(self.root / "state"),
                "XDG_RUNTIME_DIR": str(self.root / "runtime"),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        ensure_dirs()
        self.key = self.root / "fixture-identity"
        self.key.write_text("not a real private key", encoding="utf-8")
        self.key.chmod(0o600)
        self.connection = {
            "type": "tailscale",
            "peerId": "stable-peer",
            "host": "old-name.example.ts.net",
            "user": "listener",
            "port": 22,
            "securityLevel": "receiver-only",
            "managedIdentityId": "a" * 64,
            "receiverPlatform": "windows",
            "receiverName": "Gaming PC",
        }
        self.original_id = connection_id(self.connection)
        self.approved_key = base64.b64encode(b"approved-host-key").decode("ascii")
        self.store = TrustStore(trust_dir())
        self.store.approve(
            self.connection,
            [f"old-name.example.ts.net ssh-ed25519 {self.approved_key}"],
        )
        # Seed an older saved configuration, without any rename metadata.
        secure_write_text(
            config_path(),
            json.dumps(
                {
                    "schemaVersion": 4,
                    "connection": self.connection,
                    "connections": [
                        self.connection,
                        {"type": "direct", "host": "other.example", "user": "listener"},
                    ],
                    "mixProfiles": [
                        {
                            "id": "profile-fixture",
                            "name": "Desk mix",
                            "connection": self.connection,
                        }
                    ],
                    "remote": {"keyPath": str(self.key)},
                }
            ),
        )
        self.peer = {
            "ID": "stable-peer",
            "HostName": "new-name",
            "DNSName": "new-name.example.ts.net.",
            "TailscaleIPs": ["100.70.80.90"],
            "Online": True,
        }
        self.resolved_address = "100.70.80.90"
        self.additional_peers: list[dict] = []
        self.ssh_exit = 0
        self.commands: list[list[str]] = []
        for boundary in (
            patch("subprocess.run", side_effect=self.run_command),
            patch("socket.getaddrinfo", side_effect=self.resolve),
            patch("shutil.which", return_value="/usr/bin/fixture"),
        ):
            boundary.start()
            self.addCleanup(boundary.stop)

    def resolve(self, host: str, *_args: object, **_kwargs: object) -> list[tuple]:
        if host == self.peer["DNSName"].rstrip("."):
            address = self.resolved_address
        else:
            peer = next(
                (item for item in self.additional_peers if host == item["DNSName"].rstrip(".")),
                None,
            )
            if peer is None:
                raise socket.gaierror("obsolete name must not be resolved")
            address = peer["TailscaleIPs"][0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    def run_command(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command == ["tailscale", "status", "--json"]:
            peers = {
                str(index): peer
                for index, peer in enumerate([self.peer, *self.additional_peers])
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"Peer": peers}), ""
            )
        self.assertEqual(command[0], "ssh", "unexpected external operation")
        self.commands.append(command)
        return subprocess.CompletedProcess(
            command,
            self.ssh_exit,
            json.dumps({"protocolVersion": 1, "platform": "windows", "helperVersion": "1.1.2"}),
            "Host key verification failed" if self.ssh_exit else "",
        )

    def preflight(self) -> tuple[int, dict]:
        output = io.StringIO()
        with patch.object(sys, "argv", ["ssh-mixer", "test-connection"]), redirect_stdout(output):
            code = main()
        return code, json.loads(output.getvalue())

    def inspect(self) -> dict:
        return MixerApplication(
            discover_sources=lambda: [],
            read_status=lambda: {"active": False, "state": "stopped"},
            discover_profiles=lambda: [],
        ).execute({"operation": "inspect"})

    def test_cli_follows_repeated_renames_and_preserves_saved_identity_and_trust(self) -> None:
        for current_name in ("new-name", "newer-name"):
            with self.subTest(name=current_name):
                self.peer["DNSName"] = f"{current_name}.example.ts.net."
                code, result = self.preflight()
                self.assertEqual(code, 0, result)
                inspected = self.inspect()
                self.assertTrue(inspected["ok"], inspected)
                config = inspected["config"]
                self.assertEqual(config["connection"]["host"], f"{current_name}.example.ts.net")
                self.assertEqual(result["config"]["connection"]["host"], config["connection"]["host"])
                self.assertEqual(config["connection"]["connectionId"], self.original_id)
                self.assertEqual(config["connection"]["receiverName"], "Gaming PC")
                self.assertEqual(config["connection"]["managedIdentityId"], "a" * 64)
                self.assertEqual(config["remote"]["keyPath"], str(self.key))
                self.assertEqual(len(config["connections"]), 2)
                self.assertEqual(config["mixProfiles"][0]["connection"]["host"], config["connection"]["host"])
                trust = self.store.inspect(
                    config["connection"],
                    [f"{current_name}.example.ts.net ssh-ed25519 {self.approved_key}"],
                )
                self.assertEqual(trust["status"], "trusted")
                self.assertFalse(inspected["status"]["active"])
                command = self.commands[-1]
                self.assertIn("StrictHostKeyChecking=yes", command)
                self.assertIn("UpdateHostKeys=no", command)
                self.assertIn("listener@100.70.80.90", command)
                alias = next(item.split("=", 1)[1] for item in command if item.startswith("HostKeyAlias="))
                self.assertIn(
                    f"{alias} ssh-ed25519 {self.approved_key}",
                    self.store.known_hosts_path.read_text(encoding="utf-8").splitlines(),
                )
                self.assertEqual(config_path().stat().st_mode & 0o777, 0o600)

    def test_selecting_another_saved_receiver_refreshes_it_without_starting_audio(self) -> None:
        config = load_config()
        config["connection"] = next(
            saved for saved in config["connections"] if saved["type"] == "direct"
        )
        save_config(config)
        app = MixerApplication(
            discover_sources=lambda: [],
            read_status=lambda: {"active": False, "state": "stopped"},
            discover_profiles=lambda: [],
        )
        selected = app.execute(
            {
                "operation": "connection.select",
                "payload": {"connectionId": self.original_id},
            }
        )
        self.assertTrue(selected["ok"], selected)
        self.assertEqual(selected["connection"]["host"], "new-name.example.ts.net")
        self.assertEqual(selected["connectionId"], self.original_id)
        self.assertEqual(selected["config"]["remote"]["host"], "new-name.example.ts.net")
        self.assertEqual(self.commands, [], "metadata refresh must not start SSH or audio")

    def test_new_connections_follow_independent_renames_without_inheriting_identity(self) -> None:
        app = MixerApplication(
            discover_sources=lambda: [],
            read_status=lambda: {"active": False, "state": "stopped"},
            discover_profiles=lambda: [],
            scan_host_keys=lambda connection, **_kwargs: [
                f"{connection['host']} ssh-ed25519 {self.approved_key}"
            ],
        )
        new_connections = []
        for index, name in enumerate(("studio", "bedroom"), start=1):
            connection = {
                "type": "tailscale",
                "peerId": f"future-peer-{index}",
                "host": f"{name}.example.ts.net",
                "user": f"listener{index}",
                "port": 2200 + index,
                "receiverName": name.title(),
            }
            original_id = connection_id(connection)
            peer = {
                "ID": connection["peerId"],
                "HostName": name,
                "DNSName": connection["host"] + ".",
                "TailscaleIPs": [f"100.70.80.{90 + index}"],
                "Online": True,
            }
            self.additional_peers.append(peer)
            inspected = app.execute(
                {"operation": "trust.inspect", "payload": {"connection": connection}}
            )
            self.assertTrue(inspected["ok"], inspected)
            approved = app.execute(
                {
                    "operation": "trust.approve",
                    "payload": {
                        "connection": connection,
                        "expectedFingerprints": inspected["trust"]["candidateFingerprints"],
                    },
                }
            )
            self.assertTrue(approved["ok"], approved)
            peer["DNSName"] = f"{name}-renamed.example.ts.net."
            saved = app.execute(
                {"operation": "connection.save", "payload": {"connection": connection}}
            )
            self.assertTrue(saved["ok"], saved)
            with self.subTest(receiver=name, check="save response follows rename"):
                self.assertEqual(saved["connection"]["host"], peer["DNSName"].rstrip("."))
            with self.subTest(receiver=name, check="new identity remains independent"):
                persisted = self.inspect()["config"]["connection"]
                self.assertEqual(persisted["connectionId"], original_id)
                self.assertNotIn("managedIdentityId", persisted)
            new_connections.append((original_id, peer))

        self.assertEqual(self.commands, [], "onboarding metadata must not start audio")
        for original_id, peer in new_connections:
            peer["DNSName"] = "again-" + peer["DNSName"]
            selected = app.execute(
                {"operation": "connection.select", "payload": {"connectionId": original_id}}
            )
            self.assertTrue(selected["ok"], selected)
            self.assertEqual(selected["connection"]["host"], peer["DNSName"].rstrip("."))
            code, result = self.preflight()
            self.assertEqual(code, 0, result)
            self.assertEqual(result["config"]["connection"]["connectionId"], original_id)
            self.assertIn(f"@{peer['TailscaleIPs'][0]}", result["connection"]["target"])
            self.assertEqual(
                self.store.inspect(
                    selected["connection"],
                    [f"{peer['DNSName']} ssh-ed25519 {self.approved_key}"],
                )["status"],
                "trusted",
            )
        connections = self.inspect()["config"]["connections"]
        self.assertEqual(len(connections), 4)
        original = next(item for item in connections if item["connectionId"] == self.original_id)
        self.assertEqual(original["host"], "old-name.example.ts.net")
        self.assertEqual(original["managedIdentityId"], "a" * 64)

    def test_transport_follows_a_rename_without_requiring_a_prior_configuration_save(self) -> None:
        from ssh_mixer.session import test_connection

        result = test_connection(load_config()["remote"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["target"], "listener@100.70.80.90")
        self.assertIn(f"HostKeyAlias={self.original_id}", self.commands[-1])

    def test_renamed_receiver_keeps_separate_trust_when_another_device_reuses_its_old_name(self) -> None:
        other = {**self.connection, "peerId": "different-device"}
        other_key = base64.b64encode(b"separately-approved-other-device").decode("ascii")
        self.store.approve(
            other, [f"old-name.example.ts.net ssh-ed25519 {other_key}"]
        )
        code, result = self.preflight()
        self.assertEqual(code, 0, result)
        alias = next(
            item.split("=", 1)[1]
            for item in self.commands[-1]
            if item.startswith("HostKeyAlias=")
        )
        accepted_keys = {
            fields[2]
            for line in self.store.known_hosts_path.read_text(encoding="utf-8").splitlines()
            if (fields := line.split())[0] == alias
        }
        self.assertEqual(accepted_keys, {self.approved_key})

    def test_older_hostname_only_known_hosts_is_refreshed_from_approved_records(self) -> None:
        secure_write_text(
            self.store.known_hosts_path,
            f"old-name.example.ts.net ssh-ed25519 {self.approved_key}\n",
        )
        code, result = self.preflight()
        self.assertEqual(code, 0, result)
        self.assertIn(f"HostKeyAlias={self.original_id}", self.commands[-1])
        self.assertIn(
            f"{self.original_id} ssh-ed25519 {self.approved_key}",
            self.store.known_hosts_path.read_text(encoding="utf-8").splitlines(),
        )

    def test_missing_offline_replaced_or_misdirected_peers_never_reach_ssh(self) -> None:
        cases = (
            ({"Online": False}, "100.70.80.90"),
            ({"ID": "replacement-peer", "DNSName": "old-name.example.ts.net."}, "100.70.80.90"),
            ({}, "203.0.113.10"),
            ({"DNSName": "-unsafe.example.ts.net."}, "100.70.80.90"),
        )
        original_peer = dict(self.peer)
        for changes, address in cases:
            with self.subTest(changes=changes, address=address):
                self.peer = {**original_peer, **changes}
                self.resolved_address = address
                code, result = self.preflight()
                self.assertEqual(code, 1, result)
                self.assertFalse(result["ok"])
                self.assertEqual(self.commands, [])
                self.assertEqual(load_config()["connection"]["host"], "old-name.example.ts.net")
                self.assertTrue(self.store.has_record(self.connection))

    def test_unresolvable_current_name_does_not_replace_saved_address(self) -> None:
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("unresolvable")):
            code, result = self.preflight()
        self.assertEqual(code, 1, result)
        self.assertEqual(self.commands, [])
        self.assertEqual(load_config()["connection"]["host"], "old-name.example.ts.net")

    def test_rename_does_not_approve_a_changed_host_key(self) -> None:
        before = self.store.known_hosts_path.read_bytes()
        self.ssh_exit = 255
        code, result = self.preflight()
        self.assertEqual(code, 1, result)
        self.assertIn("Host key verification failed", result["connection"]["error"])
        connection = load_config()["connection"]
        replacement = base64.b64encode(b"unapproved-replacement-key").decode("ascii")
        trust = self.store.inspect(
            connection, [f"new-name.example.ts.net ssh-ed25519 {replacement}"]
        )
        self.assertEqual(trust["status"], "changed")
        self.assertEqual(self.store.known_hosts_path.read_bytes(), before)
        self.assertIn("StrictHostKeyChecking=yes", self.commands[-1])
        self.assertIn(f"HostKeyAlias={self.original_id}", self.commands[-1])

    def test_rename_does_not_create_unknown_trust(self) -> None:
        self.store.revoke(self.connection)
        self.ssh_exit = 255
        code, result = self.preflight()
        self.assertEqual(code, 1, result)
        connection = load_config()["connection"]
        trust = self.store.inspect(
            connection, [f"new-name.example.ts.net ssh-ed25519 {self.approved_key}"]
        )
        self.assertEqual(trust["status"], "unknown")
        self.assertEqual(self.store.known_hosts_path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
