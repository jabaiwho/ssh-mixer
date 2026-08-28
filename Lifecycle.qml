import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  property string backend: Quickshell.env("SSH_MIXER_BIN") || (Quickshell.env("HOME") + "/.config/omarchy/plugins/jabaiwho.ssh-mixer/bin/ssh-mixer")

  function ensureMonitor() {
    if (!monitor.running) monitor.running = true
  }

  Component.onCompleted: ensureMonitor()

  Process {
    id: monitor
    command: [
      "systemd-inhibit",
      "--what=sleep:shutdown",
      "--who=SSH-mixer",
      "--why=Stop and clean active audio routing before suspend or logout",
      "--mode=delay",
      root.backend,
      "lifecycle-monitor"
    ]
    onExited: restartTimer.restart()
  }

  Timer {
    id: restartTimer
    interval: 2000
    repeat: false
    onTriggered: root.ensureMonitor()
  }
}
