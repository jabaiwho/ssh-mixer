#!/bin/sh
set -eu

COMPANION_VERSION=1.1.1
MODE=${1:-}

json_error() {
  code=$1
  message=$2
  printf '{"schemaVersion":1,"ok":false,"experimental":true,"stage":"receiver.macos-setup","code":"%s","message":"%s"}\n' "$code" "$message" >&2
}

probe() {
  [ "$(uname -s)" = Darwin ] || { json_error unsupported-platform "Receiver is not macOS"; exit 69; }
  architecture=$(uname -m)
  case "$architecture" in
    arm64) brew_path=/opt/homebrew/bin/brew; ffplay_path=/opt/homebrew/bin/ffplay ;;
    x86_64) brew_path=/usr/local/bin/brew; ffplay_path=/usr/local/bin/ffplay ;;
    *) brew_path=; ffplay_path= ;;
  esac
  remote_login=false
  if /usr/sbin/systemsetup -getremotelogin 2>/dev/null | grep -q 'On$'; then
    remote_login=true
  elif /bin/launchctl print system/com.openssh.sshd >/dev/null 2>&1; then
    remote_login=true
  fi
  administrator=false
  if /usr/sbin/dseditgroup -o checkmember -m "$(id -un)" admin 2>/dev/null | grep -q 'yes'; then
    administrator=true
  fi
  elevated=false
  [ "$(id -u)" -eq 0 ] && elevated=true
  homebrew_path=
  ffplay_available=false
  [ -n "$brew_path" ] && [ -x "$brew_path" ] && homebrew_path=$brew_path
  [ -n "$ffplay_path" ] && [ -x "$ffplay_path" ] && ffplay_available=true
  openssh_version=$(/usr/bin/ssh -V 2>&1 | sed -n 's/^OpenSSH_\([0-9][0-9.]*\).*/\1/p')
  printf '{"schemaVersion":1,"platform":"macos","experimental":true,"realDeviceVerified":false,"architecture":"%s","version":"%s","user":"%s","home":"%s","openSshVersion":"%s","remoteLogin":%s,"administratorCapable":%s,"homebrewPath":"%s","ffplay":%s,"elevated":%s}\n' \
    "$architecture" "$(sw_vers -productVersion)" "$(id -un)" "$HOME" "$openssh_version" "$remote_login" "$administrator" "$homebrew_path" "$ffplay_available" "$elevated"
}

if [ "$MODE" = probe ]; then
  probe
  exit 0
fi

if [ "$MODE" = remove ]; then
  [ "$#" -eq 2 ] || { json_error invalid-request "remove requires the Managed Identity key body"; exit 64; }
  [ "$(uname -s)" = Darwin ] || exit 69
  key_body=$2
  printf '%s\n' "$key_body" | grep -Eq '^[A-Za-z0-9+/]+={0,3}$' || { json_error invalid-key "Managed Identity key body is invalid"; exit 64; }
  receiver_path="$HOME/.local/lib/ssh-mixer/ssh-mixer-receiver-v1"
  authorized_keys="$HOME/.ssh/authorized_keys"
  work=$(mktemp "${TMPDIR:-/tmp}/ssh-mixer-remove.XXXXXX")
  if [ -e "$authorized_keys" ] || [ -L "$authorized_keys" ]; then
    if [ ! -f "$authorized_keys" ] || [ -L "$authorized_keys" ] || [ -L "$(dirname "$authorized_keys")" ]; then
      json_error unsafe-path "authorized_keys path is unsafe"
      exit 1
    fi
    awk -v suffix=" ssh-ed25519 $key_body ssh-mixer-managed-macos-v1" 'index($0, suffix) != (length($0) - length(suffix) + 1)' "$authorized_keys" > "$work"
    install -m 600 "$work" "$authorized_keys"
    if grep -Fq " ssh-ed25519 $key_body ssh-mixer-managed-macos-v1" "$authorized_keys"; then
      json_error revocation-unverified "Managed Identity key remains"
      exit 1
    fi
  fi
  rm -f "$work"
  helper_removed=false
  if ! grep -q ' ssh-mixer-managed-macos-v1$' "$authorized_keys" 2>/dev/null; then
    state_root=${XDG_STATE_HOME:-"$HOME/Library/Application Support"}
    state_path="$state_root/ssh-mixer/quiet-test-v1"
    rm -f "$receiver_path" "$state_path"
    rmdir "$state_root/ssh-mixer" 2>/dev/null || true
    rmdir "$HOME/.local/lib/ssh-mixer" 2>/dev/null || true
    if [ -e "$receiver_path" ] || [ -e "$state_path" ]; then
      json_error cleanup-unverified "unshared Receiver state remains"
      exit 1
    fi
    helper_removed=true
  fi
  printf '{"schemaVersion":1,"ok":true,"experimental":true,"keyRevoked":true,"helperRemoved":%s}\n' "$helper_removed"
  exit 0
