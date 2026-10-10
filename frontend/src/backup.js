// Maps POST /api/backup onto Night Ops strip copy. No React here.
// 404/405 from a build without the route → hand-copy fallback.
// Live API 404 "No Walkman detected" is a real failure, not that fallback.

export const BACKUP_FALLBACK =
  'Backup API missing in this build — copy the whole Walkman drive to your PC by hand before the first transfer (see README).'

export function backupSavedMessage(path) {
  return `BACKUP SAVED · ${path}`
}

export function backupSuccessPath(body) {
  if (!body || typeof body.path !== 'string' || !body.path) return null
  return body.path
}

export function backupFailureMessage(err) {
  const status = err && err.status
  const message = (err && err.message) || 'unknown error'
  if (status === 404 && /No Walkman detected/i.test(message)) {
    return `Backup failed: ${message}`
  }
  if (status === 404 || status === 405) return BACKUP_FALLBACK
  return `Backup failed: ${message}`
}
