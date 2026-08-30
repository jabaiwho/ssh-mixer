#!/usr/bin/env bash
set -Eeuo pipefail

PROTOCOL_VERSION="v1"
COMPANION_VERSION="1.1.2"
MODE="${1:-}"

if [[ "$(id -u)" -eq 0 || "$(id -un)" == "root" ]]; then
  printf '%s\n' '{"schemaVersion":1,"ok":false,"code":"root-rejected"}' >&2
  exit 77
fi

probe() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    printf '%s\n' '{"schemaVersion":1,"ok":false,"code":"unsupported-platform"}' >&2
    exit 69
  fi
  printf 'platform=linux\nuser=%s\nhome=%s\n' "$(id -un)" "$HOME"
  local command_name
  for command_name in python3 ffplay apt-get dnf pacman zypper; do
    if command -v "$command_name" >/dev/null 2>&1; then
      printf 'command.%s=true\n' "$command_name"
    else
      printf 'command.%s=false\n' "$command_name"
    fi
  done
}

if [[ "$MODE" == "probe" ]]; then
  probe
  exit 0
fi

if [[ "$MODE" == "remove" ]]; then
  [[ "$#" -eq 2 && "$2" =~ ^[A-Za-z0-9+/]+={0,3}$ ]] || {
    printf '%s\n' '{"schemaVersion":1,"ok":false,"code":"invalid-key"}' >&2
    exit 64
  }
  key_body="$2"
  authorized_keys="$HOME/.ssh/authorized_keys"
  receiver_path="$HOME/.local/lib/ssh-mixer/ssh-mixer-receiver-v1.py"
  state_path="${XDG_STATE_HOME:-$HOME/.local/state}/ssh-mixer/quiet-test-v1.json"
  if [[ -e "$authorized_keys" ]]; then
    [[ -f "$authorized_keys" && ! -L "$authorized_keys" && ! -L "$(dirname "$authorized_keys")" ]]
    work="$(mktemp "${TMPDIR:-/tmp}/ssh-mixer-remove.XXXXXXXX")"
    awk -v suffix=" ssh-ed25519 $key_body ssh-mixer-managed-v1" \
      'index($0, suffix) != (length($0) - length(suffix) + 1)' \
      "$authorized_keys" > "$work"
    install -m 600 -- "$work" "$authorized_keys"
    rm -f -- "$work"
    if grep -Fq -- " ssh-ed25519 $key_body ssh-mixer-managed-v1" "$authorized_keys"; then
      printf '%s\n' '{"schemaVersion":1,"ok":false,"code":"revocation-unverified"}' >&2
      exit 1
    fi
  fi
  helper_removed=false
  if ! grep -q -- ' ssh-mixer-managed-v1$' "$authorized_keys" 2>/dev/null; then
    rm -f -- "$receiver_path" "$state_path"
    rmdir --ignore-fail-on-non-empty -- "$(dirname "$state_path")" 2>/dev/null || true
    rmdir --ignore-fail-on-non-empty -- "$(dirname "$receiver_path")" 2>/dev/null || true
    [[ ! -e "$receiver_path" && ! -e "$state_path" ]]
    helper_removed=true
  fi
  printf '{"schemaVersion":1,"ok":true,"keyRevoked":true,"helperRemoved":%s}\n' "$helper_removed"
  exit 0
fi

if [[ "$MODE" != "apply" || "$#" -ne 5 ]]; then
  printf 'Usage: %s probe | apply RECEIVER_SOURCE PUBLIC_KEY_FILE SHA256 PACKAGE_MANAGER\n' "$0" >&2
  exit 64
fi

RECEIVER_SOURCE="$2"
PUBLIC_KEY_FILE="$3"
EXPECTED_SHA256="$4"
PACKAGE_MANAGER="$5"
RECEIVER_DIR="$HOME/.local/lib/ssh-mixer"
RECEIVER_PATH="$RECEIVER_DIR/ssh-mixer-receiver-v1.py"
SSH_DIR="$HOME/.ssh"
AUTHORIZED_KEYS="$SSH_DIR/authorized_keys"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ssh-mixer-setup.XXXXXX")"
BACKUP_RECEIVER="$WORK_DIR/receiver.backup"
BACKUP_KEYS="$WORK_DIR/authorized_keys.backup"
HAD_RECEIVER=false
HAD_KEYS=false
CREATED_RECEIVER_DIR=false
CREATED_SSH_DIR=false
CHANGES_STARTED=false
PACKAGE_CHANGED=false
PACKAGE_ROLLBACK_COMPLETE=true
INSTALLED_PACKAGE=false
REMOVABLE_PACKAGES=()
COMMITTED=false

