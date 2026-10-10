// Pure helpers shared by App.jsx and the region components. No React here.

export const TERMINAL_STATUSES = ['done', 'failed', 'partial']

export const isTerminal = (job) => !!job && TERMINAL_STATUSES.includes(job.status)

// Badge tone per job status (Night Ops design).
export const JOB_TONE = {
  done: 'success',
  failed: 'danger',
  partial: 'warning',
  running: 'brand',
  pending: 'neutral',
}

export function fmtBytes(b) {
  if (b == null) return '—'
  const u = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let n = b
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024
    i++
  }
  return `${n.toFixed(1)} ${u[i]}`
}

export function fmtLen(s) {
  if (s == null) return '—'
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

// Job log lines look like "[18:26:23] Transcoding 04 Corrupt.flac" and, when
// ffmpeg rejects the file, "[18:26:23]   ! transcode failed: …" right under it
// (backend/main.py process_upload_job). Strip the timestamp prefix.
const bare = (line) => line.replace(/^\[\d\d:\d\d:\d\d\] /, '')

// Names of files whose transcode failure is visible in this log tail. The
// tail is only the last 20 lines and the database-update step alone writes
// ~19, so App.jsx merges the result of every poll into one Set instead of
// trusting the final tail.
export function transcodeFailures(logTail) {
  const out = []
  const lines = (logTail || []).map(bare)
  lines.forEach((line, i) => {
    if (line.startsWith('Transcoding ') && (lines[i + 1] || '').includes('! transcode failed')) {
      out.push(line.slice('Transcoding '.length))
    }
    // process_upload_job: "  ! THREAT song.mp3: MZ executable — blocked, not transferred"
    const threat = line.match(/^! THREAT (.+?): /)
    if (threat) out.push(threat[1])
  })
  return out
}

// Per-file stage badge for the staging queue, derived from what the backend
// actually reports:
//   phase 1 (progress < 0.5): message "Transcoding <name>", one log line per file
//   phase 2 (progress ≥ 0.5): one batched shim run; message "Transferring <Artist - Title>"
// `failed` is the Set App.jsx accumulates with transcodeFailures().
export function stageFor(name, job, failed) {
  if (!job) return { stage: 'STAGED', tone: 'neutral' }
  if (failed && failed.has(name)) return { stage: 'FAILED', tone: 'danger' }

  if (job.status === 'done' || job.status === 'partial') return { stage: 'ON DEVICE', tone: 'success' }
  if (job.status === 'failed') return { stage: 'ABORTED', tone: 'danger' }

  const msg = job.message || ''
  if ((job.progress || 0) >= 0.5) {
    const stem = name.replace(/\.[^.]+$/, '')
    return msg.includes(stem) ? { stage: 'TRANSFER', tone: 'brand' } : { stage: 'IN BATCH', tone: 'neutral' }
  }
  if (msg === `Scanning ${name}`) return { stage: 'SCANNING', tone: 'warning' }
  if (msg === `Transcoding ${name}`) return { stage: 'TRANSCODE', tone: 'brand' }
  const seen = (job.log_tail || []).some((line) => bare(line) === `Transcoding ${name}`)
  if (seen) return { stage: 'ENCODED', tone: 'neutral' }
  return { stage: 'QUEUED', tone: 'neutral' }
}
