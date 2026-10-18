// User-facing copy for device playlist failures. The backend attaches these
// exact strings on `fatal_code` (HTTP detail.fatal_code and files[].fatal_code).
// `detail.code` stays an application code such as playlist_failed and is ignored.
// A null or unknown fatal_code keeps the existing generic error.

export const PLAYLIST_FAILURES = {
  PLAYLIST_REF_MISSING: {
    title: 'Device writes are blocked',
    message: 'A playlist on this Walkman references a track that is missing. Transfers and other device writes stay blocked until that playlist no longer points at the missing track.',
  },
  PLAYLIST_JOURNAL_PENDING: {
    title: 'Playlist recovery is pending',
    message: 'An interrupted playlist transaction is still pending on this Walkman. Reads and writes stay blocked until that transaction is recovered.',
  },
  PLAYLIST_SLOTS_EXHAUSTED: {
    title: 'No playlist slots left',
    message: 'This Walkman has no playlist slots left. Remove a playlist from the device before creating another.',
  },
}

export function playlistFailureMessage(fatalCode) {
  if (typeof fatalCode !== 'string' || !Object.hasOwn(PLAYLIST_FAILURES, fatalCode)) return null
  return PLAYLIST_FAILURES[fatalCode]
}

function knownFatalCode(candidate) {
  if (playlistFailureMessage(candidate)) return candidate
  if (!candidate || typeof candidate !== 'object') return null
  if (playlistFailureMessage(candidate.fatal_code)) return candidate.fatal_code
  const nested = candidate.detail
  if (nested && typeof nested === 'object' && playlistFailureMessage(nested.fatal_code)) return nested.fatal_code
  if (Array.isArray(candidate.files)) {
    for (const file of candidate.files) {
      if (playlistFailureMessage(file?.fatal_code)) return file.fatal_code
    }
  }
  return null
}

export function resolvePlaylistCode(...candidates) {
  for (const candidate of candidates) {
    const fatalCode = knownFatalCode(candidate)
    if (fatalCode) return fatalCode
  }
  return null
}

// Device views replace the generic strip with an in-view alert. Everywhere
// else, a known fatal_code rewrites the existing notice and a null or unknown
// fatal_code leaves that notice untouched, including its alert-versus-status role.
export function surfaceError({ error = null, pollError = null, notice = null, fatalCode = null, view = 'listening' } = {}) {
  const copy = playlistFailureMessage(fatalCode)
  const generic = error || pollError || notice || null
  if (copy && (view === 'device' || view === 'transfer')) {
    return { alert: copy, strip: null, role: 'status', errorStyle: false }
  }
  if (copy) {
    return { alert: null, strip: `${copy.title}. ${copy.message}`, role: 'alert', errorStyle: true }
  }
  return {
    alert: null,
    strip: generic,
    role: error ? 'alert' : 'status',
    errorStyle: Boolean(error || pollError),
  }
}