cleanup() {
  rm -rf -- "$WORK_DIR"
}

remove_package() {
  [[ "${#REMOVABLE_PACKAGES[@]}" -gt 0 ]] || return 0
  case "$PACKAGE_MANAGER" in
    apt-get) sudo apt-get remove -- "${REMOVABLE_PACKAGES[@]}" ;;
    dnf) sudo dnf remove -y -- "${REMOVABLE_PACKAGES[@]}" ;;
    pacman) sudo pacman -R -- "${REMOVABLE_PACKAGES[@]}" ;;
    zypper) sudo zypper remove -- "${REMOVABLE_PACKAGES[@]}" ;;
    *) return 1 ;;
  esac
}

rollback() {
  local failure_status=$?
  trap - ERR
  set +e
  local rollback_status=0
  if [[ "$CHANGES_STARTED" == true ]]; then
    if [[ "$HAD_RECEIVER" == true ]]; then
      cp -p -- "$BACKUP_RECEIVER" "$RECEIVER_PATH" || rollback_status=1
    else
      rm -f -- "$RECEIVER_PATH" || rollback_status=1
    fi
    if [[ "$HAD_KEYS" == true ]]; then
      cp -p -- "$BACKUP_KEYS" "$AUTHORIZED_KEYS" || rollback_status=1
    else
      rm -f -- "$AUTHORIZED_KEYS" || rollback_status=1
    fi
    if [[ "$INSTALLED_PACKAGE" == true ]]; then
      remove_package || rollback_status=1
    fi
    if [[ "$PACKAGE_CHANGED" == true && "$PACKAGE_ROLLBACK_COMPLETE" != true ]]; then
      rollback_status=1
    fi
    if [[ "$CREATED_RECEIVER_DIR" == true ]]; then
      rmdir --ignore-fail-on-non-empty -- "$RECEIVER_DIR" || rollback_status=1
    fi
    if [[ "$CREATED_SSH_DIR" == true ]]; then
      rmdir --ignore-fail-on-non-empty -- "$SSH_DIR" || rollback_status=1
    fi
  fi
  if [[ "$rollback_status" -ne 0 ]]; then
    printf '%s\n' '{"schemaVersion":1,"ok":false,"code":"ROLLBACK_INCOMPLETE"}' >&2
  else
    printf '%s\n' '{"schemaVersion":1,"ok":false,"code":"rolled-back"}' >&2
  fi
  cleanup
  exit "$failure_status"
}
trap rollback ERR
trap 'false' HUP INT TERM
trap 'if [[ "$COMMITTED" == true ]]; then cleanup; fi' EXIT

