from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .linux_setup import SetupError, build_linux_plan
from .macos_setup import build_macos_plan
from .windows_setup import build_windows_plan


class UpdateBootstrap(Protocol):
    def probe(self) -> dict[str, Any]: ...

    def apply(
        self,
        plan: dict[str, Any],
        identity: dict[str, Any],
        *,
        setup_path: Path | None = None,
        receiver_path: Path | None = None,
        retain_transaction: bool = False,
    ) -> dict[str, Any]: ...

    def verify(self, plan: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]: ...

    def commit(self) -> bool: ...

    def rollback(self, plan: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]: ...


class NativeUpdateTransaction:
    """Runs one approved signed update through native Bootstrap Authentication."""

    def __init__(
        self,
        *,
        platform: str,
        bootstrap: UpdateBootstrap,
        identity: dict[str, Any],
    ) -> None:
        if platform not in {"linux", "windows", "macos"}:
            raise SetupError("native update platform is unsupported")
        self.platform = platform
        self.bootstrap = bootstrap
        self.identity = identity
        self._setup_plan: dict[str, Any] | None = None

    def plan(self, platform: str) -> dict[str, Any]:
        if platform != self.platform:
            raise SetupError("signed update platform does not match the Receiver")
        probe = self.bootstrap.probe()
        if platform == "linux":
            setup = build_linux_plan(probe)
        elif platform == "windows":
            setup = build_windows_plan(probe, administrator_confirmed=True)
        else:
            setup = build_macos_plan(probe)
        blocked = [
            change
            for change in setup.get("changes", [])
            if change.get("kind") in {
                "package-install",
                "openssh-capability",
                "openssh-service",
                "firewall-rule",
                "remote-login",
            }
        ]
        if blocked:
            raise SetupError(
                "routine Receiver update requires a separate Companion Setup plan because system dependencies or services would change"
            )
        privilege_changes = [
            str(change.get("summary", ""))
            for change in setup.get("changes", [])
            if change.get("requiresPrivilege") is True
        ]
        self._setup_plan = setup
        home = str(setup.get("home", setup.get("profile", "")))
        authorized_keys = str(
            setup.get("authorizedKeysPath", f"{home}/.ssh/authorized_keys")
        )
        return {
            "platform": platform,
            "receiverUser": str(setup.get("user", "")),
            "receiverPath": str(setup.get("receiverPath", "")),
            "authorizedKeysPath": authorized_keys,
            "authentication": "native-openssh",
            "managedIdentityCanUpdate": False,
            "privilegeRequired": bool(privilege_changes),
            "privilegeChanges": privilege_changes,
            "changes": [
                "Transfer only the signed/checksummed Companion and Receiver artifacts",
                "Retain protected helper and authorized-key backups through verification",
                "Run the verified Companion Setup as the non-root Receiver account",
                "Commit only after version, protocol, restriction, and runtime checks pass",
            ],
            "rollback": "restore the prior helper and exact authorized-key file",
            "experimental": platform == "macos",
            "realDeviceVerified": False if platform == "macos" else None,
        }

    def install(
        self, plan: dict[str, Any], staged: dict[str, Path]
    ) -> dict[str, Any]:
        if self._setup_plan is None or set(staged) != {"companion", "receiver"}:
            raise SetupError("native update transaction was not planned with both artifacts")
        setup = dict(self._setup_plan)
        artifacts = {str(item.get("kind")): item for item in plan.get("artifacts", [])}
        if set(artifacts) != {"companion", "receiver"}:
            raise SetupError("signed update artifacts are incomplete")
        setup.update(
            {
                "setupSha256": artifacts["companion"]["sha256"],
                "receiverSha256": artifacts["receiver"]["sha256"],
                "companionVersion": plan["target"]["companion"],
                "receiverVersion": plan["target"]["receiver"],
                "expectedReceiverVersion": plan["target"]["receiver"],
                "previousReceiverVersion": plan["current"]["receiver"],
            }
        )
        self._setup_plan = setup
        result = self.bootstrap.apply(
            setup,
            self.identity,
            setup_path=staged["companion"],
            receiver_path=staged["receiver"],
            retain_transaction=True,
        )
        installed = result.get("setup", {}) if isinstance(result, dict) else {}
        if (
            result.get("ok") is not True
            or not isinstance(installed, dict)
            or installed.get("companionVersion") != plan["target"]["companion"]
        ):
            raise SetupError("installed Companion version could not be verified")
        return {
            "ok": True,
            "companionVersion": installed["companionVersion"],
        }

    def verify(self, plan: dict[str, Any]) -> dict[str, Any]:
        if self._setup_plan is None:
            raise SetupError("native update transaction has no retained setup plan")
        verified = self.bootstrap.verify(self._setup_plan, self.identity)
        if verified.get("ok") is not True:
            return {"ok": False, "error": "Receiver restrictions were not verified"}
        return {
            "ok": True,
            "platform": self.platform,
            "helperVersion": plan["target"]["receiver"],
            "protocol": plan["target"]["protocol"],
            "experimental": self.platform == "macos",
        }

    def commit(self, _plan: dict[str, Any]) -> dict[str, Any]:
        complete = self.bootstrap.commit()
        return {"ok": complete, "complete": complete}

    def rollback(self, plan: dict[str, Any]) -> dict[str, Any]:
        if self._setup_plan is None:
            return {"ok": True, "complete": True}
        return self.bootstrap.rollback(self._setup_plan, self.identity)
