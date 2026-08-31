.pragma library

function revealDecision(revealedError, currentError, panelOpened) {
  const previous = String(revealedError || "")
  const current = String(currentError || "")
  return {
    revealedError: current,
    scrollTop: panelOpened === true && current !== "" && current !== previous
  }
}
