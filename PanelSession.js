.pragma library

function nextCommand(activeSession, sourceChoiceIds, destination, startWhenStopped, reuseConfiguredSelection) {
  const ids = sourceChoiceIds instanceof Array ? sourceChoiceIds.slice() : []
  const payload = { destination: String(destination || "both") }
  if (reuseConfiguredSelection !== true) payload.sourceChoiceIds = ids
  if (activeSession) return { action: "stop", payload: payload }
  if (startWhenStopped !== false && (reuseConfiguredSelection === true || ids.length > 0))
    return { action: "start", payload: payload }
  return { action: "selectionSave", payload: payload }
}

function requiresCaptureConfirmation(hasCapture, captureConfirmed) {
  return hasCapture === true && captureConfirmed !== true
}

function numberedSection(number, sections) {
  const index = Number(number) - 1
  if (!(sections instanceof Array) || index < 0 || index >= sections.length)
    return ""
  return String(sections[index] || "")
}
