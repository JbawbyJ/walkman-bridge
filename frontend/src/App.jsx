import { useEffect, useRef, useState } from 'react'
import { api } from './api'

const TERMINAL_STATUSES = ['done', 'failed', 'partial']

const fmtBytes = (b) => {
  if (b == null) return '—'
  const u = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let n = b
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(1)} ${u[i]}`
}

export default function App() {
  const [device, setDevice] = useState({ connected: false })
  const [tracks, setTracks] = useState([])
  const [activeJob, setActiveJob] = useState(null)
  const [dragOver, setDragOver] = useState(false)
  const [pollError, setPollError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [pendingDeletes, setPendingDeletes] = useState(() => new Set())
  const fileInput = useRef(null)

  const jobBusy = !!activeJob && !TERMINAL_STATUSES.includes(activeJob.status)

  // Prevent the browser/webview from navigating away when a file is
  // dropped outside the dropzone
  useEffect(() => {
    const prevent = (e) => e.preventDefault()
    window.addEventListener('dragover', prevent)
    window.addEventListener('drop', prevent)
    return () => {
      window.removeEventListener('dragover', prevent)
      window.removeEventListener('drop', prevent)
    }
  }, [])

  // Poll device + tracks every 3s
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const d = await api.device()
        if (!alive) return
        setDevice(d)
        if (d.connected) {
          const t = await api.tracks().catch(() => [])
          if (alive) setTracks(t)
        } else {
          setTracks([])
        }
        if (alive) setPollError(null)
      } catch (e) {
        if (alive) setPollError(e.message)
      }
    }
    tick()
    const id = setInterval(tick, 3000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // Poll active job
  useEffect(() => {
    if (!activeJob || TERMINAL_STATUSES.includes(activeJob.status)) return
    let failures = 0
    const id = setInterval(async () => {
      try {
        const j = await api.job(activeJob.job_id)
        failures = 0
        setActiveJob(j)
      } catch (e) {
        failures += 1
        if (e.message.startsWith('404') || failures >= 5) {
          setActiveJob((prev) =>
            prev ? { ...prev, status: 'failed', message: 'Lost contact with job' } : prev
          )
        }
      }
    }, 1000)
    return () => clearInterval(id)
  }, [activeJob])

  const handleFiles = async (files) => {
    if (!files || files.length === 0) return
    if (jobBusy) return
    if (!device.connected) {
      setActionError('No Walkman detected')
      return
    }
    try {
      const { job_id } = await api.upload(Array.from(files))
      setActiveJob({ job_id, status: 'pending', progress: 0, message: 'Queued', log_tail: [] })
    } catch (e) {
      setActionError(e.message)
    }
  }

  const onDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    if (jobBusy) return
    handleFiles(e.dataTransfer.files)
  }

  const onRemove = async (id) => {
    if (jobBusy || pendingDeletes.has(id)) return
    setPendingDeletes((prev) => {
      const next = new Set(prev)
      next.add(id)
      return next
    })
    try {
      await api.deleteTrack(id)
      setTracks((t) => t.filter((x) => x.id !== id))
    } catch (e) {
      setActionError(e.message)
    } finally {
      setPendingDeletes((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  const usagePct = device.total_bytes
    ? ((device.total_bytes - device.free_bytes) / device.total_bytes) * 100
    : 0

  return (
    <div className="min-h-screen px-6 py-8 md:px-12 md:py-12">
      {/* Header */}
      <header className="flex items-end justify-between border-b border-line pb-6 mb-10">
        <div>
          <div className="font-display text-[10px] tracking-[0.3em] text-muted">
            UTILITY · v0.1 · LOCAL
          </div>
          <h1 className="font-display text-3xl md:text-5xl font-bold tracking-tight mt-2">
            WALKMAN<span className="text-amber">·</span>BRIDGE
          </h1>
        </div>
        <div className="hidden md:flex flex-col items-end font-display text-[10px] tracking-[0.2em] text-muted">
          <div>NW-S705F · TRANSFER UTILITY</div>
          <div>VIA JSYMPHONIC</div>
        </div>
      </header>

      {/* Error strips */}
      {pollError && (
        <div className="mb-6 border border-crimson/40 bg-crimson/5 px-4 py-3 font-display text-xs text-crimson">
          ERR · {pollError}
        </div>
      )}
      {actionError && (
        <div className="mb-6 flex items-center justify-between gap-4 border border-crimson/40 bg-crimson/5 px-4 py-3 font-display text-xs text-crimson">
          <span>ERR · {actionError}</span>
          <button
            onClick={() => setActionError(null)}
            aria-label="Dismiss error"
            className="font-display text-sm leading-none text-crimson hover:opacity-60 transition-opacity"
          >
            ×
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Device panel */}
        <section className="lg:col-span-1 border border-line bg-panel p-6">
          <div className="font-display text-[10px] tracking-[0.3em] text-muted mb-4">
            DEVICE
          </div>
          <div className="flex items-center gap-3 mb-6">
            <span
              className={`pulse-dot inline-block w-2 h-2 rounded-full ${
                device.connected ? 'bg-cyan' : 'bg-muted'
              }`}
            />
            <span className="font-display text-sm">
              {device.connected ? 'CONNECTED' : 'NO DEVICE'}
            </span>
          </div>

          {device.connected ? (
            <dl className="space-y-3 font-display text-xs">
              <Row k="MOUNT" v={device.mount_path} />
              <Row k="TRACKS" v={device.track_count?.toString() ?? '—'} />
              <Row k="FREE" v={fmtBytes(device.free_bytes)} />
              <Row k="TOTAL" v={fmtBytes(device.total_bytes)} />
              <div className="pt-2">
                <div className="h-1 bg-line relative overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 bg-amber"
                    style={{ width: `${usagePct}%` }}
                  />
                </div>
                <div className="mt-1 text-[10px] tracking-[0.2em] text-muted">
                  {usagePct.toFixed(1)}% USED
                </div>
              </div>
            </dl>
          ) : (
            <div className="font-body text-sm text-muted leading-relaxed">
              Plug in your NW-S705F. It will mount as a USB drive and appear
              here within a few seconds.
            </div>
          )}
        </section>

        {/* Dropzone + Job */}
        <section className="lg:col-span-2 space-y-6">
          <div
            onDragOver={(e) => { e.preventDefault(); if (!jobBusy) setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => { if (!jobBusy) fileInput.current?.click() }}
            className={`border-2 border-dashed p-12 text-center cursor-pointer transition-colors ${
              dragOver
                ? 'border-amber bg-amber/5 drop-active'
                : 'border-line hover:border-muted bg-panel'
            } ${!device.connected || jobBusy ? 'opacity-40 pointer-events-none' : ''}`}
          >
            <input
              ref={fileInput}
              type="file"
              multiple
              accept="audio/*,.flac,.m4a,.ogg,.wav,.aac,.opus"
              className="hidden"
              disabled={jobBusy}
              onChange={(e) => {
                handleFiles(Array.from(e.target.files))
                e.target.value = ''
              }}
            />
            <div className="font-display text-[10px] tracking-[0.3em] text-muted mb-3">
              DROP AUDIO HERE
            </div>
            <div className="font-display text-2xl mb-2">+ TRANSFER</div>
            <div className="font-body text-xs text-muted">
              FLAC · M4A · OGG · WAV · MP3 → normalized to MP3 192k 44.1kHz
            </div>
          </div>

          {jobBusy && (
            <div className="font-display text-[10px] tracking-[0.2em] text-amber">
              TRANSFER IN PROGRESS · NEW UPLOADS DISABLED
            </div>
          )}

          {/* Active job */}
          {activeJob && (
            <div className="border border-line bg-panel p-6">
              <div className="flex items-center justify-between mb-4">
                <div className="font-display text-[10px] tracking-[0.3em] text-muted">
                  JOB · {activeJob.job_id.slice(0, 8)}
                </div>
                <div
                  className={`font-display text-[10px] tracking-[0.3em] ${
                    activeJob.status === 'done'
                      ? 'text-cyan'
                      : activeJob.status === 'failed'
                      ? 'text-crimson'
                      : 'text-amber'
                  }`}
                >
                  {activeJob.status?.toUpperCase()}
                </div>
              </div>
              <div className="h-1 bg-line relative overflow-hidden mb-3">
                <div
                  className="absolute inset-y-0 left-0 bg-amber transition-all"
                  style={{ width: `${(activeJob.progress || 0) * 100}%` }}
                />
              </div>
              <div className="font-body text-sm mb-4">{activeJob.message}</div>
              <pre className="font-display text-[11px] text-muted bg-bg p-3 max-h-40 overflow-auto border border-line whitespace-pre-wrap">
                {(activeJob.log_tail || []).join('\n') || '—'}
              </pre>
            </div>
          )}
        </section>
      </div>

      {/* Track list */}
      <section className="mt-10 border border-line bg-panel">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div className="font-display text-[10px] tracking-[0.3em] text-muted">
            ON DEVICE · {tracks.length} TRACK{tracks.length === 1 ? '' : 'S'}
          </div>
        </div>
        {tracks.length === 0 ? (
          <div className="px-6 py-12 text-center font-body text-sm text-muted">
            {device.connected ? 'No tracks. Drop audio above to transfer.' : 'Connect device to view tracks.'}
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {tracks.map((t) => (
              <li
                key={t.id}
                className="grid grid-cols-12 gap-4 px-6 py-3 items-center hover:bg-line/30 transition-colors group"
              >
                <span className="col-span-1 font-display text-[10px] text-muted">
                  {t.id}
                </span>
                <span className="col-span-5 font-body text-sm truncate">{t.title}</span>
                <span className="col-span-3 font-body text-sm text-muted truncate">{t.artist}</span>
                <span className="col-span-2 font-body text-sm text-muted truncate">{t.album}</span>
                <button
                  onClick={() => onRemove(t.id)}
                  disabled={jobBusy || pendingDeletes.has(t.id)}
                  title={jobBusy ? 'Removal disabled while a transfer job is running' : undefined}
                  className={`col-span-1 font-display text-[10px] tracking-[0.2em] text-right transition-opacity ${
                    pendingDeletes.has(t.id)
                      ? 'text-muted opacity-60 cursor-wait'
                      : jobBusy
                      ? 'text-muted opacity-0 group-hover:opacity-40 cursor-not-allowed'
                      : 'text-muted opacity-0 group-hover:opacity-100 hover:text-crimson'
                  }`}
                >
                  {pendingDeletes.has(t.id) ? 'REMOVING…' : 'REMOVE'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <footer className="mt-10 font-display text-[10px] tracking-[0.3em] text-muted text-center">
        BIND 127.0.0.1 · NO TELEMETRY · LOCAL PROCESS ONLY
      </footer>
    </div>
  )
}

function Row({ k, v }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-muted tracking-[0.2em] text-[10px]">{k}</dt>
      <dd className="text-ink truncate text-right">{v}</dd>
    </div>
  )
}
