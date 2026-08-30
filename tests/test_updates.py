from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.diagnostics import DiagnosticStore
from ssh_mixer.updates import (
    COMPANION_VERSIONS,
    PINNED_RECEIVER_RELEASE,
    PLUGIN_VERSION,
    PROTOCOL_VERSION,
    RECEIVER_VERSIONS,
    ReleaseSignatureVerifier,
    UpdateError,
    UpdateService,
    fetch_release_pair,
)


def artifact(platform: str, kind: str, version: str, content: bytes) -> dict[str, object]:
    extension = "ps1" if platform == "windows" else ("sh" if kind == "companion" else "bin")
    return {
        "platform": platform,
        "kind": kind,
        "version": version,
        "protocolMinimum": 1,
        "protocolMaximum": 1,
        "url": (
            "https://github.com/jabaiwho/ssh-mixer/releases/download/"
            f"receiver-v{version}/ssh-mixer-{platform}-{kind}-v{version}.{extension}"
        ),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "changes": [f"Update {platform} {kind} to {version}"],
        "sourceCommit": "0123456789abcdef0123456789abcdef01234567",
    }


def metadata(platform: str, companion: bytes, receiver: bytes) -> bytes:
    release_version = RECEIVER_VERSIONS[platform]
    if COMPANION_VERSIONS[platform] != release_version:
        raise AssertionError("test metadata requires one common artifact release version")
    value = {
        "schemaVersion": 1,
        "releaseId": f"receiver-v{release_version}+0123456789abcdef0123456789abcdef01234567",
        "pluginVersion": PLUGIN_VERSION,
        "publishedAt": "2026-02-27T00:00:00Z",
        "artifacts": [
            artifact(platform, "companion", release_version, companion),
            artifact(platform, "receiver", release_version, receiver),
        ],
    }
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


