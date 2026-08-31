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

function verticalIndex(items, currentIndex, dy) {
  const current = items[currentIndex]
  const currentY = centerY(current)
  let nearestDistance = Number.POSITIVE_INFINITY
  const candidates = []
  for (let index = 0; index < items.length; index++) {
    if (index === currentIndex) continue
    const candidate = items[index]
    const deltaY = centerY(candidate) - currentY
    const rowThreshold = Math.max(
      2,
      Math.min(Number(current.height || 0), Number(candidate.height || 0)) * 0.4
    )
    if (deltaY * dy <= rowThreshold) continue
    const distance = Math.abs(deltaY)
    candidates.push({ index: index, distance: distance })
    nearestDistance = Math.min(nearestDistance, distance)
  }

  let best = currentIndex
  let bestX = Number.POSITIVE_INFINITY
  let bestDistance = Number.POSITIVE_INFINITY
  for (let i = 0; i < candidates.length; i++) {
    const entry = candidates[i]
    const candidate = items[entry.index]
    const rowTolerance = Math.max(
      4,
      Math.min(Number(current.height || 0), Number(candidate.height || 0)) * 0.6
    )
    if (entry.distance > nearestDistance + rowTolerance) continue
    const x = Number(candidate.x || 0)
    if (x < bestX || (x === bestX && entry.distance < bestDistance)) {
      best = entry.index
      bestX = x
      bestDistance = entry.distance
    }
  }
  return best
}

function nextIndex(items, currentIndex, dx, dy) {
  if (!(items instanceof Array) || items.length === 0) return -1
  if (currentIndex < 0 || currentIndex >= items.length) return firstIndex(items)
  if (dy !== 0) return verticalIndex(items, currentIndex, dy)
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
    }
    if (score < bestScore) {
      best = index
      bestScore = score
    }
  }
  return best
}
