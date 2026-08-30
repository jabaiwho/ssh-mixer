.pragma library

function nextCommand(activeSession, sourceChoiceIds, destination, startWhenStopped) {
  const ids = sourceChoiceIds instanceof Array ? sourceChoiceIds.slice() : []
  const payload = {
    destination: String(destination || "both"),
    sourceChoiceIds: ids
  }
  if (activeSession) return { action: "stop", payload: payload }
  if (ids.length > 0 && startWhenStopped !== false)
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
