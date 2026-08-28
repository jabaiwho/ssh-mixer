from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .application import MixerApplication
from .audio import discover_sources, resolve_source_ids, resolve_source_matchers
from .config import config_from_payload, load_config, logs_dir, public_config, save_config
from .diagnostics import DiagnosticStore
from .lifecycle import run_lifecycle_monitor
from .routing import RoutingError, build_route_plan
from .session import (
    SessionError,
    SessionWorker,
    normalize_status,
    require_migration_complete,
    run_foreground,
    start_session,
    stop_session,
    test_connection,
)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def read_payload(args: argparse.Namespace) -> dict[str, Any]:
    raw = ""
    if getattr(args, "stdin", False):
        raw = sys.stdin.read()
    elif getattr(args, "json", ""):
        raw = args.json
    if not raw.strip():
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("JSON payload must be an object")
    return loaded


def snapshot_command(_args: argparse.Namespace) -> int:
    emit(MixerApplication().execute({"operation": "inspect"}))
    return 0


def list_sources_command(args: argparse.Namespace) -> int:
    sources = discover_sources()
    if args.plain:
        for source in sources:
            print(
                f"{source['id']}\t{source.get('categoryLabel', source['type'])}"
                f"\t{source['label']}\t{source['detail']}"
            )
    else:
        emit({"ok": True, "sources": sources})
    return 0


def status_command(_args: argparse.Namespace) -> int:
    emit({"ok": True, "status": normalize_status()})
    return 0


def configure_command(args: argparse.Namespace) -> int:
    result = MixerApplication().execute(
        {"operation": "configure", "payload": read_payload(args)}
    )
    emit(result)
    return 0 if result.get("ok") else 1


def plan_command(args: argparse.Namespace) -> int:
    payload = read_payload(args)
    config = config_from_payload(payload)
    sources = payload.get("availableSources") if isinstance(payload.get("availableSources"), list) else discover_sources()
    source_ids = resolve_source_ids(sources, config.get("sourceIds", []))
    if not source_ids:
        resolution = resolve_source_matchers(sources, config.get("sourceMatchers", []))
        if resolution["missingMatchers"] or resolution["ambiguousMatchers"]:
            raise RoutingError("saved Source Matchers require review in the mixer")
        source_ids = resolution["selectedIds"]
    plan = build_route_plan(sources, source_ids, config.get("destination", "both"))
    emit({"ok": True, "plan": plan, "config": public_config(config)})
    return 0


def test_connection_command(args: argparse.Namespace) -> int:
    require_migration_complete()
    payload = read_payload(args)
    config = config_from_payload(payload)
    save_config(config)
    result = test_connection(config.get("remote", {}))
    emit({"ok": result["ok"], "connection": result, "config": public_config(config)})
    return 0 if result["ok"] else 1


def start_command(args: argparse.Namespace) -> int:
    payload = read_payload(args)
    config = config_from_payload(payload)
    status = start_session(config)
    emit({"ok": True, "status": status, "config": public_config(config)})
    return 0


def stop_command(_args: argparse.Namespace) -> int:
    status = stop_session()
    emit({"ok": True, "status": status})
    return 0


def run_command(args: argparse.Namespace) -> int:
    payload = read_payload(args)
    config = config_from_payload(payload)
    return run_foreground(config)


def application_payload_command(args: argparse.Namespace) -> int:
    result = MixerApplication().execute(
        {"operation": args.application_operation, "payload": read_payload(args)}
    )
    emit(result)
    return 0 if result.get("ok") else 1


def diagnostics_preview_command(args: argparse.Namespace) -> int:
    result = MixerApplication().execute(
        {
            "operation": "diagnostics.preview",
            "payload": {"includeLogs": bool(args.include_logs)},
        }
    )
    emit(result)
    return 0 if result.get("ok") else 1


def diagnostics_report_url_command(args: argparse.Namespace) -> int:
    result = MixerApplication().execute(
        {"operation": "diagnostics.report-url", "payload": read_payload(args)}
    )
    emit(result)
    return 0 if result.get("ok") else 1


def diagnostics_clear_command(_args: argparse.Namespace) -> int:
    result = MixerApplication().execute({"operation": "diagnostics.clear"})
    emit(result)
    return 0 if result.get("ok") else 1


def diagnostics_verbose_command(_args: argparse.Namespace) -> int:
    result = MixerApplication().execute({"operation": "diagnostics.verbose-next"})
    emit(result)
    return 0 if result.get("ok") else 1


