export const playable = item => item?.status === 'ready' && item?.scan?.ok === true
export const transferable = item => playable(item) && item.needs_reconcile !== true

export function nextTrackId(items, current, { direction = 1, repeat = 'off', shuffle = false, random = Math.random } = {}) {
  const ready = items.filter(playable)
  if (!ready.length) return null
  const index = ready.findIndex(item => item.id === current)
  if (repeat === 'one' && index >= 0) return current
  if (shuffle) {
    const candidates = ready.filter(item => item.id !== current)
    return candidates.length ? candidates[Math.min(candidates.length - 1, Math.floor(random() * candidates.length))].id : repeat === 'all' ? current : null
  }
  if (index < 0) return ready[0].id
  const next = index + direction
  if (next >= 0 && next < ready.length) return ready[next].id
  return repeat === 'all' ? ready[(next + ready.length) % ready.length].id : null
}

export function moveTrack(items, id, direction) {
  const result = [...items]
  const index = result.findIndex(item => item.id === id)
  const next = index + direction
  if (index >= 0 && next >= 0 && next < result.length) [result[index], result[next]] = [result[next], result[index]]
  return result
}

export function restoreSession(value) {
  const state = value && typeof value === 'object' ? value : {}
  return {
    id: typeof state.id === 'string' ? state.id : null,
    position: Number.isFinite(state.position) ? Math.max(0, state.position) : 0,
    volume: Number.isFinite(state.volume) ? Math.max(0, Math.min(1, state.volume)) : 0.7,
    shuffle: state.shuffle === true,
    repeat: ['off', 'all', 'one'].includes(state.repeat) ? state.repeat : 'off',
    playing: false,
  }
}

export const timeLabel = seconds => Number.isFinite(seconds) && seconds >= 0
  ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : '—:—'

export function readSession(key) {
  try { return restoreSession(JSON.parse(localStorage.getItem(key))) } catch { return restoreSession(null) }
}

export function saveSession(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* Playback remains available with private/blocked storage. */ }
}
export function processingBusy(operations) {
  if (operations.draining) return true
  if (!operations.busy) return false
  if (!Array.isArray(operations.active) || operations.active.length === 0) return true
  return operations.active.some(row => !['playback', 'playback_session'].includes(row.kind))
}