fi

if [ "$MODE" != apply ] || [ "$#" -ne 7 ]; then
  json_error invalid-request "Usage: setup-v1.sh apply RECEIVER_SOURCE PUBLIC_KEY SHA256 ARCHITECTURE BREW_PATH ENABLE_REMOTE_LOGIN"
  exit 64
fi

RECEIVER_SOURCE=$2
PUBLIC_KEY_FILE=$3
EXPECTED_SHA256=$4
EXPECTED_ARCHITECTURE=$5
BREW_PATH=$6
ENABLE_REMOTE_LOGIN=$7
RECEIVER_DIR="$HOME/.local/lib/ssh-mixer"
RECEIVER_PATH="$RECEIVER_DIR/ssh-mixer-receiver-v1"
SSH_DIR="$HOME/.ssh"
AUTHORIZED_KEYS="$SSH_DIR/authorized_keys"
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ssh-mixer-macos-setup.XXXXXX")
BACKUP_RECEIVER="$WORK_DIR/receiver.backup"
BACKUP_KEYS="$WORK_DIR/authorized_keys.backup"
HAD_RECEIVER=false
HAD_KEYS=false
CREATED_RECEIVER_DIR=false
CREATED_LIB_DIR=false
CREATED_LOCAL_DIR=false
CREATED_SSH_DIR=false
CHANGES_STARTED=false
REMOTE_LOGIN_ENABLED=false
PACKAGE_CHANGED=false
PACKAGE_REMOVABLE=false

cleanup() {
  rm -rf "$WORK_DIR"
}

rollback() {
  status=$?
  trap - 0 1 2 3 15
  rollback_status=0
  if [ "$CHANGES_STARTED" = true ]; then
    if [ "$HAD_RECEIVER" = true ]; then cp -p "$BACKUP_RECEIVER" "$RECEIVER_PATH" || rollback_status=1
    else rm -f "$RECEIVER_PATH" || rollback_status=1; fi
    if [ "$HAD_KEYS" = true ]; then cp -p "$BACKUP_KEYS" "$AUTHORIZED_KEYS" || rollback_status=1
    else rm -f "$AUTHORIZED_KEYS" || rollback_status=1; fi
    if [ "$PACKAGE_CHANGED" = true ]; then
      if [ "$PACKAGE_REMOVABLE" = true ]; then brew uninstall ffmpeg || rollback_status=1
      else rollback_status=1; fi
    fi
    if [ "$REMOTE_LOGIN_ENABLED" = true ]; then
      sudo /usr/sbin/systemsetup -setremotelogin off || rollback_status=1
    fi
    if [ "$CREATED_RECEIVER_DIR" = true ]; then rmdir "$RECEIVER_DIR" 2>/dev/null || rollback_status=1; fi
    if [ "$CREATED_LIB_DIR" = true ]; then rmdir "$HOME/.local/lib" 2>/dev/null || rollback_status=1; fi
    if [ "$CREATED_LOCAL_DIR" = true ]; then rmdir "$HOME/.local" 2>/dev/null || rollback_status=1; fi
    if [ "$CREATED_SSH_DIR" = true ]; then rmdir "$SSH_DIR" 2>/dev/null || rollback_status=1; fi
  fi
  if [ "$rollback_status" -eq 0 ]; then
    json_error rolled-back "Companion Setup failed and changes were rolled back"
  else
    json_error ROLLBACK_INCOMPLETE "Companion Setup failed and rollback is incomplete"
  fi
  cleanup
  exit "$status"
}
trap rollback 0
trap 'exit 1' 1 2 3 15

[ "$(uname -s)" = Darwin ] || { json_error unsupported-platform "Receiver is not macOS"; false; }
[ "$(id -u)" -ne 0 ] || { json_error elevated-runtime "Direct root setup is refused"; false; }
architecture=$(uname -m)
[ "$architecture" = "$EXPECTED_ARCHITECTURE" ] || { json_error architecture-changed "Architecture changed after approval"; false; }
case "$architecture" in
  arm64) expected_brew=/opt/homebrew/bin/brew ;;
  x86_64) expected_brew=/usr/local/bin/brew ;;
  *) json_error unsupported-architecture "macOS architecture is unsupported"; false ;;