def lifecycle_monitor_command(_args: argparse.Namespace) -> int:
    return run_lifecycle_monitor()


def worker_command(args: argparse.Namespace) -> int:
    with os.fdopen(args.config_fd, "r", encoding="utf-8") as config_file:
        payload = json.load(config_file)
    if not isinstance(payload, dict):
        raise ValueError("worker JSON payload must be an object")
    config = config_from_payload(payload, base=payload)
    return SessionWorker(config, args.session_id).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssh-mixer",
        description="Route selected local PipeWire/PulseAudio sources over SSH.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="Print sources, status, and saved config as JSON")
    snapshot.set_defaults(func=snapshot_command)

    list_sources = sub.add_parser("list-sources", help="Print discoverable audio inputs")
    list_sources.add_argument("--plain", action="store_true", help="Print a tab-separated list")
    list_sources.set_defaults(func=list_sources_command)

    status = sub.add_parser("status", help="Print active session status")
    status.set_defaults(func=status_command)

    configure = sub.add_parser("configure", help="Persist non-secret SSH-mixer configuration")
    add_json_args(configure)
    configure.set_defaults(func=configure_command)

    plan = sub.add_parser("plan", help="Build a routing plan without touching audio")
    add_json_args(plan)
    plan.set_defaults(func=plan_command)

    test = sub.add_parser("test-connection", help="Preflight the remote Receiver Protocol")
    add_json_args(test)
    test.set_defaults(func=test_connection_command)

    start = sub.add_parser("start", help="Start a persistent mixer session")
    add_json_args(start)
    start.set_defaults(func=start_command)

    stop = sub.add_parser("stop", help="Stop the active mixer session and clean routes")
    stop.set_defaults(func=stop_command)

    run = sub.add_parser("run", help="Run a mixer session in the foreground")
    add_json_args(run)
    run.set_defaults(func=run_command)

    migration_inspect = sub.add_parser(
        "migration-inspect", help="Detect legacy configuration without exposing its values"
    )
    migration_inspect.set_defaults(
        func=application_payload_command,
        application_operation="migration.inspect",
    )

    migration_connection = sub.add_parser(
        "migration-connection",
        help="Review legacy receiver details after choosing secure import",
    )
    migration_connection.set_defaults(
        func=application_payload_command,
        application_operation="migration.connection",
    )

    migration_plan = sub.add_parser(
        "migration-plan", help="Plan one explicit legacy migration choice"
    )
    add_json_args(migration_plan)
    migration_plan.set_defaults(
        func=application_payload_command,
        application_operation="migration.plan",
    )

    migration_apply = sub.add_parser(
        "migration-apply", help="Apply one exact approved transactional migration"
    )
    add_json_args(migration_apply)
    migration_apply.set_defaults(
        func=application_payload_command,
        application_operation="migration.apply",
    )

    removal_inspect = sub.add_parser(
        "removal-inspect", help="List pending verified Receiver cleanup"
    )
    removal_inspect.set_defaults(
        func=application_payload_command,
        application_operation="removal.inspect",
    )

    removal_plan = sub.add_parser(
        "removal-plan", help="Plan removal of the configured Connection"
    )
    removal_plan.set_defaults(
        func=application_payload_command,
        application_operation="removal.plan",
    )

    removal_apply = sub.add_parser(
        "removal-apply", help="Apply an exact approved Connection-removal plan"
    )
    add_json_args(removal_apply)
    removal_apply.set_defaults(
        func=application_payload_command,
        application_operation="removal.apply",
    )

    uninstall_plan = sub.add_parser(
        "uninstall-plan", help="List Receivers and plan verified plugin removal"
    )
    uninstall_plan.set_defaults(
        func=application_payload_command,
        application_operation="uninstall.plan",
    )

    uninstall_apply = sub.add_parser(
        "uninstall-apply", help="Clean Receivers and apply an exact plugin-removal plan"
    )
    add_json_args(uninstall_apply)
    uninstall_apply.set_defaults(
        func=application_payload_command,
        application_operation="uninstall.apply",
    )

    mix_profile_save = sub.add_parser(
        "mix-profile-save", help="Save a reusable Mix Profile with stable Source Matchers"
    )
    add_json_args(mix_profile_save)
    mix_profile_save.set_defaults(
        func=application_payload_command,
        application_operation="mix-profile.save",
    )

    mix_profile_load = sub.add_parser(
        "mix-profile-load", help="Open a saved Mix Profile in the mixer"
    )
    add_json_args(mix_profile_load)
    mix_profile_load.set_defaults(
        func=application_payload_command,
        application_operation="mix-profile.load",
    )

    mix_profile_delete = sub.add_parser(
        "mix-profile-delete", help="Delete a saved Mix Profile"
    )
    add_json_args(mix_profile_delete)
    mix_profile_delete.set_defaults(
        func=application_payload_command,
        application_operation="mix-profile.delete",
    )

    mix_profile_quick_start = sub.add_parser(
        "mix-profile-quick-start",
        help="Explicitly start a uniquely resolved playback-only Mix Profile",
    )
    add_json_args(mix_profile_quick_start)
    mix_profile_quick_start.set_defaults(
        func=application_payload_command,
        application_operation="mix-profile.quick-start",
    )

    indicator_status = sub.add_parser("indicator-status", help=argparse.SUPPRESS)
    indicator_status.set_defaults(
        func=application_payload_command,
        application_operation="indicator.inspect",
    )

    lifecycle_event = sub.add_parser(
        "lifecycle-event", help="Apply a lock, suspend, logout, or connection-loss event"
    )
    add_json_args(lifecycle_event)
    lifecycle_event.set_defaults(
        func=application_payload_command,
        application_operation="lifecycle.event",
    )

    lifecycle_monitor = sub.add_parser("lifecycle-monitor", help=argparse.SUPPRESS)
    lifecycle_monitor.set_defaults(func=lifecycle_monitor_command)

    updates_check = sub.add_parser(
        "updates-check", help="Check the plugin-pinned signed Receiver release"
    )
    add_json_args(updates_check)
    updates_check.set_defaults(
        func=application_payload_command,
        application_operation="updates.check",
    )

    updates_apply_pinned = sub.add_parser(
        "updates-apply-pinned", help="Apply an exact approved plugin-pinned Receiver update"
    )
    add_json_args(updates_apply_pinned)
    updates_apply_pinned.set_defaults(
        func=application_payload_command,
        application_operation="updates.apply-pinned",
    )

    updates_plan = sub.add_parser(
        "updates-plan", help="Verify signed release metadata and build an update plan"
    )
    add_json_args(updates_plan)
    updates_plan.set_defaults(
        func=application_payload_command,
        application_operation="updates.plan",
    )

    updates_apply = sub.add_parser(
        "updates-apply", help="Apply one exact approved and verified update plan"
    )
    add_json_args(updates_apply)
    updates_apply.set_defaults(
        func=application_payload_command,
        application_operation="updates.apply",
    )

    receiver_quiet_test = sub.add_parser(
        "receiver-quiet-test", help="Play one bounded quiet Receiver test tone"
    )
    add_json_args(receiver_quiet_test)
    receiver_quiet_test.set_defaults(
        func=application_payload_command,
        application_operation="receiver.quiet-test",
    )

    receiver_macos_plan = sub.add_parser(
        "receiver-macos-plan", help="Probe and plan Experimental macOS Receiver setup"
    )
    add_json_args(receiver_macos_plan)
    receiver_macos_plan.set_defaults(
        func=application_payload_command,
        application_operation="receiver.macos-plan",
    )

    receiver_macos_setup = sub.add_parser(
        "receiver-macos-setup", help="Apply approved Experimental macOS Receiver setup"
    )
    add_json_args(receiver_macos_setup)
    receiver_macos_setup.set_defaults(
        func=application_payload_command,
        application_operation="receiver.macos-setup",
    )

    receiver_windows_plan = sub.add_parser(
        "receiver-windows-plan", help="Probe and plan Windows Receiver setup without changes"
    )
    add_json_args(receiver_windows_plan)
    receiver_windows_plan.set_defaults(
        func=application_payload_command,
        application_operation="receiver.windows-plan",
    )

    receiver_windows_setup = sub.add_parser(
        "receiver-windows-setup", help="Apply an approved Windows Receiver setup plan"
    )
    add_json_args(receiver_windows_setup)
    receiver_windows_setup.set_defaults(
        func=application_payload_command,
        application_operation="receiver.windows-setup",
    )

    receiver_linux_plan = sub.add_parser(
        "receiver-linux-plan", help="Probe and plan Linux Receiver setup without changes"
    )
    add_json_args(receiver_linux_plan)
    receiver_linux_plan.set_defaults(
        func=application_payload_command,
        application_operation="receiver.linux-plan",
    )

    receiver_linux_setup = sub.add_parser(
        "receiver-linux-setup", help="Apply an approved Linux Receiver setup plan"
    )
    add_json_args(receiver_linux_setup)
    receiver_linux_setup.set_defaults(
        func=application_payload_command,
        application_operation="receiver.linux-setup",
    )

    profile_inspect = sub.add_parser(
        "profile-inspect", help="Inspect an existing OpenSSH profile"
    )
    add_json_args(profile_inspect)
    profile_inspect.set_defaults(
        func=application_payload_command,
        application_operation="profile.inspect",
    )

    profile_save = sub.add_parser(
        "profile-save", help="Save a confirmed user-managed OpenSSH profile"
    )
    add_json_args(profile_save)
    profile_save.set_defaults(
        func=application_payload_command,
        application_operation="profile.save",
    )

    connection_save = sub.add_parser(
        "connection-save", help="Save a validated receiver Connection"
    )
    add_json_args(connection_save)
    connection_save.set_defaults(
        func=application_payload_command,
        application_operation="connection.save",
    )

    trust_inspect = sub.add_parser(
        "trust-inspect", help="Retrieve and inspect receiver host-key fingerprints"
    )
    add_json_args(trust_inspect)
    trust_inspect.set_defaults(
        func=application_payload_command,
        application_operation="trust.inspect",
    )

    trust_approve = sub.add_parser(
        "trust-approve", help="Approve unchanged receiver host-key fingerprints"
    )
    add_json_args(trust_approve)
    trust_approve.set_defaults(
        func=application_payload_command,
        application_operation="trust.approve",
    )

    diagnostics_preview = sub.add_parser(
        "diagnostics-preview", help="Preview a locally redacted diagnostic report"
    )
    diagnostics_preview.add_argument("--include-logs", action="store_true")
    diagnostics_preview.set_defaults(func=diagnostics_preview_command)

    diagnostics_report = sub.add_parser(
        "diagnostics-report-url", help="Create a GitHub URL from a reviewed report"
    )
    add_json_args(diagnostics_report)
    diagnostics_report.set_defaults(func=diagnostics_report_url_command)

    diagnostics_retention = sub.add_parser(
        "diagnostics-retention", help="Choose bounded diagnostic retention"
    )
    add_json_args(diagnostics_retention)
    diagnostics_retention.set_defaults(
        func=application_payload_command,
        application_operation="diagnostics.configure",
    )

    diagnostics_contribute = sub.add_parser(
        "diagnostics-contribute-url", help="Open reviewed contribution guidance"
    )
    diagnostics_contribute.set_defaults(
        func=application_payload_command,
        application_operation="diagnostics.contribute-url",
    )

    diagnostics_clear = sub.add_parser(
        "diagnostics-clear", help="Delete retained diagnostics"
    )
    diagnostics_clear.set_defaults(func=diagnostics_clear_command)

    diagnostics_verbose = sub.add_parser(
        "diagnostics-verbose-next", help="Enable verbose diagnostics for one Session"
    )
    diagnostics_verbose.set_defaults(func=diagnostics_verbose_command)

    worker = sub.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--session-id", required=True)
    worker.add_argument("--config-fd", required=True, type=int)
    worker.set_defaults(func=worker_command)

    return parser


def add_json_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--json", default="", help="JSON object payload")
    group.add_argument("--stdin", action="store_true", help="Read JSON object payload from stdin")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (json.JSONDecodeError, OSError, ValueError, RoutingError, SessionError) as exc:
        sensitive: list[str] = []
        try:
            remote = load_config().get("remote", {})
            config = load_config()
            remote = config.get("remote", {})
            if isinstance(remote, dict):
                sensitive = [
                    str(remote.get("host", "")),
                    str(remote.get("user", "")),
                    str(remote.get("keyPath", "")),
                ]
            source_ids = config.get("sourceIds", [])
            if isinstance(source_ids, list):
                sensitive.extend(str(source_id) for source_id in source_ids)
        except (OSError, ValueError):
            pass
        diagnostic = DiagnosticStore(logs_dir()).record(
            stage=f"cli.{getattr(args, 'command', 'unknown')}",
            code=type(exc).__name__,
            message=str(exc),
            sensitive_values=sensitive,
        )
        emit(
            {
                "ok": False,
                "error": diagnostic["message"],
                "diagnostic": diagnostic,
                "status": normalize_status(),
            }
        )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
