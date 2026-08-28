import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

Item {
  id: root

  property var shell: null
  property var manifest: null
  property bool opened: false

  property string backend: Quickshell.env("SSH_MIXER_BIN") || (Quickshell.env("HOME") + "/.config/omarchy/plugins/jabaiwho.ssh-mixer/bin/ssh-mixer")
  property var sources: []
  property var selectedIds: []
  property var recentCaptureIds: []
  property var mixProfiles: []
  property string mixProfileName: ""
  property var migration: ({ detected: false, reasons: [], choices: [] })
  property var migrationPlan: null
  property var migrationLegacyConnection: null
  property string migrationPendingPlatform: ""
  property bool migrationTrustOnly: false
  property var privacy: ({ lockBehavior: "stop-all", showReceiverLabel: false })
  property string destination: "both"
  property var status: ({ state: "stopped", active: false, error: "" })
  property var remote: ({ host: "", user: "" })
  property var connection: null
  property var componentVersions: ({})
  property var updatePlan: null
  property var tailscalePeers: []
  property var openSshProfiles: []
  property string setupProfile: ""
  property var pendingProfile: null
  property var linuxSetupPlan: null
  property var windowsSetupPlan: null
  property var macosSetupPlan: null
  property bool macosExperimentalConfirmed: false
  property bool windowsAdministratorConfirmed: false
  property bool encryptedManagedIdentity: false
  property int quietTestDbfs: -40
  property bool quietTestPlayed: false
  property bool quietAwaitingConfirmation: false
  property bool quietAudibleConfirmed: false
  property string setupType: "tailscale"
  property string setupPeerId: ""
  property string setupHost: ""
  property string setupUser: ""
  property string setupPort: "22"
  property var pendingTrust: null
  property bool configurationDirty: false
  property var pendingSession: null
  property string message: ""
  property bool busy: false
  property string action: ""
  property bool quietAction: false
  property string procOut: ""
  property string procErr: ""
  property bool expectedStop: false
  property string diagnosticBody: ""
  property bool diagnosticAvailable: false
  property bool includeDiagnosticLogs: false
  property string diagnosticRetention: "standard"
  property var removal: ({ pendingCount: 0, pending: [] })
  property var removalPlan: null
  property bool removalIsUninstall: false
  property bool abandonmentConfirmed: false

  property string focusSection: "inputs"
  property int focusIndex: 0
  property bool cursorActive: false

  readonly property string fontFamily: Style.font.family
  readonly property color foreground: Color.popups.text
  readonly property color dim: Color.muted
  readonly property color urgent: Color.urgent
  readonly property bool activeSession: !!status.active || status.state === "streaming" || status.state === "local" || status.state === "starting"
  readonly property string statusText: {
    var s = String(status.state || "stopped")
    if (s === "streaming" && status.captureActive) return "Recording/capture Session"
    if (s === "streaming") return "Streaming playback"
    if (s === "starting") return "Starting"
    if (s === "local") return "Local session"
    if (s === "error") return "Error"
    return "Stopped"
  }
  readonly property string remoteSummary: {
    var host = String(remote.host || "")
    var user = String(remote.user || "")
    if (!host) return "Not configured"
    return user ? (user + "@" + host) : host
  }

  function open(payloadJson) {
    var payload = {}
    try { payload = JSON.parse(payloadJson || "{}") || {} } catch (e) {}
    configurationDirty = false
    if (payload.destination !== undefined) {
      destination = normalizeDestination(payload.destination)
      configurationDirty = true
    }
    if (payload.sourceIds instanceof Array) {
      selectedIds = payload.sourceIds.slice()
      configurationDirty = true
    }
    opened = true
    cursorActive = false
    refresh()
    Qt.callLater(function() { if (opened) keyCatcher.forceActiveFocus() })
  }

  function close() {
    opened = false
  }

  function dismiss() {
    if (shell && typeof shell.hide === "function")
      shell.hide((manifest && manifest.id) || "jabaiwho.ssh-mixer")
    else close()
  }

  function sourceSelected(id) {
    return selectedIds.indexOf(String(id)) >= 0
  }

  function toggleSource(id) {
    id = String(id)
    var list = selectedIds.slice()
    var pos = list.indexOf(id)
    if (pos >= 0) list.splice(pos, 1)
    else list.push(id)
    selectedIds = list
    configurationDirty = true
    if (activeSession) requestSessionRestart()
  }

  function selectedLabels() {
    var labels = []
    for (var i = 0; i < sources.length; i++) {
      if (sourceSelected(sources[i].id)) labels.push(sources[i].label || sources[i].id)
    }
    return labels.join(", ")
  }

  function normalizeDestination(value) {
    value = String(value || "both").toLowerCase()
    return (value === "local" || value === "ssh" || value === "both") ? value : "both"
  }

  function chooseDestination(value) {
    var next = normalizeDestination(value)
    if (destination !== next) {
      destination = next
      configurationDirty = true
      if (activeSession) requestSessionRestart()
    }
  }

  function requestSessionRestart() {
    pendingSession = { destination: destination, sourceIds: selectedIds.slice() }
    message = "Applying " + destination.toUpperCase() + "…"
    if (!busy) run("stop", {})
  }

  function continueSessionRestart(finishedAction) {
    if (!pendingSession || busy) return
    if (finishedAction === "stop") {
      var next = pendingSession
      pendingSession = null
      Qt.callLater(function() { root.run("start", next) })
    } else {
      Qt.callLater(function() {
        if (root.pendingSession && !root.busy) root.run("stop", {})
      })
    }
  }

  function refresh() {
    run("snapshot", {})
  }

  function setupConnectionPayload() {
    return {
      type: setupType,
      peerId: setupType === "tailscale" ? setupPeerId : "",
      host: setupHost,
      user: setupUser,
      port: Number(setupPort || "22")
    }
  }

  function chooseTailscalePeer(peer) {
    setupType = "tailscale"
    setupPeerId = String(peer.id || "")
    setupHost = String(peer.host || "")
  }

  function profileSummary() {
    if (!pendingProfile) return ""
    var summary = "Effective receiver: " + pendingProfile.user + "@" + pendingProfile.host + ":" + pendingProfile.port
    summary += "\nIdentity sources configured: " + pendingProfile.identityCount
    if (pendingProfile.proxyJump && pendingProfile.proxyJump !== "none")
      summary += "\nProxyJump: " + pendingProfile.proxyJump
    if (pendingProfile.proxyCommandConfigured)
      summary += "\nProxyCommand executable: " + pendingProfile.proxyExecutable
    return summary + "\n\nSecurity: User-managed SSH permissions and host trust"
  }

  function inspectProfile(profile) {
    setupType = "openssh-profile"
    setupProfile = String(profile || "")
    run("profileInspect", { profile: setupProfile })
  }

  function saveProfile() {
    if (!pendingProfile) return
    run("profileSave", {
      profile: setupProfile,
      proxyConfirmed: !!pendingProfile.proxyCommandConfigured,
      expectedProxyHash: String(pendingProfile.proxyCommandHash || ""),
      expectedEffectiveHash: String(pendingProfile.effectiveConfigHash || ""),
      userManagedConfirmed: true
    })
  }

  function requestMigration(choice, platform) {
    migrationPlan = null
    migrationLegacyConnection = null
    migrationTrustOnly = false
    migrationPendingPlatform = String(platform || "")
    run("migrationPlan", {
      choice: String(choice || ""),
      platform: migrationPendingPlatform
    })
  }

  function applySimpleMigration() {
    if (!migrationPlan || migrationPlan.choice === "import-secure") return
    run("migrationApply", {
      plan: migrationPlan,
      approvedPlanHash: String(migrationPlan.planHash || "")
    })
  }

  function beginSecureMigrationTrust() {
    if (!migrationPlan || migrationPlan.choice === "start-fresh") return
    run("migrationConnection", {})
  }

  function inspectSecureMigrationTrust() {
    if (!migrationLegacyConnection) return
    connection = migrationLegacyConnection
    setupType = String(connection.type || "direct")
    setupHost = String(connection.host || "")
    setupUser = String(connection.user || "")
    setupPort = String(connection.port || "22")
    migrationTrustOnly = true
    inspectSetupTrust()
  }

  function applySecureMigration(setupPayload) {
    if (!migrationPlan || migrationPlan.choice !== "import-secure") return
    run("migrationApply", {
      plan: migrationPlan,
      approvedPlanHash: String(migrationPlan.planHash || ""),
      setupPayload: setupPayload || {}
    })
  }

  function checkReceiverUpdate() {
    if (!connection || !connection.receiverPlatform) return
    updatePlan = null
    run("updatesCheck", { platform: String(connection.receiverPlatform) })
  }

  function applyReceiverUpdate() {
    if (!updatePlan) return
    run("updatesApply", {
      plan: updatePlan,
      approvedPlanHash: String(updatePlan.planHash || "")
    })
  }

  function playQuietTest() {
    quietAwaitingConfirmation = false
    quietAudibleConfirmed = false
    run("quietTest", { dbfs: quietTestDbfs })
  }

  function increaseQuietTest() {
    if (quietTestPlayed && quietAudibleConfirmed && quietTestDbfs < -24) {
      quietTestDbfs += 4
      quietTestPlayed = false
      quietAudibleConfirmed = false
      quietAwaitingConfirmation = false
      message = "Level increased by 4 dB. Play only when ready."
    }
  }

  function planMacOsReceiver() {
    if (!connection) return
    macosSetupPlan = null
    run("macosPlan", { connection: connection })
  }

  function applyMacOsReceiver() {
    if (!connection || !macosSetupPlan) return
    var payload = {
      connection: connection,
      changesApproved: true,
      experimentalConfirmed: macosExperimentalConfirmed,
      approvedPlanHash: String(macosSetupPlan.planHash || ""),
      encryptedIdentity: encryptedManagedIdentity
    }
    if (migrationPlan && migrationPlan.choice === "import-secure") applySecureMigration(payload)
    else run("macosSetup", payload)
  }

  function planWindowsReceiver() {
    if (!connection) return
    windowsSetupPlan = null
    run("windowsPlan", {
      connection: connection,
      administratorConfirmed: windowsAdministratorConfirmed
    })
  }

  function applyWindowsReceiver() {
    if (!connection || !windowsSetupPlan) return
    var payload = {
      connection: connection,
      changesApproved: true,
      administratorConfirmed: windowsAdministratorConfirmed,
      approvedPlanHash: String(windowsSetupPlan.planHash || ""),
      encryptedIdentity: encryptedManagedIdentity
    }
    if (migrationPlan && migrationPlan.choice === "import-secure") applySecureMigration(payload)
    else run("windowsSetup", payload)
  }

  function planLinuxReceiver() {
    if (!connection) return
    linuxSetupPlan = null
    run("linuxPlan", { connection: connection })
  }

  function applyLinuxReceiver() {
    if (!connection || !linuxSetupPlan) return
    var payload = {
      connection: connection,
      changesApproved: true,
      approvedPlanHash: String(linuxSetupPlan.planHash || ""),
      encryptedIdentity: encryptedManagedIdentity
    }
    if (migrationPlan && migrationPlan.choice === "import-secure") applySecureMigration(payload)
    else run("linuxSetup", payload)
  }

  function inspectSetupTrust() {
    run("trustInspect", { connection: setupConnectionPayload() })
  }

  function trustSummary() {
    if (!pendingTrust) return ""
    var candidate = (pendingTrust.candidateFingerprints || []).join("\n")
    var approved = (pendingTrust.approvedFingerprints || []).join("\n")
    return (approved ? ("Previously trusted:\n" + approved + "\n\n") : "")
      + "Receiver fingerprint:\n" + candidate
  }

  function inspectSavedTrust() {
    if (!connection) return
    setupType = String(connection.type || "direct")
    setupPeerId = String(connection.peerId || "")
    setupHost = String(connection.host || "")
    setupUser = String(connection.user || "")
    setupPort = String(connection.port || "22")
    inspectSetupTrust()
  }

  function continueSecureMigrationSetup() {
    connection = migrationLegacyConnection
    if (migrationPlan && migrationPlan.choice === "keep-user-managed") {
      applySimpleMigration()
      return
    }
    if (migrationPendingPlatform === "linux") planLinuxReceiver()
    else if (migrationPendingPlatform === "windows") planWindowsReceiver()
    else if (migrationPendingPlatform === "macos") planMacOsReceiver()
  }

  function approveSetupTrust() {
    if (!pendingTrust) return
    run("trustApprove", {
      connection: setupConnectionPayload(),
      expectedFingerprints: pendingTrust.candidateFingerprints || []
    })
  }

  function savePrivacy(lockBehavior, showReceiverLabel) {
    var next = {
      lockBehavior: String(lockBehavior || "stop-all"),
      showReceiverLabel: showReceiverLabel === true
    }
    run("privacySave", { privacy: next })
  }

  function saveMixProfile() {
    if (!connection) {
      message = "Configure a Receiver Connection before saving a Mix Profile."
      return
    }
    if (!String(mixProfileName || "").trim()) {
      message = "Enter a Mix Profile name first."
      return
    }
    run("mixProfileSave", {
      name: String(mixProfileName).trim(),
      connection: connection,
      routeMode: destination,
      sourceIds: selectedIds.slice(),
      privacy: privacy,
      stream: {
        bitrate: String(remote.bitrate || "128k"),
        connectTimeoutSeconds: Number(remote.connectTimeoutSeconds || 5)
      },
      quickStartEnabled: true
    })
  }

  function openMixProfile(profile) {
    run("mixProfileLoad", { profileId: String(profile.id || "") })
  }

  function quickStartMixProfile(profile) {
    run("mixProfileQuickStart", {
      profileId: String(profile.id || ""),
      quickStartConfirmed: true
    })
  }

  function testConnection() {
    run("test", { destination: destination, sourceIds: selectedIds })
  }

  function start() {
    pendingSession = null
    run("start", { destination: destination, sourceIds: selectedIds })
  }

  function stop() {
    pendingSession = null
    run("stop", {})
  }

  function prepareDiagnostic() {
    run("diagnosticsPreview", { includeLogs: includeDiagnosticLogs })
  }

  function reportDiagnostic(body) {
    run("diagnosticsUrl", { body: String(body || "") })
  }

  function configureDiagnosticRetention(policy) {
    run("diagnosticsRetention", { retentionPolicy: String(policy || "standard") })
  }

  function contributeFix() {
    run("diagnosticsContribute", {})
  }

  function planRemoval(uninstall) {
    removalPlan = null
    removalIsUninstall = uninstall === true
    abandonmentConfirmed = false
    run(removalIsUninstall ? "uninstallPlan" : "removalPlan", {})
  }

  function canAbandonRemoval() {
    if (!removalPlan || !removalPlan.receivers || removalPlan.receivers.length === 0) return false
    for (var i = 0; i < removalPlan.receivers.length; i++) {
      if (String(removalPlan.receivers[i].status || "configured") === "configured") return false
    }
    return true
  }

  function applyRemoval(abandon) {
    if (!removalPlan) return
    run(removalIsUninstall ? "uninstallApply" : "removalApply", {
      plan: removalPlan,
      approvedPlanHash: String(removalPlan.planHash || ""),
      abandonPending: abandon === true,
      abandonmentConfirmation: abandon === true
        ? "ABANDON WITHOUT VERIFIED REVOCATION" : ""
    })
  }

  function clearDiagnostics() {
    diagnosticBody = ""
    diagnosticAvailable = false
    run("diagnosticsClear", {})
  }

  function run(kind, payload, quiet) {
    if (proc.running) return
    busy = true
    action = kind
    quietAction = quiet === true
    procOut = ""
    procErr = ""
    expectedStop = false
    if (kind === "snapshot") {
      proc.command = [backend, "snapshot"]
      if (!quietAction) message = "Refreshing…"
    } else if (kind === "status") {
      proc.command = [backend, "status"]
    } else if (kind === "test") {
      proc.command = [backend, "test-connection", "--json", JSON.stringify(payload || {})]
      message = "Testing " + remoteSummary + "…"
    } else if (kind === "start") {
      proc.command = [backend, "start", "--json", JSON.stringify(payload || {})]
      message = "Starting…"
    } else if (kind === "migrationPlan") {
      proc.command = [backend, "migration-plan", "--json", JSON.stringify(payload || {})]
      message = "Planning the selected legacy migration without changing it…"
    } else if (kind === "migrationConnection") {
      proc.command = [backend, "migration-connection"]
      message = "Loading the legacy receiver details for your approved import review…"
    } else if (kind === "migrationApply") {
      proc.command = [backend, "migration-apply", "--json", JSON.stringify(payload || {})]
      message = "Backing up, applying, and verifying the approved migration…"
    } else if (kind === "removalPlan") {
      proc.command = [backend, "removal-plan"]
      message = "Planning key revocation and verified local cleanup without changing anything…"
    } else if (kind === "removalApply") {
      proc.command = [backend, "removal-apply", "--json", JSON.stringify(payload || {})]
      message = payload && payload.abandonPending
        ? "Abandoning unverified remote cleanup without claiming revocation…"
        : "Revoking Receiver access before deleting associated local state…"
    } else if (kind === "uninstallPlan") {
      proc.command = [backend, "uninstall-plan"]
      message = "Listing every configured Receiver before plugin removal…"
    } else if (kind === "uninstallApply") {
      proc.command = [backend, "uninstall-apply", "--json", JSON.stringify(payload || {})]
      message = payload && payload.abandonPending
        ? "Removing the plugin after informed cleanup abandonment…"
        : "Cleaning configured Receivers before invoking Omarchy plugin removal…"
    } else if (kind === "privacySave") {
      proc.command = [backend, "configure", "--json", JSON.stringify(payload || {})]
      message = "Saving the approved lock and indicator privacy policy…"
    } else if (kind === "mixProfileSave") {
      proc.command = [backend, "mix-profile-save", "--json", JSON.stringify(payload || {})]
      message = "Saving stable Source Matchers in the Mix Profile…"
    } else if (kind === "mixProfileLoad") {
      proc.command = [backend, "mix-profile-load", "--json", JSON.stringify(payload || {})]
      message = "Opening the Mix Profile without starting a Session…"
    } else if (kind === "mixProfileQuickStart") {
      proc.command = [backend, "mix-profile-quick-start", "--json", JSON.stringify(payload || {})]
      message = "Resolving every Quick Start source uniquely…"
    } else if (kind === "stop") {
      proc.command = [backend, "stop"]
      message = "Stopping…"
    } else if (kind === "macosPlan") {
      proc.command = [backend, "receiver-macos-plan", "--json", JSON.stringify(payload || {})]
      message = "Detecting Experimental macOS Receiver capabilities without making changes…"
    } else if (kind === "macosSetup") {
      proc.command = [backend, "receiver-macos-setup", "--json", JSON.stringify(payload || {})]
      message = "Applying and verifying the approved Experimental macOS Receiver setup…"
    } else if (kind === "windowsPlan") {
      proc.command = [backend, "receiver-windows-plan", "--json", JSON.stringify(payload || {})]
      message = "Detecting Windows/OpenSSH Receiver capabilities without making changes…"
    } else if (kind === "windowsSetup") {
      proc.command = [backend, "receiver-windows-setup", "--json", JSON.stringify(payload || {})]
      message = "Applying and verifying the approved Windows Receiver setup…"
    } else if (kind === "updatesCheck") {
      proc.command = [backend, "updates-check", "--json", JSON.stringify(payload || {})]
      message = "Verifying the pinned signed Receiver release and native update plan…"
    } else if (kind === "updatesApply") {
      proc.command = [backend, "updates-apply-pinned", "--json", JSON.stringify(payload || {})]
      message = "Applying the approved native-authenticated Receiver update with retained rollback…"
    } else if (kind === "quietTest") {
      proc.command = [backend, "receiver-quiet-test", "--json", JSON.stringify(payload || {})]
      message = "Playing one short faded test at " + payload.dbfs + " dBFS without changing system volume…"
    } else if (kind === "linuxPlan") {
      proc.command = [backend, "receiver-linux-plan", "--json", JSON.stringify(payload || {})]
      message = "Detecting Linux Receiver capabilities without making changes…"
    } else if (kind === "linuxSetup") {
      proc.command = [backend, "receiver-linux-setup", "--json", JSON.stringify(payload || {})]
      message = "Applying and verifying the approved Linux Receiver setup…"
    } else if (kind === "profileInspect") {
      proc.command = [backend, "profile-inspect", "--json", JSON.stringify(payload || {})]
      message = "Inspecting the effective OpenSSH profile…"
    } else if (kind === "profileSave") {
      proc.command = [backend, "profile-save", "--json", JSON.stringify(payload || {})]
      message = "Saving the confirmed user-managed profile…"
    } else if (kind === "trustInspect") {
      proc.command = [backend, "trust-inspect", "--json", JSON.stringify(payload || {})]
      message = "Retrieving the receiver fingerprint…"
    } else if (kind === "trustApprove") {
      proc.command = [backend, "trust-approve", "--json", JSON.stringify(payload || {})]
      message = "Approving the unchanged fingerprint…"
    } else if (kind === "connectionSave") {
      proc.command = [backend, "connection-save", "--json", JSON.stringify(payload || {})]
      message = "Saving the verified connection…"
    } else if (kind === "diagnosticsPreview") {
      proc.command = [backend, "diagnostics-preview"]
      if (payload && payload.includeLogs) proc.command.push("--include-logs")
      message = "Preparing a redacted report…"
    } else if (kind === "diagnosticsUrl") {
      proc.command = [backend, "diagnostics-report-url", "--json", JSON.stringify(payload || {})]
      message = "Opening GitHub…"
    } else if (kind === "diagnosticsRetention") {
      proc.command = [backend, "diagnostics-retention", "--json", JSON.stringify(payload || {})]
      message = "Applying bounded diagnostic retention…"
    } else if (kind === "diagnosticsContribute") {
      proc.command = [backend, "diagnostics-contribute-url"]
      message = "Opening reviewed contribution guidance…"
    } else if (kind === "diagnosticsClear") {
      proc.command = [backend, "diagnostics-clear"]
      message = "Clearing diagnostics…"
    }
    proc.running = true
  }

  function applySnapshot(data) {
    recentCaptureIds = []
    if (data.sources instanceof Array) sources = data.sources.slice()
    if (data.status) status = data.status
    if (data.config) {
      if (!configurationDirty) {
        destination = normalizeDestination(data.config.destination)
        if (data.config.sourceIds instanceof Array) selectedIds = data.config.sourceIds.slice()
      }
      if (data.config.remote) remote = data.config.remote
      if (data.config.privacy) privacy = data.config.privacy
      if (data.config.mixProfiles instanceof Array) mixProfiles = data.config.mixProfiles.slice()
      if (data.config.connection) {
        connection = data.config.connection
        setupType = String(connection.type || "direct")
        setupPeerId = String(connection.peerId || "")
        setupProfile = String(connection.profile || "")
        setupHost = String(connection.host || "")
        setupUser = String(connection.user || "")
        setupPort = String(connection.port || "22")
      }
    }
    if (data.migration) migration = data.migration
    if (data.removal) removal = data.removal
    if (data.diagnostics && data.diagnostics.policy) diagnosticRetention = String(data.diagnostics.policy)
    if (data.componentVersions) componentVersions = data.componentVersions
    if (data.connectionOptions && data.connectionOptions.tailscalePeers instanceof Array)
      tailscalePeers = data.connectionOptions.tailscalePeers.slice()
    if (data.connectionOptions && data.connectionOptions.openSshProfiles instanceof Array)
      openSshProfiles = data.connectionOptions.openSshProfiles.slice()
    clampCursor()
  }

  function applyStatus(data) {
    if (data.status) status = data.status
    if (data.config && data.config.remote) remote = data.config.remote
  }

  function handleResult(exitCode) {
    var finishedAction = action
    var finishedQuiet = quietAction
    busy = false
    var raw = procOut || "{}"
    var data = {}
    try { data = JSON.parse(raw) || {} } catch (e) { data = { ok: false, error: procErr || "Could not parse ssh-mixer output" } }
    if (!data.ok) diagnosticAvailable = true

    if (action === "snapshot") {
      applySnapshot(data)
      if (!finishedQuiet || !data.ok) message = data.ok ? "Ready" : (data.error || procErr || "Refresh failed")
      continueSessionRestart(finishedAction)
      return
    }
    if (action === "status") {
      applyStatus(data)
      continueSessionRestart(finishedAction)
      return
    }
    if (action === "test") {
      applyStatus(data)
      message = data.ok ? ("Connection OK: " + remoteSummary) : (data.error || (data.connection && data.connection.error) || procErr || "Connection failed")
      continueSessionRestart(finishedAction)
      return
    }
    if (action === "start") {
      applyStatus(data)
      if (data.ok) {
        configurationDirty = false
        message = statusText + (selectedLabels() ? ": " + selectedLabels() : "")
      } else {
        message = data.error || procErr || "Start failed"
      }
      continueSessionRestart(finishedAction)
      return
    }
    if (action === "stop") {
      applyStatus(data)
      message = data.ok ? "Stopped and cleaned up" : (data.error || procErr || "Stop failed")
      continueSessionRestart(finishedAction)
      return
    }
    if (action === "migrationPlan") {
      if (data.ok && data.plan) {
        migrationPlan = data.plan
        message = data.plan.choice === "import-secure"
          ? "Review the protected backup and Managed Identity changes, then continue to receiver trust and setup."
          : "Review every migration change, then confirm. No legacy state has changed yet."
      } else message = data.error || procErr || "Could not plan legacy migration"
      return
    }
    if (action === "migrationConnection") {
      if (data.ok && data.connection) {
        migrationLegacyConnection = data.connection
        Qt.callLater(function() { root.inspectSecureMigrationTrust() })
      } else message = data.error || procErr || "Could not review the legacy receiver for import"
      return
    }
    if (action === "migrationApply") {
      if (data.ok && data.migration) {
        migration = ({ detected: false, reasons: [], choices: [] })
        migrationPlan = null
        migrationLegacyConnection = null
        migrationPendingPlatform = ""
        migrationTrustOnly = false
        linuxSetupPlan = null
        windowsSetupPlan = null
        macosSetupPlan = null
        if (data.migration.config) {
          if (data.migration.config.connection) connection = data.migration.config.connection
          if (data.migration.config.remote) remote = data.migration.config.remote
          destination = normalizeDestination(data.migration.config.destination)
          selectedIds = []
        }
        message = "Legacy migration verified; backup retired and obsolete command/source-id state removed."
      } else if (data.migration && data.migration.deferred) {
        message = "Migration is waiting. Stop the active Session explicitly, then retry; it was not interrupted."
      } else if (data.migration && data.migration.rollbackIncomplete) {
        message = "Migration failed and rollback is incomplete. The protected backup remains available; review diagnostics."
      } else {
        message = "Migration failed at " + String(data.migration ? data.migration.stage : "apply") + "; the prior configuration was restored from the protected backup."
      }
      return
    }
    if (action === "removalPlan" || action === "uninstallPlan") {
      if (data.ok && data.plan) {
        removalPlan = data.plan
        removalIsUninstall = action === "uninstallPlan"
        abandonmentConfirmed = false
        message = "Review every Receiver and cleanup step. Nothing has been removed yet."
      } else message = data.error || procErr || "Could not plan verified cleanup"
      return
    }
    if (action === "removalApply" || action === "uninstallApply") {
      if (data.pendingCleanup) removal = data.pendingCleanup
      if (data.ok) {
        removalPlan = null
        abandonmentConfirmed = false
        removal = ({ pendingCount: 0, pending: [] })
        if (data.revocation === "abandoned-not-revoked") {
          message = "Local cleanup completed after abandonment. Remote access was NOT verified revoked."
        } else if (action === "uninstallApply") {
          message = "Every Receiver cleanup and local deletion verified; Omarchy plugin removal invoked."
        } else {
          connection = null
          remote = ({ host: "", user: "" })
          mixProfiles = []
          message = "Receiver key revocation and associated local cleanup verified."
          Qt.callLater(function() { root.refresh() })
        }
      } else if (data.deferred) {
        message = "Cleanup is waiting. Stop the active Session explicitly, then retry; it was not interrupted."
      } else if (data.revocation === "pending") {
        removalPlan = null
        abandonmentConfirmed = false
        message = "Receiver cleanup is pending, not revoked. Retry when reachable or use Companion Setup; local retry credentials were retained."
      } else {
        message = data.error || procErr || "Cleanup failed without a verified revocation"
      }
      return
    }
    if (action === "privacySave") {
      if (data.ok && data.config && data.config.privacy) {
        privacy = data.config.privacy
        message = privacy.lockBehavior === "continue-playback"
          ? "On lock, non-microphone playback may continue; every Capture Session still stops."
          : "On lock, every Session stops and cleans up."
      } else message = data.error || procErr || "Could not save privacy policy"
      return
    }
    if (action === "mixProfileSave") {
      if (data.ok && data.config) {
        if (data.config.mixProfiles instanceof Array) mixProfiles = data.config.mixProfiles.slice()
        mixProfileName = ""
        message = data.profile && data.profile.requiresCaptureConfirmation
          ? "Mix Profile saved. Capture Sources remain recent choices and always require confirmation."
          : (data.profile && data.profile.quickStartEnabled
            ? "Playback-only Mix Profile saved with explicit Quick Start."
            : "Mix Profile saved. Open it in the mixer to review sources before Start.")
      } else message = data.error || procErr || "Could not save Mix Profile"
      return
    }
    if (action === "mixProfileLoad" || action === "mixProfileQuickStart") {
      if (data.config) {
        destination = normalizeDestination(data.config.destination)
        selectedIds = data.config.sourceIds instanceof Array ? data.config.sourceIds.slice() : []
        if (data.config.remote) remote = data.config.remote
        if (data.config.connection) connection = data.config.connection
      }
      recentCaptureIds = data.recentCaptureIds instanceof Array
        ? data.recentCaptureIds.slice()
        : (data.resolution && data.resolution.recentCaptureIds instanceof Array
          ? data.resolution.recentCaptureIds.slice() : [])
      if (data.ok && data.started) {
        applyStatus(data)
        message = "Quick Start began after every saved source matched uniquely."
      } else if (data.openMixer) {
        message = data.reason === "capture-confirmation-required"
          ? "Mixer opened: reselect and confirm every Capture Source before Start."
          : (data.reason === "session-active"
            ? "Mixer opened without interrupting the active Session. Stop it before Quick Start."
            : "Mixer opened because a Quick Start source is missing or ambiguous. Nothing started.")
      } else if (data.ok) {
        message = data.profile && data.profile.requiresCaptureConfirmation
          ? "Mix Profile opened. Reselect each recent Capture Source, review it, then Start explicitly."
          : "Mix Profile opened. Review sources, then Start explicitly."
      } else message = data.error || procErr || "Could not open Mix Profile"
      return
    }
    if (action === "macosPlan") {
      if (data.ok && data.plan) {
        macosSetupPlan = data.plan
        macosExperimentalConfirmed = false
        message = "Experimental: no real-device macOS verification is recorded. Review Remote Login, key, helper, and Homebrew changes before approval."
      } else message = data.error || procErr || "Could not plan Experimental macOS Receiver setup"
      return
    }
    if (action === "macosSetup") {
      if (data.ok) {
        connection = data.connection
        if (data.config && data.config.remote) remote = data.config.remote
        macosSetupPlan = null
        message = "Experimental macOS Receiver restrictions verified; real-device compatibility remains unverified"
      } else if (data.setup && data.setup.rollbackIncomplete) {
        message = "Experimental macOS setup failed and rollback is incomplete. Review the redacted Diagnostic Report before retrying."
      } else message = data.error || procErr || "Experimental macOS Receiver setup failed"
      return
    }
    if (action === "windowsPlan") {
      if (data.ok && data.plan) {
        windowsSetupPlan = data.plan
        windowsAdministratorConfirmed = !!data.plan.administratorConfirmed
        message = data.plan.administratorConfirmationRequired && !data.plan.administratorConfirmed
          ? "This account can administer Windows. Confirm that fact, then re-plan before applying changes. Runtime elevation will still be refused."
          : "Review every Windows/OpenSSH, firewall, ACL, helper, and package change before approval."
      } else message = data.error || procErr || "Could not plan Windows Receiver setup"
      return
    }
    if (action === "windowsSetup") {
      if (data.ok) {
        connection = data.connection
        if (data.config && data.config.remote) remote = data.config.remote
        windowsSetupPlan = null
        message = "Windows Receiver installed with non-elevated runtime and forced-command restrictions verified"
      } else if (data.setup && data.setup.rollbackIncomplete) {
        message = "Windows setup failed and rollback is incomplete. Review diagnostics before retrying."
      } else message = data.error || procErr || "Windows Receiver setup failed"
      return
    }
    if (action === "updatesCheck") {
      if (data.ok && data.plan) updatePlan = data.plan
      message = data.ok
        ? (data.plan.status === "current" ? "Receiver components are current." : "Review the complete signed update and native-authentication plan before approval.")
        : (data.error || procErr || "Could not verify Receiver update availability")
      return
    }
    if (action === "updatesApply") {
      if (data.ok) updatePlan = null
      message = data.ok
        ? "Receiver update committed after verification."
        : (data.update && data.update.rollback === "incomplete"
          ? "Receiver update failed and rollback is incomplete. Review diagnostics before retrying."
          : (data.error || procErr || "Receiver update failed and rollback status was reported."))
      return
    }
    if (action === "quietTest") {
      if (data.ok) {
        quietTestPlayed = true
        quietAwaitingConfirmation = true
        message = "Quiet test finished at " + data.dbfs + " dBFS. Confirm whether you heard it before increasing."
      } else message = data.error || procErr || "Quiet Receiver test failed"
      return
    }
    if (action === "linuxPlan") {
      if (data.ok && data.plan) {
        linuxSetupPlan = data.plan
        message = "Review every Linux Receiver change before approval. Authentication and privilege prompts remain native to OpenSSH and sudo."
      } else message = data.error || procErr || "Could not plan Linux Receiver setup"
      return
    }
    if (action === "linuxSetup") {
      if (data.ok) {
        connection = data.connection
        if (data.config && data.config.remote) remote = data.config.remote
        linuxSetupPlan = null
        message = "Linux Receiver installed and forced-command restrictions verified"
      } else if (data.setup && data.setup.rollbackIncomplete) {
        message = "Setup failed and rollback is incomplete. Review diagnostics before retrying."
      } else message = data.error || procErr || "Linux Receiver setup failed"
      return
    }
    if (action === "profileInspect") {
      if (data.ok && data.profile) {
        pendingProfile = data.profile
        message = data.profile.proxyCommandConfigured
          ? "This profile executes " + data.profile.proxyExecutable + " as a local ProxyCommand. Review and confirm both the proxy and user-managed SSH permissions."
          : "Review and confirm that this profile keeps user-managed SSH permissions."
      } else message = data.error || procErr || "Could not inspect the OpenSSH profile"
      return
    }
    if (action === "profileSave") {
      if (data.ok) {
        connection = data.connection
        pendingProfile = null
        if (data.config && data.config.remote) remote = data.config.remote
        message = "User-managed OpenSSH profile saved"
      } else message = data.error || procErr || "Could not save the OpenSSH profile"
      return
    }
    if (action === "trustInspect") {
      if (data.ok && data.trust) {
        if (data.trust.status === "trusted") {
          pendingTrust = null
          if (migrationTrustOnly) Qt.callLater(function() { root.continueSecureMigrationSetup() })
          else Qt.callLater(function() { root.run("connectionSave", { connection: root.setupConnectionPayload() }) })
        } else {
          pendingTrust = data.trust
          message = data.trust.status === "changed"
            ? "Warning: the receiver host key changed. Review both fingerprints before replacing trust."
            : "Review the receiver fingerprint, then choose Trust and save."
        }
      } else message = data.error || procErr || "Could not inspect receiver trust"
      return
    }
    if (action === "trustApprove") {
      if (data.ok) {
        pendingTrust = null
        if (migrationTrustOnly) Qt.callLater(function() { root.continueSecureMigrationSetup() })
        else Qt.callLater(function() { root.run("connectionSave", { connection: root.setupConnectionPayload() }) })
      } else message = data.error || procErr || "Trust approval failed"
      return
    }
    if (action === "connectionSave") {
      if (data.ok) {
        connection = data.connection
        if (data.config && data.config.remote) remote = data.config.remote
        message = "Verified connection saved"
      } else message = data.error || procErr || "Could not save connection"
      return
    }
    if (action === "diagnosticsPreview") {
      if (data.ok && data.report) diagnosticBody = String(data.report.body || "")
      message = data.ok ? "Review and edit the report before opening GitHub." : (data.error || procErr || "Could not prepare diagnostics")
      return
    }
    if (action === "diagnosticsUrl") {
      if (data.ok && data.url) Qt.openUrlExternally(String(data.url))
      message = data.ok ? "Review the issue in GitHub before submitting." : (data.error || procErr || "Could not open GitHub")
      return
    }
    if (action === "diagnosticsRetention") {
      if (data.ok && data.settings) diagnosticRetention = String(data.settings.policy || "standard")
      message = data.ok ? "Diagnostic retention updated and older events pruned." : (data.error || procErr || "Could not change diagnostic retention")
      return
    }
    if (action === "diagnosticsContribute") {
      if (data.ok && data.url) Qt.openUrlExternally(String(data.url))
      message = data.ok ? "Review the security and platform-fix requirements before opening a pull request." : (data.error || procErr || "Could not open contribution guidance")
      return
    }
    if (action === "diagnosticsClear") {
      message = data.ok ? "Diagnostics cleared" : (data.error || procErr || "Could not clear diagnostics")
    }
  }

  function sectionLength(section) {
    if (section === "inputs") return Math.max(1, sources.length)
    if (section === "destination") return 3
    if (section === "actions") return 3
    return 0
  }

  function sectionOrder() { return ["inputs", "destination", "actions"] }

  function clampCursor() {
    var max = sectionLength(focusSection) - 1
    if (focusIndex > max) focusIndex = max
    if (focusIndex < 0) focusIndex = 0
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    var sections = sectionOrder()
    var s = sections.indexOf(focusSection)
    if (s < 0) { focusSection = "inputs"; focusIndex = 0; return }
    if (dx !== 0 && focusSection === "destination") {
      focusIndex = Math.max(0, Math.min(2, focusIndex + dx))
      chooseDestination(["local", "ssh", "both"][focusIndex])
      return
    }
    if (dy > 0) {
      if (focusIndex < sectionLength(focusSection) - 1) focusIndex++
      else if (s < sections.length - 1) { focusSection = sections[s + 1]; focusIndex = 0 }
    } else if (dy < 0) {
      if (focusIndex > 0) focusIndex--
      else if (s > 0) { focusSection = sections[s - 1]; focusIndex = sectionLength(focusSection) - 1 }
    }
  }

  function tabSection(direction) {
    cursorActive = true
    var sections = sectionOrder()
    var s = sections.indexOf(focusSection)
    if (s < 0) s = 0
    focusSection = sections[(s + direction + sections.length) % sections.length]
    focusIndex = Math.min(focusIndex, sectionLength(focusSection) - 1)
  }

  function activateCursor() {
    cursorActive = true
    if (focusSection === "inputs" && sources.length > 0) toggleSource(sources[focusIndex].id)
    else if (focusSection === "destination") chooseDestination(["local", "ssh", "both"][focusIndex])
    else if (focusSection === "actions") {
      if (focusIndex === 0) refresh()
      else if (focusIndex === 1) testConnection()
      else if (focusIndex === 2) activeSession ? stop() : start()
    }
  }

  Timer {
    interval: 2000
    running: root.opened && !root.busy
    repeat: true
    onTriggered: root.run("snapshot", {}, true)
  }

  Process {
    id: proc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.procOut = text }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root.procErr = String(text || "").trim() }
    onExited: function(exitCode) { root.handleResult(exitCode) }
  }

  PanelWindow {
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "jabaiwho.ssh-mixer"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

    Rectangle {
      anchors.fill: parent
      color: Util.alpha(Color.background, 0.78)
      MouseArea { anchors.fill: parent; onClicked: root.dismiss() }
    }

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.dismiss()
      onTabRequested: function(direction) { root.tabSection(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") root.refresh()
        else if (t === "s" || t === "S") root.activeSession ? root.stop() : root.start()
      }

      Item {
        anchors.centerIn: parent
        width: Math.min(parent.width - Style.space(32), Style.space(460))
        height: Math.min(parent.height - Style.space(32), card.implicitHeight)

        MouseArea { anchors.fill: parent; onClicked: {} }

        BorderSurface {
          id: card
          width: parent.width
          height: parent.height
          color: Color.popups.background
          radius: Style.cornerRadius
          padding: Style.space(16)
          borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
          implicitHeight: content.implicitHeight + contentTopInset + contentBottomInset

          Flickable {
            id: flick
            anchors.fill: parent
            anchors.margins: card.contentLeftInset
            contentWidth: width
            contentHeight: content.implicitHeight
            clip: true
            interactive: contentHeight > height
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
              id: content
              width: flick.width
              spacing: Style.space(14)

              RowLayout {
                width: parent.width
                spacing: Style.space(12)

                Text {
                  text: ""
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.display
                  Layout.alignment: Qt.AlignVCenter
                }

                ColumnLayout {
                  Layout.fillWidth: true
                  spacing: Style.space(2)
                  Text {
                    text: "SSH-mixer"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.title
                    font.bold: true
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                  }
                  Text {
                    text: root.statusText + " • " + root.remoteSummary
                    color: root.status.state === "error" ? root.urgent : root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                  }
                }

                Button {
                  text: "×"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: root.dismiss()
                }
              }

              PanelSeparator { foreground: root.foreground; width: parent.width }

              Column {
                width: parent.width
                visible: root.migration.detected === true
                spacing: Style.space(8)
                PanelSectionHeader { text: "LEGACY MIGRATION REQUIRED"; foreground: root.urgent; fontFamily: root.fontFamily }
                Text {
                  width: parent.width
                  text: root.migration.sessionActive
                    ? "An active Session is using the legacy configuration. Migration will wait and will never stop it implicitly."
                    : "Legacy receiver, command, or temporary source-id state was detected without adding its private values to logs or reports. Choose one reviewed transaction."
                  color: root.migration.sessionActive ? root.urgent : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }
                Text {
                  visible: !!root.migrationPlan
                  width: parent.width
                  text: root.migrationPlan ? root.migrationPlan.changes.join("\n• ").replace(/^/, "• ") : ""
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: "Import & secure · Linux"
                    rowIndex: -1
                    onPressed: root.requestMigration("import-secure", "linux")
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Import · Windows"
                    rowIndex: -1
                    onPressed: root.requestMigration("import-secure", "windows")
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Import · macOS Experimental"
                    rowIndex: -1
                    onPressed: root.requestMigration("import-secure", "macos")
                    Layout.fillWidth: true
                  }
                }
                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: "Keep user-managed"
                    rowIndex: -1
                    onPressed: root.requestMigration("keep-user-managed", "")
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Start fresh"
                    rowIndex: -1
                    onPressed: root.requestMigration("start-fresh", "")
                    Layout.fillWidth: true
                  }
                }
                ActionButton {
                  visible: !!root.migrationPlan
                  label: root.migrationPlan && root.migrationPlan.choice === "import-secure"
                    ? "Continue to trust and Receiver setup"
                    : (root.migrationPlan && root.migrationPlan.choice === "keep-user-managed"
                      ? "Continue to trust and keep user-managed"
                      : "Confirm exact migration plan")
                  rowIndex: -1
                  onPressed: root.migrationPlan && root.migrationPlan.choice !== "start-fresh"
                    ? root.beginSecureMigrationTrust()
                    : root.applySimpleMigration()
                  width: parent.width
                }
              }

              PanelSeparator { visible: root.migration.detected === true; foreground: root.foreground; width: parent.width }

              Column {
                width: parent.width
                visible: !root.migration.detected && !root.connection && !root.remote.host
                spacing: Style.space(8)
                PanelSectionHeader { text: "RECEIVER SETUP"; foreground: root.foreground; fontFamily: root.fontFamily }

                Text {
                  width: parent.width
                  text: "Tailscale is recommended and verified on every connection. Direct SSH remains available explicitly."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  Button {
                    text: "Tailscale · Recommended"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    selected: root.setupType === "tailscale"
                    onClicked: root.setupType = "tailscale"
                    Layout.fillWidth: true
                  }
                  Button {
                    text: "Direct SSH"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    selected: root.setupType === "direct"
                    onClicked: { root.setupType = "direct"; root.setupPeerId = ""; root.pendingProfile = null }
                    Layout.fillWidth: true
                  }
                  Button {
                    text: "SSH Profile"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    selected: root.setupType === "openssh-profile"
                    onClicked: { root.setupType = "openssh-profile"; root.setupPeerId = "" }
                    Layout.fillWidth: true
                  }
                }

                Repeater {
                  model: root.setupType === "tailscale" ? root.tailscalePeers : []
                  Button {
                    required property var modelData
                    width: content.width
                    text: (modelData.label || modelData.host) + (root.setupPeerId === String(modelData.id) ? "  ✓" : "")
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    selected: root.setupPeerId === String(modelData.id)
                    onClicked: root.chooseTailscalePeer(modelData)
                  }
                }

                Repeater {
                  model: root.setupType === "openssh-profile" ? root.openSshProfiles : []
                  Button {
                    required property var modelData
                    width: content.width
                    text: String(modelData) + (root.setupProfile === String(modelData) ? "  ✓" : "")
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    selected: root.setupProfile === String(modelData)
                    onClicked: root.inspectProfile(modelData)
                  }
                }

                Text {
                  visible: root.setupType === "openssh-profile" && root.openSshProfiles.length === 0
                  width: parent.width
                  text: "No concrete Host aliases were found in your OpenSSH configuration."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                Text {
                  visible: root.setupType === "tailscale" && root.tailscalePeers.length === 0
                  width: parent.width
                  text: "No online Tailscale peers were found. Refresh, start Tailscale, or choose Direct SSH."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                TextField {
                  visible: root.setupType === "direct"
                  width: parent.width
                  placeholderText: "Receiver hostname or IP address"
                  text: root.setupHost
                  onTextChanged: root.setupHost = text
                }

                RowLayout {
                  visible: root.setupType !== "openssh-profile"
                  width: parent.width
                  spacing: Style.space(8)
                  TextField {
                    Layout.fillWidth: true
                    placeholderText: "Remote username"
                    text: root.setupUser
                    onTextChanged: root.setupUser = text
                  }
                  TextField {
                    Layout.preferredWidth: Style.space(80)
                    placeholderText: "Port"
                    text: root.setupPort
                    inputMethodHints: Qt.ImhDigitsOnly
                    onTextChanged: root.setupPort = text
                  }
                }

                Text {
                  visible: !!root.pendingProfile
                  width: parent.width
                  text: root.profileSummary()
                  color: root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                RowLayout {
                  visible: !!root.pendingProfile
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: "Confirm and save profile"
                    rowIndex: -1
                    onPressed: root.saveProfile()
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Cancel"
                    rowIndex: -1
                    onPressed: { root.pendingProfile = null; root.message = "Profile was not saved." }
                    Layout.fillWidth: true
                  }
                }

                Text {
                  visible: !!root.pendingTrust
                  width: parent.width
                  text: root.trustSummary()
                  color: root.pendingTrust && root.pendingTrust.status === "changed" ? root.urgent : root.foreground
                  font.family: "monospace"
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WrapAnywhere
                }

                RowLayout {
                  visible: root.setupType !== "openssh-profile"
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: root.pendingTrust
                      ? (root.migrationTrustOnly ? "Trust for secure import" : "Trust and save")
                      : "Review connection"
                    rowIndex: -1
                    onPressed: root.pendingTrust ? root.approveSetupTrust() : root.inspectSetupTrust()
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    visible: !!root.pendingTrust
                    label: "Cancel"
                    rowIndex: -1
                    onPressed: {
                      root.pendingTrust = null
                      root.migrationTrustOnly = false
                      root.message = "Trust was not changed. Legacy migration remains unchanged."
                    }
                    Layout.fillWidth: true
                  }
                }
              }

              PanelSeparator {
                visible: !root.connection && !root.remote.host
                foreground: root.foreground
                width: parent.width
              }

              Column {
                width: parent.width
                spacing: Style.space(8)
                PanelSectionHeader { text: "INPUTS"; foreground: root.foreground; fontFamily: root.fontFamily }

                Text {
                  visible: root.sources.length === 0
                  width: parent.width
                  text: root.busy ? "Looking for PipeWire/PulseAudio sources…" : "No useful inputs found. Start cliamp playback or refresh."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                Repeater {
                  model: root.sources
                  SourceRow {
                    required property var modelData
                    required property int index
                    width: content.width
                    sourceData: modelData
                    rowIndex: index
                  }
                }
              }

              PanelSeparator { foreground: root.foreground; width: parent.width }

              Column {
                width: parent.width
                spacing: Style.space(8)
                PanelSectionHeader { text: "MIX PROFILES"; foreground: root.foreground; fontFamily: root.fontFamily }
                Text {
                  width: parent.width
                  text: "Profiles remember the Connection, Route Mode, stable Source Matchers, privacy, and stream settings. Quick Start is explicit and never starts Capture Sources."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                Repeater {
                  model: root.mixProfiles
                  RowLayout {
                    required property var modelData
                    width: parent.width
                    spacing: Style.space(8)
                    ActionButton {
                      label: modelData.quickStartEnabled ? ("Quick Start · " + modelData.name) : ("Open · " + modelData.name)
                      rowIndex: -1
                      onPressed: modelData.quickStartEnabled
                        ? root.quickStartMixProfile(modelData)
                        : root.openMixProfile(modelData)
                      Layout.fillWidth: true
                    }
                    Text {
                      text: modelData.requiresCaptureConfirmation ? "Capture confirmation required" : "Playback only"
                      color: modelData.requiresCaptureConfirmation ? root.urgent : root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      Layout.preferredWidth: Style.space(150)
                      elide: Text.ElideRight
                    }
                  }
                }
                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  TextField {
                    Layout.fillWidth: true
                    placeholderText: "New Mix Profile name"
                    text: root.mixProfileName
                    onTextChanged: root.mixProfileName = text
                  }
                  ActionButton {
                    label: "Save profile"
                    rowIndex: -1
                    onPressed: root.saveMixProfile()
                    Layout.preferredWidth: Style.space(120)
                  }
                }
              }

              PanelSeparator { foreground: root.foreground; width: parent.width }

              Column {
                width: parent.width
                spacing: Style.space(8)
                PanelSectionHeader { text: "DESTINATION"; foreground: root.foreground; fontFamily: root.fontFamily }
                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  DestinationButton { label: "Local"; value: "local"; rowIndex: 0; Layout.fillWidth: true }
                  DestinationButton { label: "SSH"; value: "ssh"; rowIndex: 1; Layout.fillWidth: true }
                  DestinationButton { label: "Both"; value: "both"; rowIndex: 2; Layout.fillWidth: true }
                }
                Text {
                  width: parent.width
                  text: "Microphones are never fed into local speakers by SSH-mixer; Local leaves normal capture availability alone."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
              }

              PanelSeparator { foreground: root.foreground; width: parent.width }

              Column {
                width: parent.width
                spacing: Style.space(8)
                PanelSectionHeader { text: "PRIVACY LIFECYCLE"; foreground: root.foreground; fontFamily: root.fontFamily }
                Text {
                  width: parent.width
                  text: "Stop all on lock is the default. The alternative may continue playback only; Capture Sources always stop and never resume after unlock. Suspend, logout, disconnect, and fatal network loss always stop and clean up."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  PrivacyButton {
                    label: "Stop all on lock"
                    value: "stop-all"
                    Layout.fillWidth: true
                  }
                  PrivacyButton {
                    label: "Continue playback on lock"
                    value: "continue-playback"
                    Layout.fillWidth: true
                  }
                }
                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  Text {
                    text: "Show Receiver label on persistent bar indicator"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                  }
                  ToggleSwitch {
                    checked: root.privacy.showReceiverLabel === true
                    foreground: root.foreground
                    onToggled: root.savePrivacy(
                      root.privacy.lockBehavior,
                      root.privacy.showReceiverLabel !== true
                    )
                  }
                }
              }

              PanelSeparator { foreground: root.foreground; width: parent.width }

              Column {
                width: parent.width
                spacing: Style.space(8)
                PanelSectionHeader { text: "REMOTE"; foreground: root.foreground; fontFamily: root.fontFamily }
                Text {
                  width: parent.width
                  text: root.remoteSummary + " via Opus/Ogg over SSH"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  elide: Text.ElideRight
                }
                Text {
                  width: parent.width
                  text: root.connection && root.connection.type === "tailscale"
                    ? "Tailscale peer · verified before every connection"
                    : (root.connection && root.connection.type === "openssh-profile"
                      ? "OpenSSH Profile · user-managed permissions and host trust"
                      : (root.connection ? "Direct SSH · not Tailscale verified" : "Legacy connection · migration required"))
                  color: root.connection && root.connection.type === "tailscale" ? root.foreground : root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                Text {
                  visible: !!root.connection && root.connection.securityLevel === "user-managed"
                  width: parent.width
                  text: "User-managed identity · SSH-mixer cannot guarantee receiver-only key permissions"
                  color: root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                Text {
                  visible: !!root.connection && root.connection.securityLevel === "receiver-only"
                  width: parent.width
                  text: "Managed Identity · dedicated to this Receiver · forced Receiver Protocol v1 verified"
                    + (root.connection && root.connection.experimental ? " · macOS Experimental (no real-device verification)" : "")
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                Text {
                  visible: !!root.connection && root.connection.securityLevel === "receiver-only"
                  width: parent.width
                  text: {
                    var connection = root.connection || ({})
                    var platform = String(connection.receiverPlatform || "")
                    var receiverVersions = root.componentVersions.receiver || {}
                    var companionVersions = root.componentVersions.companion || {}
                    return "Versions · plugin " + String(root.componentVersions.plugin || "unknown")
                      + " · Companion " + String(companionVersions[platform] || "unknown")
                      + " · helper " + String(receiverVersions[platform] || "unknown")
                      + " · protocol " + String(root.componentVersions.protocol || "unknown")
                  }
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                Text {
                  visible: !!root.connection && root.connection.securityLevel === "receiver-only"
                    && !root.componentVersions.signedUpdateTrustConfigured
                  width: parent.width
                  text: "Signed updates are fail-closed: no reviewed production release trust root is configured. Nothing will install automatically."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                ActionButton {
                  visible: !!root.connection && root.connection.securityLevel === "receiver-only"
                    && root.componentVersions.signedUpdateTrustConfigured
                  label: "Check signed Receiver update"
                  rowIndex: -1
                  onPressed: root.checkReceiverUpdate()
                  Layout.fillWidth: true
                }

                Column {
                  visible: !!root.updatePlan
                  width: parent.width
                  spacing: Style.space(6)

                  Text {
                    width: parent.width
                    text: root.updatePlan
                      ? ("Signed release " + root.updatePlan.releaseId + " · " + root.updatePlan.status
                        + "\nReceiver account: " + root.updatePlan.transaction.receiverUser
                        + "\nHelper path: " + root.updatePlan.transaction.receiverPath
                        + "\nAuthorized keys: " + root.updatePlan.transaction.authorizedKeysPath
                        + "\nNative authentication: " + root.updatePlan.transaction.authentication
                        + (root.updatePlan.transaction.privilegeRequired ? " · native privilege approval disclosed below" : " · no privilege change")
                        + "\nRollback: " + root.updatePlan.transaction.rollback)
                      : ""
                    color: root.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                  }

                  Repeater {
                    model: root.updatePlan && root.updatePlan.changes ? root.updatePlan.changes : []
                    delegate: Text {
                      required property var modelData
                      width: parent.width
                      text: "• " + modelData.component + " " + modelData.version + ": " + modelData.items.join("; ")
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }

                  Repeater {
                    model: root.updatePlan && root.updatePlan.transaction ? root.updatePlan.transaction.changes : []
                    delegate: Text {
                      required property var modelData
                      width: parent.width
                      text: "• " + modelData
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }

                  Repeater {
                    model: root.updatePlan && root.updatePlan.transaction && root.updatePlan.transaction.privilegeChanges ? root.updatePlan.transaction.privilegeChanges : []
                    delegate: Text {
                      required property var modelData
                      width: parent.width
                      text: "Native privilege approval: " + modelData
                      color: root.urgent
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }

                  ActionButton {
                    visible: !!root.updatePlan && root.updatePlan.status !== "current"
                    label: "Approve exact update plan"
                    rowIndex: -1
                    onPressed: root.applyReceiverUpdate()
                    Layout.fillWidth: true
                  }
                }

                Text {
                  visible: !!root.connection && root.connection.experimental
                  width: parent.width
                  text: "EXPERIMENTAL macOS: automated restrictions passed, but hardware compatibility is not claimed."
                  color: root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  wrapMode: Text.WordWrap
                }
                Column {
                  visible: !!root.connection && root.connection.securityLevel === "receiver-only"
                  width: parent.width
                  spacing: Style.space(6)
                  Text {
                    width: parent.width
                    text: "Quiet test: " + root.quietTestDbfs + " dBFS · 0.5 seconds · faded · never changes system volume"
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }
                  RowLayout {
                    width: parent.width
                    spacing: Style.space(8)
                    ActionButton {
                      label: "Play once"
                      rowIndex: -1
                      onPressed: root.playQuietTest()
                      Layout.fillWidth: true
                    }
                    ActionButton {
                      label: root.quietTestDbfs >= -24 ? "Maximum -24 dBFS" : "Increase by 4 dB"
                      rowIndex: -1
                      enabled: root.quietTestPlayed && root.quietAudibleConfirmed && root.quietTestDbfs < -24
                      onPressed: root.increaseQuietTest()
                      Layout.fillWidth: true
                    }
                  }
                  RowLayout {
                    visible: root.quietAwaitingConfirmation
                    width: parent.width
                    spacing: Style.space(8)
                    ActionButton {
                      label: "I heard it"
                      rowIndex: -1
                      onPressed: {
                        root.quietAudibleConfirmed = true
                        root.quietAwaitingConfirmation = false
                        root.message = "Audible output confirmed at " + root.quietTestDbfs + " dBFS."
                      }
                      Layout.fillWidth: true
                    }
                    ActionButton {
                      label: "I did not hear it"
                      rowIndex: -1
                      onPressed: {
                        root.quietAudibleConfirmed = false
                        root.quietAwaitingConfirmation = false
                        root.message = "No audible output confirmed. The level was not increased; review diagnostics or replay once."
                      }
                      Layout.fillWidth: true
                    }
                  }
                }
                RowLayout {
                  visible: !!root.connection
                    && root.connection.type !== "openssh-profile"
                    && root.connection.securityLevel !== "receiver-only"
                    && !root.linuxSetupPlan
                    && !root.windowsSetupPlan
                    && !root.macosSetupPlan
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: "Plan Linux Receiver"
                    rowIndex: -1
                    onPressed: root.planLinuxReceiver()
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Plan Windows Receiver"
                    rowIndex: -1
                    onPressed: root.planWindowsReceiver()
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Plan macOS (Experimental)"
                    rowIndex: -1
                    onPressed: root.planMacOsReceiver()
                    Layout.fillWidth: true
                  }
                }
                Column {
                  visible: !!root.linuxSetupPlan
                  width: parent.width
                  spacing: Style.space(6)
                  Text {
                    width: parent.width
                    text: "Approved changes only — no changes have been applied yet:"
                    color: root.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                  }
                  Repeater {
                    model: root.linuxSetupPlan && root.linuxSetupPlan.changes ? root.linuxSetupPlan.changes : []
                    Text {
                      required property var modelData
                      width: parent.width
                      text: "• " + modelData.summary + (modelData.requiresPrivilege ? " (native sudo prompt)" : "")
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }
                  Text {
                    visible: !!root.linuxSetupPlan && root.linuxSetupPlan.packageCommand && root.linuxSetupPlan.packageCommand.length > 0
                    width: parent.width
                    text: "Package command: " + (root.linuxSetupPlan ? root.linuxSetupPlan.packageCommand.join(" ") : "")
                    color: root.dim
                    font.family: "monospace"
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WrapAnywhere
                  }
                  CheckBox {
                    text: "Encrypt the dedicated key and load it into my existing ssh-agent"
                    checked: root.encryptedManagedIdentity
                    onToggled: root.encryptedManagedIdentity = checked
                  }
                  Text {
                    width: parent.width
                    text: root.encryptedManagedIdentity
                      ? "ssh-keygen and ssh-agent handle the passphrase; SSH-mixer never reads it."
                      : "Default: an unencrypted key stored in a private directory, usable only by the forced Receiver Protocol."
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }
                  RowLayout {
                    width: parent.width
                    spacing: Style.space(8)
                    ActionButton {
                      label: "Approve, install, and verify"
                      rowIndex: -1
                      onPressed: root.applyLinuxReceiver()
                      Layout.fillWidth: true
                    }
                    ActionButton {
                      label: "Cancel"
                      rowIndex: -1
                      onPressed: { root.linuxSetupPlan = null; root.message = "No Receiver changes were applied." }
                      Layout.fillWidth: true
                    }
                  }
                }
                Column {
                  visible: !!root.windowsSetupPlan
                  width: parent.width
                  spacing: Style.space(6)
                  Text {
                    width: parent.width
                    text: "Approved Windows changes only — no changes have been applied yet:"
                    color: root.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                  }
                  Repeater {
                    model: root.windowsSetupPlan && root.windowsSetupPlan.changes ? root.windowsSetupPlan.changes : []
                    Text {
                      required property var modelData
                      width: parent.width
                      text: "• " + modelData.summary + (modelData.requiresPrivilege ? " (Windows Administrator approval)" : "")
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }
                  Text {
                    visible: !!root.windowsSetupPlan && root.windowsSetupPlan.packageCommand && root.windowsSetupPlan.packageCommand.length > 0
                    width: parent.width
                    text: "Trusted package command: " + (root.windowsSetupPlan ? root.windowsSetupPlan.packageCommand.join(" ") : "")
                    color: root.dim
                    font.family: "monospace"
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WrapAnywhere
                  }
                  CheckBox {
                    visible: !!root.windowsSetupPlan && root.windowsSetupPlan.administratorConfirmationRequired
                    text: "I understand this account can administer Windows; Receiver runtime must still be non-elevated"
                    checked: root.windowsAdministratorConfirmed
                    onToggled: {
                      root.windowsAdministratorConfirmed = checked
                      root.planWindowsReceiver()
                    }
                  }
                  CheckBox {
                    text: "Encrypt the dedicated key and load it into my existing ssh-agent"
                    checked: root.encryptedManagedIdentity
                    onToggled: root.encryptedManagedIdentity = checked
                  }
                  Text {
                    width: parent.width
                    text: "Windows security prompts are never bypassed. Setup verifies OpenSSH restrictions, key ACLs, FFplay, non-elevated runtime, arbitrary-command rejection, and forwarding rejection."
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }
                  RowLayout {
                    width: parent.width
                    spacing: Style.space(8)
                    ActionButton {
                      label: "Approve, install, and verify"
                      rowIndex: -1
                      enabled: !!root.windowsSetupPlan
                        && (!root.windowsSetupPlan.administratorConfirmationRequired || root.windowsAdministratorConfirmed)
                      onPressed: root.applyWindowsReceiver()
                      Layout.fillWidth: true
                    }
                    ActionButton {
                      label: "Cancel"
                      rowIndex: -1
                      onPressed: {
                        root.windowsSetupPlan = null
                        root.windowsAdministratorConfirmed = false
                        root.message = "No Windows Receiver changes were applied."
                      }
                      Layout.fillWidth: true
                    }
                  }
                }
                Column {
                  visible: !!root.macosSetupPlan
                  width: parent.width
                  spacing: Style.space(6)
                  Text {
                    width: parent.width
                    text: "EXPERIMENTAL macOS adapter — real-device verification has not been recorded. No changes have been applied yet."
                    color: root.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                    wrapMode: Text.WordWrap
                  }
                  Text {
                    width: parent.width
                    text: root.macosSetupPlan && root.macosSetupPlan.administratorCapable
                      ? "Account capability: macOS administrator. Setup may request native approval; Receiver runtime still refuses root."
                      : "Account capability: standard user. Receiver runtime refuses root."
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }
                  Repeater {
                    model: root.macosSetupPlan && root.macosSetupPlan.changes ? root.macosSetupPlan.changes : []
                    Text {
                      required property var modelData
                      width: parent.width
                      text: "• " + modelData.summary + (modelData.requiresPrivilege ? " (native macOS security approval)" : "")
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }
                  Text {
                    visible: !!root.macosSetupPlan && root.macosSetupPlan.packageCommand && root.macosSetupPlan.packageCommand.length > 0
                    width: parent.width
                    text: "Trusted Homebrew command: " + (root.macosSetupPlan ? root.macosSetupPlan.packageCommand.join(" ") : "")
                    color: root.dim
                    font.family: "monospace"
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WrapAnywhere
                  }
                  CheckBox {
                    text: "I understand macOS support is Experimental and has no recorded real-device verification"
                    checked: root.macosExperimentalConfirmed
                    onToggled: root.macosExperimentalConfirmed = checked
                  }
                  CheckBox {
                    text: "Encrypt the dedicated key and load it into my existing ssh-agent"
                    checked: root.encryptedManagedIdentity
                    onToggled: root.encryptedManagedIdentity = checked
                  }
                  Text {
                    width: parent.width
                    text: "SSH-mixer never installs Homebrew, bypasses Gatekeeper, removes quarantine metadata, or suppresses macOS security approval."
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }
                  RowLayout {
                    width: parent.width
                    spacing: Style.space(8)
                    ActionButton {
                      label: "Approve Experimental setup"
                      rowIndex: -1
                      enabled: root.macosExperimentalConfirmed
                      onPressed: root.applyMacOsReceiver()
                      Layout.fillWidth: true
                    }
                    ActionButton {
                      label: "Cancel"
                      rowIndex: -1
                      onPressed: {
                        root.macosSetupPlan = null
                        root.macosExperimentalConfirmed = false
                        root.message = "No Experimental macOS Receiver changes were applied."
                      }
                      Layout.fillWidth: true
                    }
                  }
                }
                Text {
                  visible: !!root.pendingProfile && !!root.connection && root.connection.type === "openssh-profile"
                  width: parent.width
                  text: root.profileSummary()
                  color: root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }
                RowLayout {
                  visible: !!root.pendingProfile && !!root.connection && root.connection.type === "openssh-profile"
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: "Confirm updated profile"
                    rowIndex: -1
                    onPressed: root.saveProfile()
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Cancel"
                    rowIndex: -1
                    onPressed: { root.pendingProfile = null; root.message = "Profile was not changed." }
                    Layout.fillWidth: true
                  }
                }
                Text {
                  visible: !!root.pendingTrust && !!root.connection
                  width: parent.width
                  text: root.trustSummary()
                  color: root.pendingTrust && root.pendingTrust.status === "changed" ? root.urgent : root.foreground
                  font.family: "monospace"
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WrapAnywhere
                }
                RowLayout {
                  visible: !!root.pendingTrust && !!root.connection
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: root.pendingTrust && root.pendingTrust.status === "changed" ? "Trust replacement" : "Trust and save"
                    rowIndex: -1
                    onPressed: root.approveSetupTrust()
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Cancel"
                    rowIndex: -1
                    onPressed: { root.pendingTrust = null; root.message = "Trust was not changed." }
                    Layout.fillWidth: true
                  }
                }
                ActionButton {
                  visible: !!root.connection && root.connection.type !== "openssh-profile"
                  width: parent.width
                  label: "Review host trust"
                  rowIndex: -1
                  onPressed: root.inspectSavedTrust()
                }
                ActionButton {
                  visible: !!root.connection && root.connection.type === "openssh-profile"
                  width: parent.width
                  label: "Review OpenSSH profile"
                  rowIndex: -1
                  onPressed: root.inspectProfile(root.connection.profile)
                }
                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton { label: "Refresh"; rowIndex: 0; onPressed: root.refresh(); Layout.fillWidth: true }
                  ActionButton { label: "Test connection"; rowIndex: 1; onPressed: root.testConnection(); Layout.fillWidth: true }
                  ActionButton { label: root.activeSession ? "Stop" : "Start"; rowIndex: 2; onPressed: root.activeSession ? root.stop() : root.start(); Layout.fillWidth: true }
                }
              }

              PanelSeparator { foreground: root.foreground; width: parent.width }

              Column {
                width: parent.width
                spacing: Style.space(8)
                PanelSectionHeader { text: "VERIFIED REMOVAL"; foreground: root.foreground; fontFamily: root.fontFamily }
                Text {
                  width: parent.width
                  text: "Receiver access is revoked and verified before SSH-mixer deletes its owned key, Trust Record, matching Mix Profiles, and diagnostics. Shared Receiver helpers remain while another SSH-mixer key uses them."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                Text {
                  visible: root.removal && root.removal.pendingCount > 0
                  width: parent.width
                  text: String(root.removal.pendingCount) + " Receiver cleanup operation(s) pending — not revoked. Retry when reachable or run the platform Companion Setup directly."
                  color: root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  wrapMode: Text.WordWrap
                }
                Repeater {
                  model: root.removal && root.removal.pending ? root.removal.pending : []
                  Text {
                    required property var modelData
                    width: parent.width
                    text: "Pending · " + modelData.label + " · " + modelData.platform + " · " + modelData.code
                    color: root.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }
                }
                RowLayout {
                  visible: !root.removalPlan
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: root.removal && root.removal.pendingCount > 0 ? "Retry Connection cleanup" : "Remove Connection"
                    rowIndex: -1
                    enabled: !!root.connection || (root.removal && root.removal.pendingCount === 1)
                    onPressed: root.planRemoval(false)
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Plan full uninstall"
                    rowIndex: -1
                    onPressed: root.planRemoval(true)
                    Layout.fillWidth: true
                  }
                }
                Column {
                  visible: !!root.removalPlan
                  width: parent.width
                  spacing: Style.space(6)
                  Text {
                    width: parent.width
                    text: root.removalIsUninstall
                      ? "Full uninstall will clean these configured Receivers before invoking Omarchy plugin removal:"
                      : "Connection removal will clean this Receiver:"
                    color: root.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                    wrapMode: Text.WordWrap
                  }
                  Repeater {
                    model: root.removalPlan && root.removalPlan.receivers ? root.removalPlan.receivers : []
                    Text {
                      required property var modelData
                      width: parent.width
                      text: "• " + modelData.label + " · " + modelData.platform
                        + (modelData.experimental ? " Experimental (no real-device verification)" : "")
                        + " · " + modelData.securityLevel
                        + (modelData.status !== "configured" ? " · " + modelData.status : "")
                      color: modelData.securityLevel === "receiver-only" ? root.foreground : root.urgent
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }
                  Repeater {
                    model: root.removalPlan && root.removalPlan.changes ? root.removalPlan.changes : []
                    Text {
                      required property var modelData
                      width: parent.width
                      text: "• " + modelData
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }
                  Text {
                    width: parent.width
                    text: root.removalPlan ? root.removalPlan.abandonmentWarning : ""
                    color: root.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }
                  CheckBox {
                    text: "If cleanup cannot be verified, I understand abandonment deletes local retry credentials and does NOT revoke remote access"
                    checked: root.abandonmentConfirmed
                    onToggled: root.abandonmentConfirmed = checked
                  }
                  RowLayout {
                    width: parent.width
                    spacing: Style.space(8)
                    ActionButton {
                      label: root.removalIsUninstall ? "Approve cleanup, then uninstall" : "Approve verified removal"
                      rowIndex: -1
                      onPressed: root.applyRemoval(false)
                      Layout.fillWidth: true
                    }
                    ActionButton {
                      label: "Abandon — not revoked"
                      rowIndex: -1
                      enabled: root.abandonmentConfirmed && root.canAbandonRemoval()
                      onPressed: root.applyRemoval(true)
                      Layout.fillWidth: true
                    }
                    ActionButton {
                      label: "Cancel"
                      rowIndex: -1
                      onPressed: {
                        root.removalPlan = null
                        root.abandonmentConfirmed = false
                        root.message = "No Receiver or plugin state was removed."
                      }
                      Layout.fillWidth: true
                    }
                  }
                }
              }

              Text {
                width: parent.width
                visible: root.message !== "" || root.status.error
                text: root.status.error ? root.status.error : root.message
                color: root.status.error ? root.urgent : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }

              Column {
                width: parent.width
                spacing: Style.space(8)

                PanelSectionHeader { text: "DIAGNOSTICS & CONTRIBUTING"; foreground: root.foreground; fontFamily: root.fontFamily }

                Text {
                  width: parent.width
                  text: "Retention is always byte-bounded. Choose Minimal (1 day / 5 Sessions), Standard (7 / 20), or Extended (30 / 50). Reports remain local until you review and open GitHub."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  RetentionButton { label: "Minimal"; value: "minimal"; Layout.fillWidth: true }
                  RetentionButton { label: "Standard"; value: "standard"; Layout.fillWidth: true }
                  RetentionButton { label: "Extended"; value: "extended"; Layout.fillWidth: true }
                }

                CheckBox {
                  text: "Include redacted operational logs"
                  checked: root.includeDiagnosticLogs
                  onToggled: root.includeDiagnosticLogs = checked
                }

                TextArea {
                  id: diagnosticEditor
                  visible: root.diagnosticBody !== ""
                  width: parent.width
                  height: Style.space(150)
                  text: root.diagnosticBody
                  wrapMode: TextEdit.Wrap
                  selectByMouse: true
                  placeholderText: "The locally redacted diagnostic report will appear here for review."
                }

                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  ActionButton {
                    label: root.diagnosticBody === "" ? "Prepare report" : "Refresh report"
                    rowIndex: -1
                    onPressed: root.prepareDiagnostic()
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    visible: root.diagnosticBody !== ""
                    label: "Report on GitHub"
                    rowIndex: -1
                    onPressed: root.reportDiagnostic(diagnosticEditor.text)
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    visible: root.diagnosticBody !== ""
                    label: "Clear"
                    rowIndex: -1
                    onPressed: root.clearDiagnostics()
                    Layout.fillWidth: true
                  }
                  ActionButton {
                    label: "Contribute a fix"
                    rowIndex: -1
                    onPressed: root.contributeFix()
                    Layout.fillWidth: true
                  }
                }
              }

              Text {
                width: parent.width
                text: "Selected: " + (root.selectedLabels() || "none") + " • Destination: " + root.destination.toUpperCase()
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }
          }
        }
      }
    }
  }

  component SourceRow: CursorSurface {
    id: row
    required property var sourceData
    required property int rowIndex
    readonly property bool checked: root.sourceSelected(sourceData.id)
    hasCursor: root.cursorActive && root.focusSection === "inputs" && root.focusIndex === rowIndex
    current: checked
    foreground: root.foreground
    implicitHeight: inner.implicitHeight + Style.spacing.xl

    RowLayout {
      id: inner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        text: sourceData.type === "playback" ? "󰕾" : (sourceData.type === "monitor" ? "󰓃" : "󰍬")
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
        Layout.preferredWidth: Style.space(24)
        horizontalAlignment: Text.AlignHCenter
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        Text {
          text: sourceData.label || sourceData.id
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: row.checked
          Layout.fillWidth: true
          elide: Text.ElideRight
        }
        Text {
          text: (sourceData.categoryLabel || sourceData.type || "Source")
            + (root.recentCaptureIds.indexOf(String(sourceData.id)) >= 0 || sourceData.recentChoice ? " · recent, confirmation required" : "")
            + " • " + (sourceData.detail || sourceData.name || "")
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          Layout.fillWidth: true
          elide: Text.ElideRight
        }
      }

      ToggleSwitch {
        checked: row.checked
        hasCursor: row.hasCursor
        foreground: root.foreground
        onToggled: root.toggleSource(sourceData.id)
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) { root.cursorActive = true; root.focusSection = "inputs"; root.focusIndex = row.rowIndex }
      onClicked: root.toggleSource(row.sourceData.id)
    }
  }

  component DestinationButton: Button {
    required property string label
    required property string value
    required property int rowIndex
    text: label
    foreground: root.foreground
    fontFamily: root.fontFamily
    selected: root.destination === value
    hasCursor: root.cursorActive && root.focusSection === "destination" && root.focusIndex === rowIndex
    onHovered: function(on) { if (on) { root.cursorActive = true; root.focusSection = "destination"; root.focusIndex = rowIndex } }
    onClicked: root.chooseDestination(value)
  }

  component PrivacyButton: Button {
    required property string label
    required property string value
    text: label
    foreground: root.foreground
    fontFamily: root.fontFamily
    selected: root.privacy.lockBehavior === value
    onClicked: root.savePrivacy(value, root.privacy.showReceiverLabel === true)
  }

  component RetentionButton: Button {
    required property string label
    required property string value
    text: label
    foreground: root.foreground
    fontFamily: root.fontFamily
    selected: root.diagnosticRetention === value
    onClicked: root.configureDiagnosticRetention(value)
  }

  component ActionButton: Button {
    required property string label
    required property int rowIndex
    signal pressed()
    text: label
    foreground: root.foreground
    fontFamily: root.fontFamily
    selected: rowIndex === 2 && root.activeSession
    hasCursor: rowIndex >= 0 && root.cursorActive && root.focusSection === "actions" && root.focusIndex === rowIndex
    onHovered: function(on) { if (on && rowIndex >= 0) { root.cursorActive = true; root.focusSection = "actions"; root.focusIndex = rowIndex } }
    onClicked: pressed()
  }
}
