import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

WidgetButton {
  id: root

  property bool sessionActive: false
  property bool captureActive: false
  property string receiverLabel: ""
  property string backend: Quickshell.env("SSH_MIXER_BIN") || (Quickshell.env("HOME") + "/.config/omarchy/plugins/jabaiwho.ssh-mixer/bin/ssh-mixer")

  text: sessionActive
    ? ((captureActive ? "󰍬" : "󰕾") + (receiverLabel ? ("  " + receiverLabel) : ""))
    : ""
  active: captureActive
  activeColor: bar ? bar.urgent : Color.urgent
  foreground: bar ? bar.barForeground : Color.foreground
  tooltipText: captureActive
    ? "SSH-mixer recording/capture Session · Open controls"
    : "SSH-mixer playback Session · Open controls"
  interactive: sessionActive
  pressable: sessionActive

  function refresh() {
    if (!statusProcess.running) statusProcess.running = true
  }

  function applyStatus(raw) {
    var data = {}
    try { data = JSON.parse(String(raw || "{}")) || {} } catch (e) { return }
    if (!data.ok) return
    sessionActive = data.active === true
    captureActive = data.capture === true
    receiverLabel = String(data.receiverLabel || "")
  }

  onPressed: function() {
    if (!sessionActive || summonProcess.running) return
    summonProcess.running = true
  }

  Component.onCompleted: refresh()

  Timer {
    interval: 1000
    repeat: true
    running: true
    onTriggered: root.refresh()
  }

  Process {
    id: statusProcess
    command: [root.backend, "indicator-status"]
    stdout: StdioCollector {
      id: statusOutput
      waitForEnd: true
      onStreamFinished: root.applyStatus(text)
    }
  }

  Process {
    id: summonProcess
    command: [
      "omarchy-shell",
      "shell",
      "summon",
      "jabaiwho.ssh-mixer",
      "{}"
    ]
  }
}
