.pragma library

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value))
}

function sectionTargetY(currentY, viewportHeight, contentHeight, headerTop, bodyBottom, margin, revealPanelTop) {
  if (revealPanelTop === true) return 0
  const safeMargin = Math.max(0, margin)
  const maximumY = Math.max(0, contentHeight - viewportHeight)
  const visibleTop = currentY + safeMargin
  const visibleBottom = currentY + viewportHeight - safeMargin

  if (headerTop >= visibleTop && bodyBottom <= visibleBottom)
    return clamp(currentY, 0, maximumY)

  return clamp(headerTop - safeMargin, 0, maximumY)
}
