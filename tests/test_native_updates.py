from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from ssh_mixer.linux_setup import SetupError
from ssh_mixer.native_updates import NativeUpdateTransaction


class FakeBootstrap:
    def __init__(self, *, missing_dependencies: bool = False) -> None:
        self.missing_dependencies = missing_dependencies
        self.calls: list[str] = []
        self.applied_plan: dict[str, object] = {}

    def probe(self):
        self.calls.append("probe")
        return {
            "platform": "linux",
            "user": "listener",
            "home": "/home/listener",
            "commands": {
                "python3": not self.missing_dependencies,
                "ffplay": not self.missing_dependencies,
                "apt-get": True,
            },
        }

    def apply(self, plan, identity, **kwargs):
        self.calls.append("apply")
        self.applied_plan = plan
        self.asserted_identity = identity
        self.asserted_kwargs = kwargs
        return {
            "ok": True,
            "setup": {"ok": True, "companionVersion": plan["companionVersion"]},
        }

    def verify(self, plan, identity):
        self.calls.append("verify")
        return {"ok": True, "restrictionsVerified": True}

    def commit(self):
        self.calls.append("commit")
        return True

    def rollback(self, plan, identity):
        self.calls.append("rollback")
        return {"ok": True, "complete": True, "restoredVersion": "1.0.0"}


class WindowsFakeBootstrap(FakeBootstrap):
    def probe(self):
        self.calls.append("probe")
        return {
            "schemaVersion": 1,
            "platform": "windows",
            "user": "Listener",
            "profile": "C:\\Users\\Listener",
            "openSshVersion": "9.5.0.0",
            "sshdInstalled": True,
            "sshdRunning": True,
            "firewallRule": True,
            "firewallPort": "22",
            "ffplay": True,
            "winget": True,
            "administratorCapable": True,
            "elevated": False,
            "sshPort": 22,
        }


class NativeUpdateTransactionTest(unittest.TestCase):
    def test_native_transaction_plans_applies_verifies_and_commits_without_managed_update_authority(self) -> None:
        bootstrap = FakeBootstrap()
        identity = {"publicKey": "ssh-ed25519 ZmFrZQ==", "privateKeyPath": "/private/key"}
        transaction = NativeUpdateTransaction(
            platform="linux", bootstrap=bootstrap, identity=identity
        )
        with tempfile.TemporaryDirectory() as temp:
            companion = Path(temp) / "setup.sh"
            receiver = Path(temp) / "receiver.py"
            companion.write_bytes(b"companion")
            receiver.write_bytes(b"receiver")
            update_plan = {
                "artifacts": [
                    {"kind": "companion", "sha256": hashlib.sha256(b"companion").hexdigest()},
                    {"kind": "receiver", "sha256": hashlib.sha256(b"receiver").hexdigest()},
                ],
                "current": {"receiver": "1.0.0"},
                "target": {"companion": "1.1.0", "receiver": "1.1.0", "protocol": 1},
            }

            public_plan = transaction.plan("linux")
            installed = transaction.install(
                update_plan, {"companion": companion, "receiver": receiver}
            )
            verified = transaction.verify(update_plan)
            committed = transaction.commit(update_plan)

        self.assertEqual(public_plan["authentication"], "native-openssh")
        self.assertFalse(public_plan["managedIdentityCanUpdate"])
        self.assertFalse(public_plan["privilegeRequired"])
        self.assertEqual(installed["companionVersion"], "1.1.0")
        self.assertEqual(verified["helperVersion"], "1.1.0")
        self.assertTrue(committed["complete"])
        self.assertEqual(bootstrap.calls, ["probe", "apply", "verify", "commit"])
        self.assertTrue(bootstrap.asserted_kwargs["retain_transaction"])
        self.assertEqual(bootstrap.applied_plan["previousReceiverVersion"], "1.0.0")

    def test_windows_administrator_acl_approval_is_disclosed(self) -> None:
        transaction = NativeUpdateTransaction(
            platform="windows",
            bootstrap=WindowsFakeBootstrap(),
            identity={},
        )

        plan = transaction.plan("windows")

        self.assertTrue(plan["privilegeRequired"])
        self.assertTrue(any("ACL" in item for item in plan["privilegeChanges"]))
        self.assertFalse(plan["managedIdentityCanUpdate"])

    def test_routine_update_rejects_dependency_or_system_changes(self) -> None:
        transaction = NativeUpdateTransaction(
            platform="linux",
            bootstrap=FakeBootstrap(missing_dependencies=True),
            identity={},
        )

        with self.assertRaisesRegex(SetupError, "separate Companion Setup plan"):
            transaction.plan("linux")

    def test_rollback_uses_retained_native_transaction(self) -> None:
        bootstrap = FakeBootstrap()
        transaction = NativeUpdateTransaction(
            platform="linux", bootstrap=bootstrap, identity={}
        )
        transaction.plan("linux")

        result = transaction.rollback({"target": {"receiver": "1.1.0"}})

        self.assertTrue(result["complete"])
        self.assertEqual(result["restoredVersion"], "1.0.0")
        self.assertEqual(bootstrap.calls, ["probe", "rollback"])


if __name__ == "__main__":
    unittest.main()
