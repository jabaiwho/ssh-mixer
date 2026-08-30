from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .audio import discover_sources as default_discover_sources
from .audio import (
    matchers_for_source_ids,
    resolve_source_ids,
    resolve_source_matchers,
)
from .bootstrap import LinuxBootstrap
from .config import (
    config_from_payload,
    load_config,
    keys_dir,
    logs_dir,
    public_config,
    save_config,
    trust_dir,
)
from .connections import (
    TrustStore,
    connection_id,
    discover_tailscale_peers as default_discover_tailscale_peers,
    find_connection,
    normalize_connection,
    rename_connection,
    scan_host_keys as default_scan_host_keys,
    verify_tailscale_peer,
)
from .diagnostics import DiagnosticStore, GITHUB_CONTRIBUTING_URL, github_issue_url
from .identity import ManagedIdentityStore
from .lifecycle import (
    handle_lifecycle_event,
    indicator_status,
    write_indicator_heartbeat,
)
from .linux_setup import LinuxSetupTracer
from .migration import MigrationService
from .mix_profiles import find_mix_profile, normalize_mix_profile, upsert_mix_profile
from .native_updates import NativeUpdateTransaction
from .macos_bootstrap import MacOsBootstrap
from .macos_setup import MacOsSetupTracer
from .removal import PluginRemover, RemoteRemover, RemovalService
from .openssh_profiles import (
    discover_profiles as default_discover_profiles,
    inspect_profile as default_inspect_profile,
    profile_connection,
)
from .source_choices import build_source_choices, resolve_choice_ids
from .source_history import SourceHistoryStore
from .session import (
    SessionError,
    normalize_status,
    quiet_test,
    test_connection as default_test_connection,
    start_session as default_start_session,
    stop_session as default_stop_session,
)
from .windows_bootstrap import WindowsBootstrap
from .windows_setup import WindowsSetupTracer
from .updates import ReleaseSignatureVerifier, UpdateService, fetch_release_pair
from .versions import (
    COMPANION_VERSIONS,
    PLUGIN_VERSION,
    PINNED_RECEIVER_RELEASE,
    PROTOCOL_VERSION,
    RECEIVER_VERSIONS,
)

SourceDiscovery = Callable[[], list[dict[str, Any]]]
StatusReader = Callable[[], dict[str, Any]]
SessionStarter = Callable[[dict[str, Any]], dict[str, Any]]
SessionStopper = Callable[..., dict[str, Any]]
ConnectionTester = Callable[[dict[str, Any]], dict[str, Any]]
ReleasePairFetcher = Callable[[str], tuple[bytes, bytes]]
ConnectionDiscovery = Callable[[], list[dict[str, Any]]]
HostKeyScanner = Callable[..., list[str]]
ProfileDiscovery = Callable[[], list[str]]
ProfileInspector = Callable[[str], dict[str, Any]]
BootstrapFactory = Callable[[dict[str, Any], str | None], LinuxBootstrap]
WindowsBootstrapFactory = Callable[[dict[str, Any], str | None], WindowsBootstrap]
MacOsBootstrapFactory = Callable[[dict[str, Any], str | None], MacOsBootstrap]


def _source_resolution_requires_review(
    matchers: list[dict[str, Any]], resolution: dict[str, Any]
) -> bool:
    for raw_index in resolution.get("missingMatchers", []):
        try:
            matcher = matchers[int(raw_index)]
        except (IndexError, TypeError, ValueError):
            return True
        if matcher.get("kind") != "playback":
            return True
    return bool(resolution.get("ambiguousMatchers"))