class VersionRepresentationTest(unittest.TestCase):
    def test_release_builder_emits_six_immutable_versioned_artifacts(self) -> None:
        changes = {
            f"{platform}/{kind}": [f"Reviewed {platform} {kind} change"]
            for platform in ("linux", "windows", "macos")
            for kind in ("companion", "receiver")
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            changes_path = root / "changes.json"
            output = root / "release-metadata.json"
            artifacts = root / "artifacts"
            changes_path.write_text(json.dumps(changes), encoding="utf-8")
            completed = subprocess.run(
                [
                    "python3",
                    str(Path(__file__).resolve().parents[1] / "scripts" / "build_release_metadata.py"),
                    "--version",
                    "1.0.0",
                    "--commit",
                    "a" * 40,
                    "--published-at",
                    "2026-02-27T00:00:00Z",
                    "--changes",
                    str(changes_path),
                    "--output",
                    str(output),
                    "--artifact-dir",
                    str(artifacts),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            built = json.loads(output.read_text(encoding="utf-8"))
            artifact_count = len(list(artifacts.glob("*")))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(built["artifacts"]), 6)
        self.assertTrue(all("/releases/download/receiver-v1.0.0/" in item["url"] for item in built["artifacts"]))
        self.assertEqual(artifact_count, 6)

    def test_plugin_companion_receiver_and_protocol_versions_are_independent(self) -> None:
        self.assertEqual(PLUGIN_VERSION, "0.1.1")
        self.assertEqual(PROTOCOL_VERSION, 1)
        self.assertEqual(PINNED_RECEIVER_RELEASE, "1.1.2")
        self.assertEqual(COMPANION_VERSIONS["linux"], "1.1.2")
        self.assertEqual(RECEIVER_VERSIONS["windows"], "1.1.2")
        self.assertNotEqual(PLUGIN_VERSION, RECEIVER_VERSIONS["macos"])


class SignatureVerificationTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ssh-keygen"), "OpenSSH ssh-keygen is required")
    def test_real_detached_openssh_signature_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            key = root / "release_signing_key"
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                check=True,
            )
            allowed = root / "allowed_signers"
            public = key.with_suffix(".pub").read_text(encoding="utf-8").split()
            allowed.write_text(
                f"ssh-mixer-release {public[0]} {public[1]}\n", encoding="utf-8"
            )
            payload = metadata("linux", b"setup", b"receiver")
            metadata_path = root / "release-metadata.json"
            metadata_path.write_bytes(payload)
            subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "sign",
                    "-f",
                    str(key),
                    "-n",
                    "ssh-mixer-release",
                    str(metadata_path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            signature = Path(str(metadata_path) + ".sig")

            verified = ReleaseSignatureVerifier(allowed).verify(payload, signature)

        self.assertTrue(verified)

    def test_openssh_signature_is_verified_before_metadata_is_trusted(self) -> None:
        calls: list[tuple[list[str], bytes]] = []

        def runner(command: list[str], **kwargs: object):
            calls.append((command, kwargs.get("input", b"")))
            return subprocess.CompletedProcess(command, 0, b"Good signature", b"")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = root / "allowed_signers"
            signature = root / "release-metadata.json.sig"
            allowed.write_text("ssh-mixer-release ssh-ed25519 AAAATEST\n", encoding="utf-8")
            signature.write_bytes(b"-----BEGIN SSH SIGNATURE-----\ntest\n")
            verifier = ReleaseSignatureVerifier(allowed, runner=runner)
            payload = metadata("linux", b"setup", b"receiver")
            verifier.verify(payload, signature)

        command, signed_input = calls[0]
        self.assertEqual(command[:3], ["ssh-keygen", "-Y", "verify"])
        self.assertIn("ssh-mixer-release", command)
        self.assertEqual(signed_input, payload)
        self.assertNotIn(b"AAAATEST", signed_input)

    def test_signature_bytes_are_staged_privately_and_removed(self) -> None:
        observed: list[tuple[Path, int]] = []

        def runner(command: list[str], **kwargs: object):
            signature = Path(command[command.index("-s") + 1])
            observed.append((signature, signature.stat().st_mode & 0o777))
            return subprocess.CompletedProcess(command, 0, b"Good signature", b"")

        with tempfile.TemporaryDirectory() as temp:
            allowed = Path(temp) / "allowed_signers"
            allowed.write_text("ssh-mixer-release ssh-ed25519 AAAATEST\n", encoding="utf-8")
            verifier = ReleaseSignatureVerifier(allowed, runner=runner)

            verified = verifier.verify(b"metadata", b"detached-signature")
            staged_path = observed[0][0]

        self.assertTrue(verified)
        self.assertEqual(observed[0][1], 0o600)
        self.assertFalse(staged_path.exists())

    def test_bad_signature_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = root / "allowed_signers"
            signature = root / "metadata.sig"
            allowed.write_text("ssh-mixer-release ssh-ed25519 AAAATEST\n", encoding="utf-8")
            signature.write_bytes(b"bad")
            verifier = ReleaseSignatureVerifier(
                allowed,
                runner=lambda command, **kwargs: subprocess.CompletedProcess(
                    command, 1, b"", b"signature verification failed"
                ),
            )
            with self.assertRaises(UpdateError):
                verifier.verify(metadata("linux", b"setup", b"receiver"), signature)


class ReleaseFetchTest(unittest.TestCase):
    def test_pinned_release_fetch_uses_only_immutable_bounded_urls(self) -> None:
        calls: list[tuple[str, int]] = []

        def fetch(url: str, maximum: int) -> bytes:
            calls.append((url, maximum))
            return b"signature" if url.endswith(".sig") else b"metadata"

        pair = fetch_release_pair("1.1.0", fetch=fetch)

        self.assertEqual(pair, (b"metadata", b"signature"))
        self.assertEqual(
            [url for url, _maximum in calls],
            [
                "https://github.com/jabaiwho/ssh-mixer/releases/download/receiver-v1.1.0/receiver-v1.1.0.json",
                "https://github.com/jabaiwho/ssh-mixer/releases/download/receiver-v1.1.0/receiver-v1.1.0.json.sig",
            ],
        )
        self.assertTrue(all(maximum <= 256 * 1024 for _url, maximum in calls))


class UpdateServiceTest(unittest.TestCase):
    def service(
        self,
        *,
        platform: str = "linux",
        companion: bytes = b"new setup",
        receiver: bytes = b"new receiver",
        installer=None,
        post_verify=None,
        rollback=None,
        commit=None,
    ) -> tuple[UpdateService, bytes, dict[str, bytes]]:
        signed = metadata(platform, companion, receiver)
        artifacts = {
            json.loads(signed)["artifacts"][0]["url"]: companion,
            json.loads(signed)["artifacts"][1]["url"]: receiver,
        }
        service = UpdateService(
            signature_verifier=lambda payload, _signature: payload == signed,
            fetch_artifact=lambda url, maximum: artifacts[url],
            installer=installer
            or (
                lambda plan, _paths: {
                    "ok": True,
                    "companionVersion": plan["target"]["companion"],
                }
            ),
            post_verify=post_verify
            or (
                lambda plan: {
                    "ok": True,
                    "platform": platform,
                    "helperVersion": plan["target"]["receiver"],
                    "protocol": 1,
                }
            ),
            rollback=rollback or (lambda _plan: {"ok": True, "complete": True}),
            commit=commit or (lambda _plan: {"ok": True, "complete": True}),
        )
        return service, signed, artifacts

    def test_compatible_installed_helper_is_not_forced_to_update(self) -> None:
        service, signed, _artifacts = self.service()
        plan = service.plan(
            signed,
            b"signature",
            platform="linux",
            installed={"helperVersion": "1.0.0", "protocol": 1},
        )
        self.assertEqual(plan["status"], "update-available")
        self.assertFalse(plan["required"])
        self.assertFalse(plan["automaticInstall"])
        self.assertEqual(plan["current"]["receiver"], "1.0.0")
        self.assertEqual(plan["target"]["receiver"], RECEIVER_VERSIONS["linux"])
        self.assertTrue(plan["changes"])

    def test_incompatible_installed_helper_requires_guided_update(self) -> None:
        service, signed, _artifacts = self.service()
        plan = service.plan(
            signed,
            b"signature",
            platform="linux",
            installed={"helperVersion": "0.5.0", "protocol": 0},
        )
        self.assertEqual(plan["status"], "update-required")
        self.assertTrue(plan["required"])
        self.assertIn("Receiver Protocol", plan["guidance"])

    def test_incompatible_release_is_not_installable(self) -> None:
        service, signed, _artifacts = self.service()
        value = json.loads(signed)
        for item in value["artifacts"]:
            item["protocolMinimum"] = 2
            item["protocolMaximum"] = 2
        incompatible = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        service = UpdateService(
            signature_verifier=lambda _payload, _signature: True,
            fetch_artifact=lambda _url, _maximum: b"",
            installer=lambda _plan, _paths: {"ok": True},
            post_verify=lambda _plan: {"ok": True},
            rollback=lambda _plan: {"ok": True, "complete": True},
        )
        with self.assertRaisesRegex(UpdateError, "incompatible"):
            service.plan(
                incompatible,
                b"signature",
                platform="linux",
                installed={"helperVersion": "1.0.0", "protocol": 1},
            )

    def test_release_id_version_and_commit_bind_every_artifact(self) -> None:
        service, signed, _artifacts = self.service()
        service.signature_verifier = lambda _payload, _signature: True
        for field, value in (
            ("version", "1.2.0"),
            ("sourceCommit", "f" * 40),
        ):
            changed = json.loads(signed)
            changed["artifacts"][0][field] = value
            payload = (json.dumps(changed, sort_keys=True, separators=(",", ":")) + "\n").encode()
            with self.subTest(field=field), self.assertRaisesRegex(UpdateError, "release id"):
                service.plan(
                    payload,
                    b"signature",
                    platform="linux",
                    installed={"helperVersion": "1.0.0", "protocol": 1},
                )

    def test_mutable_or_cross_repository_artifact_urls_are_rejected(self) -> None:
        service, signed, _artifacts = self.service()
        for bad_url in (
            "https://github.com/jabaiwho/ssh-mixer/releases/latest/download/helper",
            "https://raw.githubusercontent.com/jabaiwho/ssh-mixer/main/helper",
            "https://example.com/helper",
        ):
            value = json.loads(signed)
            value["artifacts"][0]["url"] = bad_url
            changed = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
            with self.subTest(url=bad_url), self.assertRaises(UpdateError):
                service.plan(
                    changed,
                    b"signature",
                    platform="linux",
                    installed={"helperVersion": "1.0.0", "protocol": 1},
                )

    def test_signature_and_metadata_identity_are_rechecked_at_execution(self) -> None:
        calls = 0
        service, signed, artifacts = self.service()

        def verifier(payload, _signature):
            nonlocal calls
            calls += 1
            return payload == signed

        service.signature_verifier = verifier
        plan = service.plan(
            signed,
            b"signature",
            platform="linux",
            installed={"helperVersion": "1.0.0", "protocol": 1},
        )
        result = service.execute(
            plan, signed, b"signature", approved_plan_hash=plan["planHash"]
        )

        self.assertTrue(result["ok"])
        self.assertEqual(calls, 2)
        self.assertEqual(len(artifacts), 2)

    def test_no_update_installs_without_exact_plan_approval(self) -> None:
        installed = False

        def installer(_plan, _paths):
            nonlocal installed
            installed = True
            return {"ok": True}

        service, signed, _artifacts = self.service(installer=installer)
        plan = service.plan(
            signed,
            b"signature",
            platform="linux",
            installed={"helperVersion": "1.0.0", "protocol": 1},
        )
        with self.assertRaises(UpdateError):
            service.execute(plan, signed, b"signature", approved_plan_hash="cancelled")
        self.assertFalse(installed)

    def test_checksum_failure_does_not_execute_or_require_rollback(self) -> None:
        calls: list[str] = []
        service, signed, artifacts = self.service(
            installer=lambda _plan, _paths: calls.append("install") or {"ok": True},
            rollback=lambda _plan: calls.append("rollback") or {"ok": True, "complete": True},
        )
        bad_url = next(iter(artifacts))
        service.fetch_artifact = lambda url, maximum: b"substituted" if url == bad_url else artifacts[url]
        plan = service.plan(
            signed,
            b"signature",
            platform="linux",
            installed={"helperVersion": "1.0.0", "protocol": 1},
        )
        result = service.execute(
            plan, signed, b"signature", approved_plan_hash=plan["planHash"]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "artifact-verification")
        self.assertEqual(result["rollback"], "not-required")
        self.assertEqual(calls, [])

    def test_unverified_companion_version_rolls_back_before_helper_verification(self) -> None:
        calls: list[str] = []
        service, signed, _artifacts = self.service(
            installer=lambda _plan, _paths: calls.append("install")
            or {"ok": True, "companionVersion": "unknown"},
            post_verify=lambda _plan: calls.append("verify") or {"ok": True},
            rollback=lambda _plan: calls.append("rollback")
            or {"ok": True, "complete": True},
        )
        plan = service.plan(
            signed,
            b"signature",
            platform="linux",
            installed={"helperVersion": "1.0.0", "protocol": 1},
        )
        result = service.execute(
            plan, signed, b"signature", approved_plan_hash=plan["planHash"]
        )
        self.assertEqual(calls, ["install", "rollback"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"], "complete")

    def test_failed_post_update_verification_restores_prior_version(self) -> None:
        calls: list[str] = []
        service, signed, _artifacts = self.service(
            platform="windows",
            installer=lambda plan, _paths: calls.append("install")
            or {
                "ok": True,
                "companionVersion": plan["target"]["companion"],
            },
            post_verify=lambda _plan: calls.append("verify")
            or {"ok": False, "error": "capability mismatch"},
            rollback=lambda _plan: calls.append("rollback")
            or {"ok": True, "complete": True, "restoredVersion": "1.0.0"},
        )
        plan = service.plan(
            signed,
            b"signature",
            platform="windows",
            installed={"helperVersion": "1.0.0", "protocol": 1},
        )
        result = service.execute(
            plan, signed, b"signature", approved_plan_hash=plan["planHash"]
        )
        self.assertEqual(calls, ["install", "verify", "rollback"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"], "complete")
        self.assertEqual(result["restoredVersion"], "1.0.0")

    def test_verified_update_commits_and_cleans_private_staging(self) -> None:
        staged_modes: list[int] = []
        calls: list[str] = []

        def installer(plan, paths):
            calls.append("install")
            staged_modes.extend(path.stat().st_mode & 0o777 for path in paths.values())
            return {
                "ok": True,
                "companionVersion": plan["target"]["companion"],
            }

        service, signed, _artifacts = self.service(
            platform="macos",
            installer=installer,
            post_verify=lambda plan: calls.append("verify") or {
                "ok": True,
                "platform": "macos",
                "helperVersion": plan["target"]["receiver"],
                "protocol": 1,
            },
            commit=lambda _plan: calls.append("commit") or {"ok": True, "complete": True},
        )
        plan = service.plan(
            signed,
            b"signature",
            platform="macos",
            installed={"helperVersion": "1.0.0", "protocol": 1},
        )
        result = service.execute(
            plan, signed, b"signature", approved_plan_hash=plan["planHash"]
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertEqual(calls, ["install", "verify", "commit"])
        self.assertEqual(staged_modes, [0o600, 0o600])
        self.assertFalse(Path(result["stagingPath"]).exists())

    def test_failed_transaction_commit_rolls_back(self) -> None:
        calls: list[str] = []
        service, signed, _artifacts = self.service(
            installer=lambda plan, _paths: calls.append("install") or {
                "ok": True,
                "companionVersion": plan["target"]["companion"],
            },
            post_verify=lambda plan: calls.append("verify") or {
                "ok": True,
                "platform": "linux",
                "helperVersion": plan["target"]["receiver"],
                "protocol": 1,
            },
            commit=lambda _plan: calls.append("commit") or {"ok": False, "complete": False},
            rollback=lambda _plan: calls.append("rollback") or {
                "ok": True,
                "complete": True,
                "restoredVersion": "1.0.0",
            },
        )
        plan = service.plan(
            signed,
            b"signature",
            platform="linux",
            installed={"helperVersion": "1.0.0", "protocol": 1},
        )

        result = service.execute(
            plan, signed, b"signature", approved_plan_hash=plan["planHash"]
        )

        self.assertEqual(calls, ["install", "verify", "commit", "rollback"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "post-update-verification")
        self.assertEqual(result["rollback"], "complete")


class UpdateApplicationTest(unittest.TestCase):
    def test_pinned_release_check_and_apply_fetch_verified_receiver_state(self) -> None:
        service, signed, _artifacts = UpdateServiceTest().service()
        fetches: list[str] = []
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
                update_service=service,
                test_connection=lambda _remote: {
                    "ok": True,
                    "capabilities": {"helperVersion": "1.0.0", "protocolVersion": 1},
                },
                release_pair_fetcher=lambda version: fetches.append(version) or (
                    signed,
                    b"signature",
                ),
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            checked = app.execute(
                {"operation": "updates.check", "payload": {"platform": "linux"}}
            )
            applied = app.execute(
                {
                    "operation": "updates.apply-pinned",
                    "payload": {
                        "plan": checked["plan"],
                        "approvedPlanHash": checked["plan"]["planHash"],
                    },
                }
            )

        self.assertTrue(checked["ok"])
        self.assertEqual(checked["plan"]["status"], "update-available")
        self.assertTrue(applied["ok"])
        self.assertEqual(fetches, [PINNED_RECEIVER_RELEASE, PINNED_RECEIVER_RELEASE])

    def test_application_exposes_plan_then_requires_approval(self) -> None:
        service, signed, _artifacts = UpdateServiceTest().service()
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
                update_service=service,
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            planned = app.execute(
                {
                    "operation": "updates.plan",
                    "payload": {
                        "metadata": signed.decode(),
                        "signature": "signature",
                        "platform": "linux",
                        "installed": {"helperVersion": "1.0.0", "protocol": 1},
                    },
                }
            )
            rejected = app.execute(
                {
                    "operation": "updates.apply",
                    "payload": {
                        "metadata": signed.decode(),
                        "signature": "signature",
                        "plan": planned["plan"],
                        "approvedPlanHash": "",
                    },
                }
            )
            active_app = MixerApplication(
                update_service=service,
                read_status=lambda: {"state": "streaming", "active": True},
                diagnostic_store=DiagnosticStore(Path(temp) / "active-logs"),
            )
            deferred = active_app.execute(
                {
                    "operation": "updates.apply",
                    "payload": {
                        "metadata": signed.decode(),
                        "signature": "signature",
                        "plan": planned["plan"],
                        "approvedPlanHash": planned["plan"]["planHash"],
                    },
                }
            )

        self.assertTrue(planned["ok"])
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["diagnostic"]["code"], "approval-required")
        self.assertFalse(deferred["ok"])
        self.assertEqual(deferred["diagnostic"]["code"], "session-active")


if __name__ == "__main__":
    unittest.main()