esac
[ "$BREW_PATH" = "$expected_brew" ] || { json_error homebrew-path "Homebrew path is not valid for this architecture"; false; }
if [ ! -f "$RECEIVER_SOURCE" ] || [ -L "$RECEIVER_SOURCE" ]; then false; fi
if [ ! -f "$PUBLIC_KEY_FILE" ] || [ -L "$PUBLIC_KEY_FILE" ]; then false; fi
[ "$(/usr/bin/shasum -a 256 "$RECEIVER_SOURCE" | awk '{print $1}')" = "$EXPECTED_SHA256" ]
PUBLIC_KEY=$(tr -d '\r\n' < "$PUBLIC_KEY_FILE")
printf '%s\n' "$PUBLIC_KEY" | grep -Eq '^ssh-ed25519 [A-Za-z0-9+/]+={0,3}( .*)?$'

if [ -e "$RECEIVER_PATH" ]; then [ ! -L "$RECEIVER_PATH" ] || false; HAD_RECEIVER=true; cp -p "$RECEIVER_PATH" "$BACKUP_RECEIVER"; fi
if [ -e "$AUTHORIZED_KEYS" ]; then [ ! -L "$AUTHORIZED_KEYS" ] || false; HAD_KEYS=true; cp -p "$AUTHORIZED_KEYS" "$BACKUP_KEYS"; fi
CHANGES_STARTED=true
[ -d "$HOME/.local" ] || CREATED_LOCAL_DIR=true
[ -d "$HOME/.local/lib" ] || CREATED_LIB_DIR=true
if [ ! -d "$RECEIVER_DIR" ]; then mkdir -p "$RECEIVER_DIR"; chmod 700 "$RECEIVER_DIR"; CREATED_RECEIVER_DIR=true; fi
if [ ! -d "$SSH_DIR" ]; then mkdir -m 700 "$SSH_DIR"; CREATED_SSH_DIR=true; fi
if [ -L "$HOME/.local" ] || [ -L "$HOME/.local/lib" ] || [ -L "$RECEIVER_DIR" ] || [ -L "$SSH_DIR" ]; then false; fi

if [ "$ENABLE_REMOTE_LOGIN" = true ]; then
  REMOTE_LOGIN_ENABLED=true
  sudo /usr/sbin/systemsetup -setremotelogin on
fi

BREW_DIR=$(dirname "$BREW_PATH")
PATH="$BREW_DIR:/usr/bin:/bin:/usr/sbin:/sbin"
HOMEBREW_NO_AUTO_UPDATE=1
export PATH HOMEBREW_NO_AUTO_UPDATE
[ "$(command -v brew)" = "$BREW_PATH" ] || { json_error homebrew-missing "Approved Homebrew executable is unavailable"; false; }
if [ ! -x "${BREW_DIR}/ffplay" ]; then
  PACKAGE_CHANGED=true
  if brew list --versions ffmpeg >/dev/null 2>&1; then PACKAGE_REMOVABLE=false
  else PACKAGE_REMOVABLE=true; fi
  brew install ffmpeg
fi
[ -x "${BREW_DIR}/ffplay" ] || { json_error ffplay-missing "Homebrew FFplay verification failed"; false; }
brew list --versions ffmpeg >/dev/null
brew info --json=v2 ffmpeg >/dev/null

install -m 700 "$RECEIVER_SOURCE" "$RECEIVER_PATH"
[ "$(/usr/bin/shasum -a 256 "$RECEIVER_PATH" | awk '{print $1}')" = "$EXPECTED_SHA256" ]
KEY_BODY=$(printf '%s\n' "$PUBLIC_KEY" | awk '{print $2}')
TEMP_KEYS="$WORK_DIR/authorized_keys.new"
if [ "$HAD_KEYS" = true ]; then
  awk -v suffix=" ssh-ed25519 $KEY_BODY ssh-mixer-managed-macos-v1" \
    'index($0, suffix) != (length($0) - length(suffix) + 1)' \
    "$AUTHORIZED_KEYS" > "$TEMP_KEYS"
else
  : > "$TEMP_KEYS"
fi
printf 'command="%s --forced --key %s",restrict,no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 %s ssh-mixer-managed-macos-v1\n' "$RECEIVER_PATH" "$KEY_BODY" "$KEY_BODY" >> "$TEMP_KEYS"
install -m 600 "$TEMP_KEYS" "$AUTHORIZED_KEYS"
EXPECTED_ENTRY="command=\"$RECEIVER_PATH --forced --key $KEY_BODY\",restrict,no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 $KEY_BODY ssh-mixer-managed-macos-v1"
grep -Fqx "$EXPECTED_ENTRY" "$AUTHORIZED_KEYS"

trap - 0 1 2 3 15
cleanup
printf '{"schemaVersion":1,"ok":true,"platform":"macos","experimental":true,"realDeviceVerified":false,"protocol":"v1","companionVersion":"%s"}\n' "$COMPANION_VERSION"