[[ -f "$RECEIVER_SOURCE" && ! -L "$RECEIVER_SOURCE" ]]
[[ -f "$PUBLIC_KEY_FILE" && ! -L "$PUBLIC_KEY_FILE" ]]
[[ "$EXPECTED_SHA256" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$EXPECTED_SHA256" "$RECEIVER_SOURCE" | sha256sum --check --status -
PUBLIC_KEY="$(tr -d '\r\n' < "$PUBLIC_KEY_FILE")"
[[ "$PUBLIC_KEY" =~ ^ssh-ed25519\ [A-Za-z0-9+/]+={0,3}(\ .*)?$ ]]

if [[ -e "$RECEIVER_PATH" ]]; then
  [[ -f "$RECEIVER_PATH" && ! -L "$RECEIVER_PATH" ]]
  cp -p -- "$RECEIVER_PATH" "$BACKUP_RECEIVER"
  HAD_RECEIVER=true
fi
if [[ -e "$AUTHORIZED_KEYS" ]]; then
  [[ -f "$AUTHORIZED_KEYS" && ! -L "$AUTHORIZED_KEYS" ]]
  cp -p -- "$AUTHORIZED_KEYS" "$BACKUP_KEYS"
  HAD_KEYS=true
fi
if [[ -e "$RECEIVER_DIR" ]]; then
  [[ -d "$RECEIVER_DIR" && ! -L "$RECEIVER_DIR" ]]
else
  install -d -m 700 -- "$RECEIVER_DIR"
  CREATED_RECEIVER_DIR=true
fi
if [[ -e "$SSH_DIR" ]]; then
  [[ -d "$SSH_DIR" && ! -L "$SSH_DIR" ]]
else
  install -d -m 700 -- "$SSH_DIR"
  CREATED_SSH_DIR=true
fi
CHANGES_STARTED=true

MISSING_PACKAGES=()
command -v python3 >/dev/null 2>&1 || MISSING_PACKAGES+=(python3)
command -v ffplay >/dev/null 2>&1 || MISSING_PACKAGES+=(ffmpeg)
if [[ "${#MISSING_PACKAGES[@]}" -gt 0 ]]; then
  PACKAGE_CHANGED=true
  local_package=""
  for local_package in "${MISSING_PACKAGES[@]}"; do
    case "$PACKAGE_MANAGER" in
      apt-get)
        dpkg-query -W -f='${Status}' "$local_package" 2>/dev/null | grep -Fq 'install ok installed' || REMOVABLE_PACKAGES+=("$local_package")
        ;;
      dnf | zypper)
        rpm -q "$local_package" >/dev/null 2>&1 || REMOVABLE_PACKAGES+=("$local_package")
        ;;
      pacman)
        pacman -Q "$local_package" >/dev/null 2>&1 || REMOVABLE_PACKAGES+=("$local_package")
        ;;
      *) printf '%s\n' '{"schemaVersion":1,"ok":false,"code":"package-manager-mismatch"}' >&2; false ;;
    esac
  done
  if [[ "${#REMOVABLE_PACKAGES[@]}" -ne "${#MISSING_PACKAGES[@]}" ]]; then
    PACKAGE_ROLLBACK_COMPLETE=false
  fi
  INSTALLED_PACKAGE=true
  case "$PACKAGE_MANAGER" in
    apt-get) sudo apt-get install -- "${MISSING_PACKAGES[@]}" ;;
    dnf) sudo dnf install -y -- "${MISSING_PACKAGES[@]}" ;;
    pacman) sudo pacman -S --needed -- "${MISSING_PACKAGES[@]}" ;;
    zypper) sudo zypper install -- "${MISSING_PACKAGES[@]}" ;;
  esac
fi
command -v python3 >/dev/null 2>&1
command -v ffplay >/dev/null 2>&1

install -m 700 -- "$RECEIVER_SOURCE" "$RECEIVER_PATH"
printf '%s  %s\n' "$EXPECTED_SHA256" "$RECEIVER_PATH" | sha256sum --check --status -

KEY_BODY="$(awk '{print $2}' <<< "$PUBLIC_KEY")"
TEMP_KEYS="$WORK_DIR/authorized_keys.new"
if [[ "$HAD_KEYS" == true ]]; then
  awk -v suffix=" ssh-ed25519 $KEY_BODY ssh-mixer-managed-v1" \
    'index($0, suffix) != (length($0) - length(suffix) + 1)' \
    "$AUTHORIZED_KEYS" > "$TEMP_KEYS"
else
  : > "$TEMP_KEYS"
fi
printf 'command="%s --forced --key %s",restrict,no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 %s ssh-mixer-managed-v1\n' "$RECEIVER_PATH" "$KEY_BODY" "$KEY_BODY" >> "$TEMP_KEYS"
install -m 600 -- "$TEMP_KEYS" "$AUTHORIZED_KEYS"

EXPECTED_ENTRY="command=\"$RECEIVER_PATH --forced --key $KEY_BODY\",restrict,no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 $KEY_BODY ssh-mixer-managed-v1"
grep -Fqx -- "$EXPECTED_ENTRY" "$AUTHORIZED_KEYS"

COMMITTED=true
printf '%s\n' "{\"schemaVersion\":1,\"ok\":true,\"protocol\":\"$PROTOCOL_VERSION\",\"companionVersion\":\"$COMPANION_VERSION\",\"packageInstalled\":$INSTALLED_PACKAGE}"
