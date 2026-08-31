.pragma library

function centerX(item) {
  return Number(item.x || 0) + Number(item.width || 0) / 2
}

function centerY(item) {
  return Number(item.y || 0) + Number(item.height || 0) / 2
}

function firstIndex(items) {
  if (!(items instanceof Array) || items.length === 0) return -1
  let best = 0
  for (let index = 1; index < items.length; index++) {
    const y = centerY(items[index])
    const bestY = centerY(items[best])
    if (y < bestY || (y === bestY && centerX(items[index]) < centerX(items[best])))
      best = index
  }
  return best
}

function nextIndex(items, currentIndex, dx, dy) {
  if (!(items instanceof Array) || items.length === 0) return -1
  if (currentIndex < 0 || currentIndex >= items.length) return firstIndex(items)
  const current = items[currentIndex]
  const currentX = centerX(current)
  const currentY = centerY(current)
  let best = currentIndex
  let bestScore = Number.POSITIVE_INFINITY
  for (let index = 0; index < items.length; index++) {
    if (index === currentIndex) continue
    const candidate = items[index]
    const deltaX = centerX(candidate) - currentX
    const deltaY = centerY(candidate) - currentY
    let score = Number.POSITIVE_INFINITY
    if (dx !== 0) {
      const rowTolerance = Math.max(
        4,
        Math.min(Number(current.height || 0), Number(candidate.height || 0)) * 0.6
      )
      if (deltaX * dx > 1 && Math.abs(deltaY) <= rowTolerance)
        score = Math.abs(deltaX) * 1000 + Math.abs(deltaY)
    } else if (dy !== 0) {
      const rowThreshold = Math.max(
        2,
        Math.min(Number(current.height || 0), Number(candidate.height || 0)) * 0.4
      )
      if (deltaY * dy > rowThreshold)
        score = Math.abs(deltaY) * 1000 + Math.abs(deltaX)
    }
    if (score < bestScore) {
      best = index
      bestScore = score
    }
  }
  return best
}