class MixerApplication:
    """Structured interface shared by interactive SSH-mixer adapters."""

    def __init__(
        self,
        *,
        discover_sources: SourceDiscovery = default_discover_sources,
        read_status: StatusReader = normalize_status,
        start_session: SessionStarter = default_start_session,
        stop_session: SessionStopper = default_stop_session,
        test_connection: ConnectionTester = default_test_connection,
        release_pair_fetcher: ReleasePairFetcher = fetch_release_pair,
        discover_tailscale_peers: ConnectionDiscovery = default_discover_tailscale_peers,
        scan_host_keys: HostKeyScanner = default_scan_host_keys,
        discover_profiles: ProfileDiscovery = default_discover_profiles,
        inspect_profile: ProfileInspector = default_inspect_profile,
        bootstrap_factory: BootstrapFactory | None = None,
        windows_bootstrap_factory: WindowsBootstrapFactory | None = None,
        macos_bootstrap_factory: MacOsBootstrapFactory | None = None,
        identity_store: ManagedIdentityStore | None = None,
        update_service: UpdateService | None = None,
        diagnostic_store: DiagnosticStore | None = None,
        trust_store: TrustStore | None = None,
        remote_remove: RemoteRemover | None = None,
        plugin_remove: PluginRemover | None = None,
        source_history: SourceHistoryStore | None = None,
    ) -> None:
        self._discover_sources = discover_sources
        self._read_status = read_status
        self._start_session = start_session
        self._stop_session = stop_session
        self._test_connection = test_connection
        self._release_pair_fetcher = release_pair_fetcher
        self._discover_tailscale_peers = discover_tailscale_peers
        self._scan_host_keys = scan_host_keys
        self._discover_profiles = discover_profiles
        self._inspect_profile = inspect_profile
        self._bootstrap_factory = bootstrap_factory
        self._windows_bootstrap_factory = windows_bootstrap_factory
        self._macos_bootstrap_factory = macos_bootstrap_factory
        self._identities = identity_store
        self._updates = update_service
        self._migration = MigrationService(status_reader=self._read_status)
        self._migration_bootstrap_key_path = ""
        self._migration_in_progress = False
        self._diagnostics = diagnostic_store or DiagnosticStore(logs_dir())
        self._trust = trust_store
        self._remote_remover = remote_remove
        self._plugin_remover = plugin_remove
        self._source_history = source_history or SourceHistoryStore()

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = str(request.get("operation", "unknown")).strip() or "unknown"
        try:
            return self._execute(request)
        except (OSError, ValueError, SessionError) as exc:
            return self._error(
                str(exc),
                stage=f"application.{operation}",
                code="operation-failed",
            )

    def _execute(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = str(request.get("operation", "")).strip()
        migration_blocked_operations = {
            "configure",
            "source.pin",
            "source-history.clear",
            "mix-profile.save",
            "mix-profile.delete",
            "mix-profile.load",
            "profile.save",
            "connection.save",
            "receiver.linux-setup",
            "receiver.windows-setup",
            "receiver.macos-setup",
            "removal.plan",
            "removal.apply",
            "uninstall.plan",
            "uninstall.apply",
        }
        if (
            operation in migration_blocked_operations
            and not self._migration_in_progress
            and self._migration.inspect()["detected"]
        ):
            return self._error(
                "legacy configuration requires an explicit migration choice",
                stage=f"application.{operation}",
                code="migration-required",
            )
        if operation == "inspect":
            return self._inspect()
        if operation == "configure":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "request payload must be an object",
                    stage="application.configure",
                    code="invalid-request",
                )
            configured_payload = dict(payload)
            if "sourceIds" in configured_payload and "sourceChoiceIds" in configured_payload:
                return self._error(
                    "provide sourceChoiceIds or sourceIds, not both",
                    stage="application.configure",
                    code="invalid-request",
                )
            if "sourceChoiceIds" in configured_payload:
                choice_ids = configured_payload.get("sourceChoiceIds", [])
                if not isinstance(choice_ids, list):
                    return self._error(
                        "sourceChoiceIds must be an array",
                        stage="application.configure",
                        code="invalid-request",
                    )
                config = load_config()
                choice_selection = resolve_choice_ids(
                    self._discover_sources(),
                    config.get("sourceMatchers", [])
                    + config.get("pinnedSourceMatchers", [])
                    + self._source_history.load(),
                    [str(item) for item in choice_ids],
                )
                configured_payload["sourceMatchers"] = choice_selection[
                    "sourceMatchers"
                ]
                configured_payload["sourceIds"] = choice_selection["sourceIds"]
            elif "sourceIds" in configured_payload:
                source_ids = configured_payload.get("sourceIds", [])
                if not isinstance(source_ids, list):
                    return self._error(
                        "sourceIds must be an array",
                        stage="application.configure",
                        code="invalid-request",
                    )
                configured_payload["sourceMatchers"] = matchers_for_source_ids(
                    self._discover_sources(), [str(item) for item in source_ids]
                )
            config = config_from_payload(configured_payload)
            save_config(config)
            return {
                "ok": True,
                "schemaVersion": 1,
                "config": public_config(config),
            }
        if operation == "source.pin":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "Source pin payload must be an object",
                    stage="application.source.pin",
                    code="invalid-request",
                )
            choice_id = str(payload.get("sourceChoiceId", "")).strip()
            if not choice_id or not isinstance(payload.get("pinned"), bool):
                return self._error(
                    "sourceChoiceId and a pinned boolean are required",
                    stage="application.source.pin",
                    code="invalid-request",
                )
            config = load_config()
            sources = self._discover_sources()
            selection = resolve_choice_ids(
                sources,
                config.get("sourceMatchers", [])
                + config.get("pinnedSourceMatchers", [])
                + self._source_history.load(),
                [choice_id],
            )
            matcher = selection["sourceMatchers"][0]
            pinned_matchers = list(config.get("pinnedSourceMatchers", []))
            if payload["pinned"] and matcher not in pinned_matchers:
                pinned_matchers.append(matcher)
            if not payload["pinned"]:
                pinned_matchers = [
                    value for value in pinned_matchers if value != matcher
                ]
            config["pinnedSourceMatchers"] = pinned_matchers
            save_config(config)
            return {
                "ok": True,
                "schemaVersion": 1,
                "config": public_config(config),
                "sourceChoiceId": choice_id,
                "pinned": payload["pinned"],
            }
        if operation == "source-history.clear":
            sources = self._discover_sources()
            self._source_history.clear(sources)
            return {
                "ok": True,
                "schemaVersion": 1,
                "cleared": True,
            }
        if operation == "migration.inspect":
            return self._migration.inspect()
        if operation == "migration.connection":
            return {
                "ok": True,
                "schemaVersion": 1,
                "connection": self._migration.legacy_connection(),
                "displayOnlyAfterImportChoice": True,
            }
        if operation == "migration.plan":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "migration plan payload must be an object",
                    stage="migration.plan",
                    code="invalid-request",
                )
            plan = self._migration.plan(
                str(payload.get("choice", "")),
                platform=str(payload.get("platform", "")),
            )
            return {"ok": True, "schemaVersion": 1, "plan": plan}
        if operation == "migration.apply":
            payload = request.get("payload", {})
            if not isinstance(payload, dict) or not payload.get("approvedPlanHash"):
                return self._error(
                    "exact migration plan approval is required",
                    stage="migration.apply",
                    code="approval-required",
                )
            plan = payload.get("plan")
            if not isinstance(plan, dict):
                return self._error(
                    "migration plan must be an object",
                    stage="migration.apply",
                    code="invalid-request",
                )

            if plan.get("choice") != "start-fresh":
                legacy_connection = self._migration.legacy_connection()
                candidates = self._host_key_candidates(legacy_connection)
                trust = self._trust_store().inspect(legacy_connection, candidates)
                if trust["status"] != "trusted":
                    return self._error(
                        "legacy receiver host key must be approved before migration",
                        stage="migration.apply",
                        code="trust-required",
                    )

            def secure_import(
                platform: str,
                connection: dict[str, Any],
                setup_payload: dict[str, Any],
            ) -> dict[str, Any]:
                approved_setup = dict(setup_payload)
                bootstrap_key_path = str(
                    approved_setup.pop("_legacyBootstrapKeyPath", "")
                )
                approved_setup["connection"] = connection
                self._migration_bootstrap_key_path = bootstrap_key_path
                self._migration_in_progress = True
                try:
                    setup = self._execute(
                        {
                            "operation": f"receiver.{platform}-setup",
                            "payload": approved_setup,
                        }
                    )
                finally:
                    self._migration_bootstrap_key_path = ""
                    self._migration_in_progress = False
                if not setup.get("ok"):
                    setup_result = (
                        setup.get("setup", {})
                        if isinstance(setup.get("setup"), dict)
                        else {}
                    )
                    return {
                        "ok": False,
                        "verified": False,
                        "stage": setup.get("diagnostic", {}).get(
                            "stage", f"receiver.{platform}-setup"
                        ),
                        "rollbackIncomplete": setup_result.get("rollbackIncomplete")
                        is True,
                    }
                setup_result = setup.get("setup", {})
                return {
                    "ok": True,
                    "verified": setup_result.get("verified") is True,
                    "connection": setup.get("connection"),
                    "config": setup.get("config"),
                }

            result = self._migration.execute(
                plan,
                approved_plan_hash=str(payload["approvedPlanHash"]),
                secure_import=secure_import,
                setup_payload=(
                    payload.get("setupPayload")
                    if isinstance(payload.get("setupPayload"), dict)
                    else {}
                ),
            )
            if not result.get("ok"):
                failure = self._error(
                    str(result.get("error", "legacy migration failed")),
                    stage=f"migration.{result.get('stage', 'apply')}",
                    code=(
                        "session-active"
                        if result.get("deferred")
                        else (
                            "rollback-incomplete"
                            if result.get("rollbackIncomplete")
                            else "migration-rolled-back"
                        )
                    ),
                )
                failure["migration"] = result
                return failure
            return {"ok": True, "schemaVersion": 1, "migration": result}
        if operation == "indicator.inspect":
            status = indicator_status(self._read_status(), load_config())
            write_indicator_heartbeat()
            return status
        if operation == "lifecycle.event":
            payload = request.get("payload", {})
            event = str(payload.get("event", "")) if isinstance(payload, dict) else ""
            return handle_lifecycle_event(
                event,
                status_reader=self._read_status,
                stopper=self._stop_session,
            )
        if operation == "mix-profile.save":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "Mix Profile payload must be an object",
                    stage="mix-profile.save",
                    code="invalid-request",
                )
            source_ids = payload.get("sourceIds", [])
            choice_ids = payload.get("sourceChoiceIds", [])
            if not isinstance(source_ids, list) or not isinstance(choice_ids, list):
                return self._error(
                    "Mix Profile source IDs must be arrays",
                    stage="mix-profile.save",
                    code="invalid-request",
                )
            if "sourceIds" in payload and "sourceChoiceIds" in payload:
                return self._error(
                    "provide sourceChoiceIds or sourceIds, not both",
                    stage="mix-profile.save",
                    code="invalid-request",
                )
            profile_value = dict(payload)
            if "sourceChoiceIds" in payload:
                available_sources = self._discover_sources()
                selection_config = load_config()
                choice_selection = resolve_choice_ids(
                    available_sources,
                    selection_config.get("sourceMatchers", [])
                    + selection_config.get("pinnedSourceMatchers", [])
                    + self._source_history.load(),
                    [str(item) for item in choice_ids],
                )
                profile_value["sourceMatchers"] = choice_selection[
                    "sourceMatchers"
                ]
            elif "sourceIds" in payload:
                available_sources = self._discover_sources()
                profile_value["sourceMatchers"] = matchers_for_source_ids(
                    available_sources, [str(item) for item in source_ids]
                )
            if "sourceChoiceIds" in payload or "sourceIds" in payload:
                save_resolution = resolve_source_matchers(
                    available_sources, profile_value["sourceMatchers"]
                )
                if _source_resolution_requires_review(
                    profile_value["sourceMatchers"], save_resolution
                ):
                    profile_value["quickStartEnabled"] = False
            profile = normalize_mix_profile(profile_value)
            config = load_config()
            config["mixProfiles"] = upsert_mix_profile(
                config.get("mixProfiles", []), profile
            )
            save_config(config)
            return {
                "ok": True,
                "schemaVersion": 1,
                "profile": profile,
                "config": public_config(config),
            }
        if operation == "mix-profile.delete":
            payload = request.get("payload", {})
            profile_id = str(payload.get("profileId", "")) if isinstance(payload, dict) else ""
            config = load_config()
            find_mix_profile(config.get("mixProfiles", []), profile_id)
            config["mixProfiles"] = [
                profile
                for profile in config.get("mixProfiles", [])
                if profile.get("id") != profile_id
            ]
            save_config(config)
            return {"ok": True, "schemaVersion": 1, "deletedProfileId": profile_id}
        if operation in {"mix-profile.load", "mix-profile.quick-start"}:
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "Mix Profile payload must be an object",
                    stage=operation,
                    code="invalid-request",
                )
            if operation == "mix-profile.quick-start" and payload.get(
                "quickStartConfirmed"
            ) is not True:
                return self._error(
                    "Quick Start requires an explicit confirmed action",
                    stage=operation,
                    code="confirmation-required",
                )
            profile = find_mix_profile(
                load_config().get("mixProfiles", []), str(payload.get("profileId", ""))
            )
            sources = self._discover_sources()
            resolution = resolve_source_matchers(sources, profile["sourceMatchers"])
            profile_config = self._mix_profile_config(profile, resolution["selectedIds"])
            if operation == "mix-profile.load":
                save_config(profile_config)
                return {
                    "ok": True,
                    "schemaVersion": 1,
                    "profile": profile,
                    "config": public_config(profile_config),
                    "resolution": resolution,
                    "openMixer": True,
                }
            if profile["requiresCaptureConfirmation"]:
                return self._quick_start_blocked(
                    profile,
                    profile_config,
                    resolution,
                    reason="capture-confirmation-required",
                    message="Capture Sources require confirmation in the mixer",
                )
            if not profile["quickStartEnabled"]:
                return self._quick_start_blocked(
                    profile,
                    profile_config,
                    resolution,
                    reason="quick-start-disabled",
                    message="Quick Start is not enabled for this Mix Profile",
                )
            has_armed_playback = any(
                matcher.get("kind") == "playback"
                for matcher in profile["sourceMatchers"]
            )
            if (
                _source_resolution_requires_review(
                    profile["sourceMatchers"], resolution
                )
                or (not resolution["selectedIds"] and not has_armed_playback)
            ):
                return self._quick_start_blocked(
                    profile,
                    profile_config,
                    resolution,
                    reason="source-resolution-required",
                    message="Quick Start sources are missing or ambiguous",
                )
            if self._read_status().get("active"):
                return self._quick_start_blocked(
                    profile,
                    profile_config,
                    resolution,
                    reason="session-active",
                    message="Quick Start cannot replace an active Session",
                )
            status = self._start_session(profile_config)
            return {
                "ok": True,
                "schemaVersion": 1,
                "started": True,
                "profile": profile,
                "config": public_config(profile_config),
                "status": status,
            }
        if operation == "updates.check":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "update request payload must be an object",
                    stage="updates.check",
                    code="invalid-request",
                )
            platform = str(payload.get("platform", ""))
            updates = self._update_service(platform)
            config = load_config()
            connection = self._test_connection(config.get("remote", {}))
            capabilities = connection.get("capabilities", {})
            if connection.get("ok") is not True or not isinstance(capabilities, dict):
                return self._error(
                    "Receiver capabilities could not be verified before update planning",
                    stage="updates.check",
                    code="connection-failed",
                )
            metadata, signature = self._release_pair_fetcher(PINNED_RECEIVER_RELEASE)
            plan = updates.plan(
                metadata,
                signature,
                platform=platform,
                installed={
                    "helperVersion": capabilities.get("helperVersion", ""),
                    "protocol": capabilities.get("protocolVersion"),
                },
            )
            return {"ok": True, "schemaVersion": 1, "plan": plan}
        if operation == "updates.apply-pinned":
            payload = request.get("payload", {})
            plan = payload.get("plan") if isinstance(payload, dict) else None
            approved = str(payload.get("approvedPlanHash", "")) if isinstance(payload, dict) else ""
            if not isinstance(plan, dict) or not approved or approved != plan.get("planHash"):
                return self._error(
                    "exact update plan approval is required",
                    stage="updates.apply-pinned",
                    code="approval-required",
                )
            if self._read_status().get("active"):
                return self._error(
                    "Receiver updates wait until the active Session is stopped",
                    stage="updates.apply-pinned",
                    code="session-active",
                )
            updates = self._update_service(str(plan.get("platform", "")))
            metadata, signature = self._release_pair_fetcher(PINNED_RECEIVER_RELEASE)
            result = updates.execute(
                plan,
                metadata,
                signature,
                approved_plan_hash=approved,
            )
            if not result.get("ok"):
                failure = self._error(
                    str(result.get("error", "verified update failed")),
                    stage="updates.apply-pinned",
                    code=(
                        "rollback-incomplete"
                        if result.get("rollbackIncomplete")
                        else "update-failed"
                    ),
                )
                failure["update"] = result
                return failure
            return {"ok": True, "schemaVersion": 1, "update": result}
        if operation == "updates.plan":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "update request payload must be an object",
                    stage="updates.plan",
                    code="invalid-request",
                )
            metadata = payload.get("metadata", "")
            signature = payload.get("signature", "")
            installed = payload.get("installed", {})
            if not isinstance(metadata, str) or not isinstance(installed, dict):
                return self._error(
                    "update metadata and installed versions are invalid",
                    stage="updates.plan",
                    code="invalid-request",
                )
            updates = self._update_service(str(payload.get("platform", "")))
            plan = updates.plan(
                metadata.encode("utf-8"),
                signature,
                platform=str(payload.get("platform", "")),
                installed=installed,
            )
            return {"ok": True, "schemaVersion": 1, "plan": plan}
        if operation == "updates.apply":
            payload = request.get("payload", {})
            if not isinstance(payload, dict) or not payload.get("approvedPlanHash"):
                return self._error(
                    "exact update plan approval is required",
                    stage="updates.apply",
                    code="approval-required",
                )
            if self._read_status().get("active"):
                return self._error(
                    "Receiver updates wait until the active Session is stopped",
                    stage="updates.apply",
                    code="session-active",
                )
            metadata = payload.get("metadata", "")
            plan = payload.get("plan")
            if not isinstance(metadata, str) or not isinstance(plan, dict):
                return self._error(
                    "approved update request is invalid",
                    stage="updates.apply",
                    code="invalid-request",
                )
            if str(payload["approvedPlanHash"]) != str(plan.get("planHash", "")):
                return self._error(
                    "exact update plan approval is required",
                    stage="updates.apply",
                    code="approval-required",
                )
            updates = self._update_service(str(plan.get("platform", "")))
            result = updates.execute(
                plan,
                metadata.encode("utf-8"),
                payload.get("signature", ""),
                approved_plan_hash=str(payload["approvedPlanHash"]),
            )
            if not result.get("ok"):
                failure = self._error(
                    str(result.get("error", "verified update failed")),
                    stage="updates.apply",
                    code=(
                        "rollback-incomplete"
                        if result.get("rollbackIncomplete")
                        else "update-failed"
                    ),
                )
                failure["update"] = result
                return failure
            return {"ok": True, "schemaVersion": 1, "update": result}
        if operation == "receiver.quiet-test":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "quiet test payload must be an object",
                    stage="receiver.quiet-test",
                    code="invalid-request",
                )
            dbfs = payload.get("dbfs", -32)
            if isinstance(dbfs, bool) or not isinstance(dbfs, int):
                return self._error(
                    "Receiver test level must be an integer",
                    stage="receiver.quiet-test",
                    code="invalid-request",
                )
            config = load_config()
            return quiet_test(config["remote"], dbfs)
        if operation == "receiver.macos-plan":
            connection = self._connection_from_request(request)
            bootstrap = self._macos_bootstrap(connection)
            tracer = MacOsSetupTracer(
                apply=bootstrap.apply,
                verify=bootstrap.verify,
                rollback=bootstrap.rollback,
            )
            return {
                "ok": True,
                "schemaVersion": 1,
                "experimental": True,
                "plan": tracer.trace_plan(bootstrap.probe()),
            }
        if operation == "receiver.macos-setup":
            payload = request.get("payload", {})
            if not isinstance(payload, dict) or payload.get("changesApproved") is not True:
                return self._error(
                    "Experimental macOS Receiver changes require approval",
                    stage="receiver.macos-setup",
                    code="confirmation-required",
                )
            if payload.get("experimentalConfirmed") is not True:
                return self._error(
                    "Experimental macOS status requires explicit confirmation",
                    stage="receiver.macos-setup",
                    code="experimental-confirmation-required",
                )
            encrypted_identity = payload.get("encryptedIdentity", False)
            if not isinstance(encrypted_identity, bool):
                return self._error(
                    "encrypted identity choice must be a boolean",
                    stage="receiver.macos-setup",
                    code="invalid-request",
                )
            connection = self._connection_from_request(request)
            bootstrap = self._macos_bootstrap(connection)
            tracer = MacOsSetupTracer(
                apply=bootstrap.apply,
                verify=bootstrap.verify,
                rollback=bootstrap.rollback,
            )
            plan = tracer.trace_plan(bootstrap.probe())
            approved_plan_hash = str(payload.get("approvedPlanHash", ""))
            if approved_plan_hash != plan["planHash"]:
                return self._error(
                    "Experimental macOS setup plan changed before approval",
                    stage="receiver.macos-setup",
                    code="plan-changed",
                )
            identity_id = connection_id(connection)
            identity = self._identity_store().generate(
                identity_id,
                encrypted=encrypted_identity,
            )
            result = tracer.execute(
                plan,
                identity,
                approved_plan_hash=approved_plan_hash,
            )
            if not result["ok"]:
                if result["rollback"] == "complete":
                    self._identity_store().revoke_local(identity_id)
                failure = self._error(
                    str(result.get("error", "Experimental macOS Receiver setup failed")),
                    stage="receiver.macos-setup",
                    code=(
                        "rollback-incomplete"
                        if result.get("rollbackIncomplete")
                        else "setup-rolled-back"
                    ),
                )
                failure["experimental"] = True
                failure["setup"] = result
                return failure
            connection["securityLevel"] = "receiver-only"
            connection["managedIdentityId"] = identity_id
            connection["receiverPlatform"] = "macos"
            connection["experimental"] = True
            config = config_from_payload(
                {
                    "connection": connection,
                    "remote": {"keyPath": identity["privateKeyPath"]},
                }
            )
            try:
                save_config(config)
            except (OSError, ValueError) as exc:
                rollback = bootstrap.rollback(plan, identity)
                if rollback.get("complete"):
                    self._identity_store().revoke_local(identity_id)
                failure = self._error(
                    "macOS setup succeeded but local configuration could not be saved",
                    stage="receiver.macos-setup",
                    code=(
                        "setup-rolled-back"
                        if rollback.get("complete")
                        else "rollback-incomplete"
                    ),
                )
                failure["experimental"] = True
                failure["cause"] = type(exc).__name__
                failure["setup"] = {
                    "schemaVersion": 1,
                    "ok": False,
                    "experimental": True,
                    "rollback": (
                        "complete" if rollback.get("complete") else "incomplete"
                    ),
                    "rollbackIncomplete": not bool(rollback.get("complete")),
                }
                return failure
            return {
                "ok": True,
                "schemaVersion": 1,
                "experimental": True,
                "setup": result,
                "connection": connection,
                "config": public_config(config),
            }
        if operation == "receiver.windows-plan":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "Windows setup payload must be an object",
                    stage="receiver.windows-plan",
                    code="invalid-request",
                )
            administrator_confirmed = payload.get("administratorConfirmed", False)
            if not isinstance(administrator_confirmed, bool):
                return self._error(
                    "administrator confirmation must be a boolean",
                    stage="receiver.windows-plan",
                    code="invalid-request",
                )
            connection = self._connection_from_request(request)
            bootstrap = self._windows_bootstrap(connection)
            tracer = WindowsSetupTracer(
                apply=bootstrap.apply,
                verify=bootstrap.verify,
                rollback=bootstrap.rollback,
            )
            probe = bootstrap.probe()
            probe["sshPort"] = connection["port"]
            return {
                "ok": True,
                "schemaVersion": 1,
                "plan": tracer.trace_plan(
                    probe,
                    administrator_confirmed=administrator_confirmed,
                ),
            }
        if operation == "receiver.windows-setup":
            payload = request.get("payload", {})
            if not isinstance(payload, dict) or payload.get("changesApproved") is not True:
                return self._error(
                    "Windows Receiver changes require approval",
                    stage="receiver.windows-setup",
                    code="confirmation-required",
                )
            administrator_confirmed = payload.get("administratorConfirmed", False)
            encrypted_identity = payload.get("encryptedIdentity", False)
            if not isinstance(administrator_confirmed, bool) or not isinstance(
                encrypted_identity, bool
            ):
                return self._error(
                    "Windows confirmation choices must be boolean",
                    stage="receiver.windows-setup",
                    code="invalid-request",
                )
            connection = self._connection_from_request(request)
            bootstrap = self._windows_bootstrap(connection)
            tracer = WindowsSetupTracer(
                apply=bootstrap.apply,
                verify=bootstrap.verify,
                rollback=bootstrap.rollback,
            )
            probe = bootstrap.probe()
            probe["sshPort"] = connection["port"]
            plan = tracer.trace_plan(
                probe,
                administrator_confirmed=administrator_confirmed,
            )
            if plan.get("administratorConfirmationRequired") and not administrator_confirmed:
                return self._error(
                    "administrator-capable Windows setup requires explicit confirmation",
                    stage="receiver.windows-setup",
                    code="administrator-confirmation-required",
                )
            approved_plan_hash = str(payload.get("approvedPlanHash", ""))
            if approved_plan_hash != plan["planHash"]:
                return self._error(
                    "Windows setup plan changed before approval",
                    stage="receiver.windows-setup",
                    code="plan-changed",
                )
            if (
                any(
                    change.get("requiresPrivilege") is True
                    for change in plan.get("changes", [])
                )
                and plan.get("bootstrapElevated") is not True
            ):
                return self._error(
                    "Windows privileged changes require elevated Bootstrap Authentication",
                    stage="receiver.windows-setup",
                    code="elevated-bootstrap-required",
                )
            identity_id = connection_id(connection)
            identity = self._identity_store().generate(
                identity_id,
                encrypted=encrypted_identity,
            )
            result = tracer.execute(
                plan,
                identity,
                approved_plan_hash=approved_plan_hash,
            )
            if not result["ok"]:
                if result["rollback"] == "complete":
                    self._identity_store().revoke_local(identity_id)
                failure = self._error(
                    str(result.get("error", "Windows Receiver setup failed")),
                    stage="receiver.windows-setup",
                    code=(
                        "rollback-incomplete"
                        if result.get("rollbackIncomplete")
                        else "setup-rolled-back"
                    ),
                )
                failure["setup"] = result
                return failure
            connection["securityLevel"] = "receiver-only"
            connection["managedIdentityId"] = identity_id
            connection["receiverPlatform"] = "windows"
            config = config_from_payload(
                {
                    "connection": connection,
                    "remote": {"keyPath": identity["privateKeyPath"]},
                }
            )
            previous_config = load_config()
            try:
                save_config(config)
            except (OSError, ValueError) as exc:
                rollback = bootstrap.rollback(plan, identity)
                if rollback.get("complete"):
                    self._identity_store().revoke_local(identity_id)
                failure = self._error(
                    "Windows setup succeeded but local configuration could not be saved",
                    stage="receiver.windows-setup",
                    code=(
                        "setup-rolled-back"
                        if rollback.get("complete")
                        else "rollback-incomplete"
                    ),
                )
                failure["setup"] = {
                    "schemaVersion": 1,
                    "ok": False,
                    "verified": True,
                    "rollback": (
                        "complete" if rollback.get("complete") else "incomplete"
                    ),
                    "rollbackIncomplete": not bool(rollback.get("complete")),
                }
                failure["cause"] = type(exc).__name__
                return failure
            commit = getattr(bootstrap, "commit", lambda: True)
            try:
                committed = bool(commit())
            except (OSError, ValueError):
                committed = False
            if not committed:
                rollback = bootstrap.rollback(plan, identity)
                local_restored = True
                try:
                    save_config(previous_config)
                except (OSError, ValueError):
                    local_restored = False
                complete = bool(rollback.get("complete")) and local_restored
                if complete:
                    self._identity_store().revoke_local(identity_id)
                failure = self._error(
                    "Windows setup verification passed but transaction commit failed",
                    stage="receiver.windows-setup",
                    code=("setup-rolled-back" if complete else "rollback-incomplete"),
                )
                failure["setup"] = {
                    "schemaVersion": 1,
                    "ok": False,
                    "verified": True,
                    "rollback": "complete" if complete else "incomplete",
                    "rollbackIncomplete": not complete,
                }
                return failure
            return {
                "ok": True,
                "schemaVersion": 1,
                "setup": result,
                "connection": connection,
                "config": public_config(config),
            }
        if operation == "receiver.linux-plan":
            connection = self._connection_from_request(request)
            bootstrap = self._linux_bootstrap(connection)
            tracer = LinuxSetupTracer(
                apply=bootstrap.apply,
                verify=bootstrap.verify,
                rollback=bootstrap.rollback,
            )
            return {
                "ok": True,
                "schemaVersion": 1,
                "plan": tracer.trace_plan(bootstrap.probe()),
            }
        if operation == "receiver.linux-setup":
            payload = request.get("payload", {})
            if not isinstance(payload, dict) or payload.get("changesApproved") is not True:
                return self._error(
                    "Linux Receiver changes require approval",
                    stage="receiver.linux-setup",
                    code="confirmation-required",
                )
            connection = self._connection_from_request(request)
            bootstrap = self._linux_bootstrap(connection)
            tracer = LinuxSetupTracer(
                apply=bootstrap.apply,
                verify=bootstrap.verify,
                rollback=bootstrap.rollback,
            )
            plan = tracer.trace_plan(bootstrap.probe())
            approved_plan_hash = str(payload.get("approvedPlanHash", ""))
            if approved_plan_hash != plan["planHash"]:
                return self._error(
                    "Linux setup plan changed before approval",
                    stage="receiver.linux-setup",
                    code="plan-changed",
                )
            identity_id = connection_id(connection)
            encrypted_identity = payload.get("encryptedIdentity", False)
            if not isinstance(encrypted_identity, bool):
                return self._error(
                    "encrypted identity choice must be a boolean",
                    stage="receiver.linux-setup",
                    code="invalid-request",
                )
            identity = self._identity_store().generate(
                identity_id,
                encrypted=encrypted_identity,
            )
            result = tracer.execute(
                plan,
                identity,
                approved_plan_hash=approved_plan_hash,
            )
            if not result["ok"]:
                if result["rollback"] == "complete":
                    self._identity_store().revoke_local(identity_id)
                failure = self._error(
                    str(result.get("error", "Linux Receiver setup failed")),
                    stage="receiver.linux-setup",
                    code=(
                        "rollback-incomplete"
                        if result.get("rollbackIncomplete")
                        else "setup-rolled-back"
                    ),
                )
                failure["setup"] = result
                return failure
            connection["securityLevel"] = "receiver-only"
            connection["managedIdentityId"] = identity_id
            connection["receiverPlatform"] = "linux"
            config = config_from_payload(
                {
                    "connection": connection,
                    "remote": {"keyPath": identity["privateKeyPath"]},
                }
            )
            try:
                save_config(config)
            except (OSError, ValueError) as exc:
                rollback = bootstrap.rollback(plan, identity)
                if rollback.get("complete"):
                    self._identity_store().revoke_local(identity_id)
                failure = self._error(
                    "Receiver setup succeeded but local configuration could not be saved",
                    stage="receiver.linux-setup",
                    code=(
                        "setup-rolled-back"
                        if rollback.get("complete")
                        else "rollback-incomplete"
                    ),
                )
                failure["setup"] = {
                    "schemaVersion": 1,
                    "ok": False,
                    "verified": True,
                    "rollback": (
                        "complete" if rollback.get("complete") else "incomplete"
                    ),
                    "rollbackIncomplete": not bool(rollback.get("complete")),
                }
                failure["cause"] = type(exc).__name__
                return failure
            return {
                "ok": True,
                "schemaVersion": 1,
                "setup": result,
                "connection": connection,
                "config": public_config(config),
            }
        if operation == "profile.inspect":
            payload = request.get("payload", {})
            profile = str(payload.get("profile", "")) if isinstance(payload, dict) else ""
            return {
                "ok": True,
                "schemaVersion": 1,
                "profile": self._inspect_profile(profile),
            }
        if operation == "profile.save":
            payload = request.get("payload", {})
            if not isinstance(payload, dict) or not bool(payload.get("userManagedConfirmed")):
                return self._error(
                    "user-managed SSH permissions require confirmation",
                    stage="profile.save",
                    code="confirmation-required",
                )
            profile = str(payload.get("profile", ""))
            inspected = self._inspect_profile(profile)
            connection = profile_connection(
                inspected,
                proxy_confirmed=bool(payload.get("proxyConfirmed", False)),
                expected_proxy_hash=str(payload.get("expectedProxyHash", "")),
                expected_effective_hash=str(payload.get("expectedEffectiveHash", "")),
            )
            connection = normalize_connection(connection)
            config = config_from_payload({"connection": connection})
            save_config(config)
            return {
                "ok": True,
                "schemaVersion": 1,
                "connection": connection,
                "config": public_config(config),
            }
        if operation == "connection.select":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "Connection selection payload must be an object",
                    stage="connection.select",
                    code="invalid-request",
                )
            if self._read_status().get("active"):
                return self._error(
                    "Stop the active Session before changing Receiver",
                    stage="connection.select",
                    code="session-active",
                )
            config = load_config()
            connection = find_connection(
                config.get("connections", []),
                str(payload.get("connectionId", "")),
            )
            config["connection"] = connection
            save_config(config)
            return {
                "ok": True,
                "schemaVersion": 1,
                "connectionId": connection_id(connection),
                "connection": connection,
                "config": public_config(config),
            }
        if operation == "connection.rename":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "Connection rename payload must be an object",
                    stage="connection.rename",
                    code="invalid-request",
                )
            config = load_config()
            connections, renamed = rename_connection(
                config.get("connections", []),
                str(payload.get("connectionId", "")),
                payload.get("receiverName", ""),
            )
            item_id = connection_id(renamed)
            config["connections"] = connections
            if isinstance(config.get("connection"), dict) and connection_id(
                config["connection"]
            ) == item_id:
                config["connection"] = renamed
            updated_profiles: list[dict[str, Any]] = []
            for profile in config.get("mixProfiles", []):
                updated = dict(profile)
                saved_profile_connection = updated.get("connection")
                if isinstance(saved_profile_connection, dict) and connection_id(
                    saved_profile_connection
                ) == item_id:
                    updated["connection"] = renamed
                updated_profiles.append(updated)
            config["mixProfiles"] = updated_profiles
            save_config(config)
            return {
                "ok": True,
                "schemaVersion": 1,
                "connectionId": item_id,
                "connection": renamed,
                "config": public_config(config),
            }
        if operation == "connection.save":
            payload = request.get("payload", {})
            connection_value = payload.get("connection") if isinstance(payload, dict) else None
            connection = normalize_connection(connection_value)
            if connection.get("securityLevel") == "receiver-only":
                saved_connection = load_config().get("connection")
                if saved_connection != connection:
                    return self._error(
                        "Managed Identity status requires verified Receiver setup",
                        stage="connection.save",
                        code="managed-setup-required",
                    )
            if connection["type"] == "openssh-profile":
                return self._error(
                    "OpenSSH Profile Connections require profile confirmation",
                    stage="connection.save",
                    code="profile-confirmation-required",
                )
            candidates = self._host_key_candidates(connection)
            trust = self._trust_store().inspect(connection, candidates)
            if trust["status"] != "trusted":
                return self._error(
                    "receiver host key must be approved before saving",
                    stage="connection.save",
                    code="trust-required",
                )
            config = config_from_payload({"connection": connection})
            save_config(config)
            return {
                "ok": True,
                "schemaVersion": 1,
                "connection": connection,
                "config": public_config(config),
            }
        if operation == "trust.inspect":
            connection = self._connection_from_request(request)
            candidates = self._host_key_candidates(connection)
            return {
                "ok": True,
                "schemaVersion": 1,
                "connection": connection,
                "trust": self._trust_store().inspect(connection, candidates),
            }
        if operation == "trust.approve":
            payload = request.get("payload", {})
            connection = self._connection_from_request(request)
            expected = payload.get("expectedFingerprints", []) if isinstance(payload, dict) else []
            if not isinstance(expected, list) or not expected:
                return self._error(
                    "expected host-key fingerprints are required",
                    stage="trust.approve",
                    code="invalid-request",
                )
            candidates = self._host_key_candidates(connection)
            inspected = self._trust_store().inspect(connection, candidates)
            if sorted(map(str, expected)) != sorted(inspected["candidateFingerprints"]):
                return self._error(
                    "receiver host keys changed before approval",
                    stage="trust.approve",
                    code="candidate-changed",
                )
            approved = self._trust_store().approve(connection, candidates)
            return {
                "ok": True,
                "schemaVersion": 1,
                "connection": connection,
                "trust": approved,
            }
        if operation == "removal.inspect":
            inspected = self._removal_service().inspect()
            return {"ok": True, **inspected}
        if operation in {"removal.plan", "uninstall.plan"}:
            plan = self._removal_service().plan(uninstall=operation == "uninstall.plan")
            return {"ok": True, "schemaVersion": 1, "plan": plan}
        if operation in {"removal.apply", "uninstall.apply"}:
            payload = request.get("payload", {})
            if not isinstance(payload, dict) or not isinstance(payload.get("plan"), dict):
                return self._error(
                    "an exact removal plan is required",
                    stage=operation,
                    code="invalid-request",
                )
            result = self._removal_service().execute(
                payload["plan"],
                approved_plan_hash=str(payload.get("approvedPlanHash", "")),
                abandon_pending=bool(payload.get("abandonPending", False)),
                abandonment_confirmation=str(
                    payload.get("abandonmentConfirmation", "")
                ),
            )
            if not result.get("ok"):
                if result.get("localSensitiveStateRemoved"):
                    result["diagnostic"] = {
                        "schemaVersion": 1,
                        "stage": operation,
                        "code": str(result.get("code", "cleanup-failed")),
                        "message": str(result.get("message", "cleanup failed")),
                    }
                    result["error"] = result["diagnostic"]["message"]
                    return result
                failure = self._error(
                    str(result.get("message", "cleanup failed")),
                    stage=operation,
                    code=str(result.get("code", "cleanup-failed")),
                )
                failure.update(result)
                failure["diagnostic"] = failure.get("diagnostic", {})
                return failure
            return result
        if operation == "diagnostics.preview":
            payload = request.get("payload", {})
            include_logs = (
                bool(payload.get("includeLogs", False))
                if isinstance(payload, dict)
                else False
            )
            return {
                "ok": True,
                "schemaVersion": 1,
                "report": self._diagnostics.preview_report(include_logs=include_logs),
            }
        if operation == "diagnostics.report-url":
            payload = request.get("payload", {})
            if not isinstance(payload, dict) or not isinstance(payload.get("body"), str):
                return self._error(
                    "reviewed diagnostic body is required",
                    stage="diagnostics.report-url",
                    code="invalid-request",
                )
            return {
                "ok": True,
                "schemaVersion": 1,
                "url": github_issue_url(
                    payload["body"],
                    title=str(payload.get("title", "SSH-mixer diagnostic")),
                ),
            }
        if operation == "diagnostics.configure":
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                return self._error(
                    "request payload must be an object",
                    stage="diagnostics.configure",
                    code="invalid-request",
                )
            settings = self._diagnostics.configure_retention(
                str(payload.get("retentionPolicy", ""))
            )
            return {"ok": True, "schemaVersion": 1, "settings": settings}
        if operation == "diagnostics.contribute-url":
            return {
                "ok": True,
                "schemaVersion": 1,
                "url": GITHUB_CONTRIBUTING_URL,
            }
        if operation == "diagnostics.clear":
            self._diagnostics.clear()
            return {"ok": True, "schemaVersion": 1, "cleared": True}
        if operation == "diagnostics.verbose-next":
            self._diagnostics.enable_verbose_next_session()
            return {"ok": True, "schemaVersion": 1, "enabledForSessions": 1}
        return self._error(
            f"unknown operation: {operation or '<empty>'}",
            stage=f"application.{operation or 'unknown'}",
            code="unknown-operation",
        )

    def _mix_profile_config(
        self, profile: dict[str, Any], source_ids: list[str]
    ) -> dict[str, Any]:
        stream = profile["stream"]
        connection = profile["connection"]
        remote: dict[str, Any] = {
            "bitrate": stream["bitrate"],
            "connectTimeoutSeconds": stream["connectTimeoutSeconds"],
        }
        managed_identity_id = str(connection.get("managedIdentityId", ""))
        if managed_identity_id:
            remote["keyPath"] = str(
                keys_dir() / managed_identity_id / "id_ed25519"
            )
        return config_from_payload(
            {
                "connection": connection,
                "destination": profile["routeMode"],
                "sourceIds": source_ids,
                "sourceMatchers": profile["sourceMatchers"],
                "privacy": profile["privacy"],
                "remote": remote,
            }
        )

    def _quick_start_blocked(
        self,
        profile: dict[str, Any],
        config: dict[str, Any],
        resolution: dict[str, Any],
        *,
        reason: str,
        message: str,
    ) -> dict[str, Any]:
        # Opening the mixer applies the profile's non-temporary settings for a
        # later explicit Start, but save_config strips concrete source ids.
        save_config(config)
        result = self._error(
            message,
            stage="mix-profile.quick-start",
            code=reason,
        )
        result.update(
            {
                "openMixer": True,
                "reason": reason,
                "profile": profile,
                "config": public_config(config),
                "resolution": resolution,
                "recentCaptureIds": resolution["recentCaptureIds"],
            }
        )
        return result

    def _error(self, message: str, *, stage: str, code: str) -> dict[str, Any]:
        diagnostic = self._diagnostics.record(
            stage=stage,
            code=code,
            message=message,
            session_id="application",
        )
        return {
            "ok": False,
            "schemaVersion": 1,
            "error": diagnostic["message"],
            "diagnostic": diagnostic,
        }

    def _removal_service(self) -> RemovalService:
        return RemovalService(
            remote_remove=self._remote_remover or self._remove_receiver,
            status_reader=self._read_status,
            identity_store=self._identity_store(),
            trust_store=self._trust_store(),
            diagnostic_store=self._diagnostics,
            **({"plugin_remove": self._plugin_remover} if self._plugin_remover else {}),
        )

    def _remove_receiver(self, request: dict[str, Any]) -> dict[str, Any]:
        connection = normalize_connection(request.get("connection"))
        platform = str(request.get("platform", ""))
        bootstrap: LinuxBootstrap
        if platform == "linux":
            bootstrap = self._linux_bootstrap(connection)
        elif platform == "windows":
            bootstrap = self._windows_bootstrap(connection)
        elif platform == "macos":
            bootstrap = self._macos_bootstrap(connection)
        else:
            return {
                "ok": False,
                "verified": False,
                "code": "unsupported-receiver-platform",
            }
        return bootstrap.remove(
            {
                "privateKeyPath": str(request.get("privateKeyPath", "")),
                "publicKeyBody": str(request.get("publicKeyBody", "")),
            }
        )

    def _update_service(self, platform: str) -> UpdateService:
        if self._updates is not None:
            return self._updates
        trust_root = Path(__file__).resolve().parents[2] / "release" / "allowed_signers"
        if trust_root.is_symlink() or not trust_root.is_file():
            raise ValueError("signed update trust root is not configured")
        config = load_config()
        connection = config.get("connection")
        if not isinstance(connection, dict):
            raise ValueError("a verified Receiver Connection is required for updates")
        if (
            connection.get("securityLevel") != "receiver-only"
            or connection.get("receiverPlatform") != platform
        ):
            raise ValueError("updates require the matching receiver-only Connection")
        identity_id = str(connection.get("managedIdentityId", ""))
        identity = self._identity_store().load(identity_id)
        if platform == "linux":
            bootstrap = self._linux_bootstrap(connection)
        elif platform == "windows":
            bootstrap = self._windows_bootstrap(connection)
        elif platform == "macos":
            bootstrap = self._macos_bootstrap(connection)
        else:
            raise ValueError("update platform is unsupported")
        transaction = NativeUpdateTransaction(
            platform=platform,
            bootstrap=bootstrap,
            identity=identity,
        )
        verifier = ReleaseSignatureVerifier(trust_root)
        return UpdateService(
            signature_verifier=verifier.verify,
            installer=transaction.install,
            post_verify=transaction.verify,
            rollback=transaction.rollback,
            commit=transaction.commit,
            transaction_plan=transaction.plan,
        )

    def _identity_store(self) -> ManagedIdentityStore:
        if self._identities is None:
            self._identities = ManagedIdentityStore(keys_dir())
        return self._identities

    def _macos_bootstrap(self, connection: dict[str, Any]) -> MacOsBootstrap:
        address = None
        if connection["type"] == "tailscale":
            verified = verify_tailscale_peer(connection, self._discover_tailscale_peers())
            address = str(verified["address"])
        if self._macos_bootstrap_factory is not None:
            return self._macos_bootstrap_factory(connection, address)
        return MacOsBootstrap(
            connection,
            known_hosts=trust_dir() / "known_hosts",
            address=address,
            bootstrap_key_path=self._migration_bootstrap_key_path,
        )

    def _windows_bootstrap(self, connection: dict[str, Any]) -> WindowsBootstrap:
        address = None
        if connection["type"] == "tailscale":
            verified = verify_tailscale_peer(connection, self._discover_tailscale_peers())
            address = str(verified["address"])
        if self._windows_bootstrap_factory is not None:
            return self._windows_bootstrap_factory(connection, address)
        return WindowsBootstrap(
            connection,
            known_hosts=trust_dir() / "known_hosts",
            address=address,
            bootstrap_key_path=self._migration_bootstrap_key_path,
        )

    def _linux_bootstrap(self, connection: dict[str, Any]) -> LinuxBootstrap:
        address = None
        if connection["type"] == "tailscale":
            verified = verify_tailscale_peer(connection, self._discover_tailscale_peers())
            address = str(verified["address"])
        if self._bootstrap_factory is not None:
            return self._bootstrap_factory(connection, address)
        return LinuxBootstrap(
            connection,
            known_hosts=trust_dir() / "known_hosts",
            address=address,
            bootstrap_key_path=self._migration_bootstrap_key_path,
        )

    def _trust_store(self) -> TrustStore:
        if self._trust is None:
            self._trust = TrustStore(trust_dir())
        return self._trust

    def _connection_from_request(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload", {})
        value = payload.get("connection") if isinstance(payload, dict) else None
        return normalize_connection(value)

    def _host_key_candidates(self, connection: dict[str, Any]) -> list[str]:
        address = None
        if connection["type"] == "tailscale":
            verified = verify_tailscale_peer(
                connection,
                self._discover_tailscale_peers(),
            )
            address = str(verified["address"])
        return self._scan_host_keys(connection, address=address)

    def _inspect(self) -> dict[str, Any]:
        config = load_config()
        sources = [dict(source) for source in self._discover_sources()]
        recent_matchers = self._source_history.observe(sources)
        resolution = resolve_source_matchers(
            sources, config.get("sourceMatchers", [])
        )
        concrete_ids = resolve_source_ids(sources, config.get("sourceIds", []))
        if not concrete_ids:
            concrete_ids = resolution["selectedIds"]
        recent_capture_ids = set(resolution["recentCaptureIds"])
        for source in sources:
            source["recentChoice"] = str(source.get("id", "")) in recent_capture_ids
        public = public_config(config)
        public["sourceIds"] = concrete_ids
        public["sourceResolution"] = resolution
        migration = self._migration.inspect()
        return {
            "ok": True,
            "schemaVersion": 1,
            "sources": sources,
            "sourceChoices": build_source_choices(
                sources,
                config.get("sourceMatchers", []),
                config.get("pinnedSourceMatchers", []),
                recent_matchers,
            ),
            "connectionOptions": {
                "tailscaleRecommended": True,
                "tailscalePeers": self._discover_tailscale_peers(),
                "openSshProfiles": self._discover_profiles(),
            },
            "status": self._read_status(),
            "migration": migration,
            "removal": self._removal_service().inspect(),
            "diagnostics": self._diagnostics.retention_settings(),
            "componentVersions": {
                "plugin": PLUGIN_VERSION,
                "companion": dict(COMPANION_VERSIONS),
                "receiver": dict(RECEIVER_VERSIONS),
                "protocol": PROTOCOL_VERSION,
                "signedUpdateTrustConfigured": self._updates is not None
                or (
                    (Path(__file__).resolve().parents[2] / "release" / "allowed_signers").is_file()
                    and not (Path(__file__).resolve().parents[2] / "release" / "allowed_signers").is_symlink()
                ),
            },
            "config": public,
        }
