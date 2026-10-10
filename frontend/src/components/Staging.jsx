import { useEffect, useRef } from 'react'
import { Badge, Button, Switch } from '../ui'
import { JOB_TONE, stageFor } from '../format'

function Panel({ title, badge, children }) {
  return (
    <div className="flex flex-col border border-line bg-surface">
      <div className="flex justify-between items-center px-[14px] py-[9px] border-b border-line">
        <span className="font-display text-[9px] tracking-widest text-muted">{title}</span>
        {badge}
      </div>
      {children}
    </div>
  )
}

function Dropzone({ connected, jobBusy, dragOver, onDragOver, onDragLeave, onDrop, onBrowse }) {
  return (
    <div
      onClick={onBrowse}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className="p-5 text-center cursor-pointer transition-[border-color] duration-300 ease-in-out"
      style={{
        border: `1px dashed ${dragOver ? 'var(--color-border-brand)' : 'var(--color-border-strong)'}`,
        opacity: jobBusy || !connected ? 0.45 : 1,
      }}
    >
      <div className="font-display text-[10px] tracking-widest text-muted mb-[6px]">STAGE AUDIO</div>
      <div className="font-display text-[16px] font-bold text-brand-text">
        {jobBusy ? 'TRANSFER IN PROGRESS' : '+ DEPLOY TO DEVICE'}
      </div>
      <div className="font-mono text-[10px] text-muted mt-[6px]">FLAC · M4A · OGG · WAV · MP3</div>
    </div>
  )
}

function QueuePanel({ job, staged, failedFiles, onBrowse }) {
  const logRef = useRef(null)
  const log = job && job.log_tail && job.log_tail.length ? job.log_tail.join('\n') : null

  // Keep the newest log line in view while a job streams.
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [log])

  const showRetry = !!job && (job.status === 'failed' || job.status === 'partial')

  return (
    <Panel
      title={job ? `QUEUE · ${job.job_id.slice(0, 8).toUpperCase()}` : 'QUEUE'}
      badge={job && <Badge tone={JOB_TONE[job.status] || 'neutral'}>{job.status.toUpperCase()}</Badge>}
    >
      {staged.length === 0 && <div className="px-[14px] py-[14px] text-[12px] text-ink2">Nothing staged.</div>}
      {staged.map((f, i) => {
        const { stage, tone } = stageFor(f.name, job, failedFiles)
        return (
          <div
            key={`${f.name}-${i}`}
            className="flex justify-between items-center gap-[10px] px-[14px] py-2 border-b border-line"
          >
            <span className="font-mono text-[11px] truncate">{f.name}</span>
            <span className="font-mono text-[10px] text-muted flex-none">{f.size}</span>
            <Badge tone={tone}>{stage}</Badge>
          </div>
        )
      })}
      {job && job.message && (
        <div className="px-[14px] py-2 border-b border-line text-[12px] text-ink2">{job.message}</div>
      )}
      {log && (
        <div
          ref={logRef}
          className="px-[14px] py-2 font-mono text-[10px] leading-[1.6] text-ink2 bg-sunken whitespace-pre-wrap max-h-[130px] overflow-y-auto"
        >
          {log}
        </div>
      )}
      {showRetry && (
        <div className="px-[14px] py-[10px]">
          <Button variant="secondary" size="sm" onClick={onBrowse} style={{ width: '100%' }}>
            STAGE FAILED FILES AGAIN
          </Button>
        </div>
      )}
    </Panel>
  )
}

function ScanPanel({ scan }) {
  return (
    <Panel title="SCAN PROCESSES" badge={<Badge tone={scan.tone}>{scan.badge}</Badge>}>
      {scan.offline && (
        <div className="px-[14px] py-3 flex flex-col gap-[6px]">
          <span className="font-display text-[11px] tracking-wider text-muted">SCAN SERVICE OFFLINE</span>
          <span className="font-mono text-[10px] text-muted">{scan.endpoint}</span>
        </div>
      )}
      {scan.rows.map((p) => (
        <div
          key={`${p.pid}-${p.target}`}
          className="grid grid-cols-[44px_1fr_auto] gap-2 items-center px-[14px] py-2 border-b border-line font-mono text-[10px]"
        >
          <span className="text-muted">{p.pid}</span>
          <span className="truncate">
            {p.proc} · {p.target}
          </span>
          <span style={{ color: p.color }}>{p.state}</span>
        </div>
      ))}
      {scan.footer && <div className="px-[14px] py-2 font-mono text-[10px] text-ink2">{scan.footer}</div>}
    </Panel>
  )
}

function Fact({ label, value }) {
  return (
    <div className="flex flex-col gap-[6px]">
      <span className="text-xs font-medium text-ink2 tracking-wide uppercase">{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  )
}

// Bitrate/container stay facts (192k CBR, no ATRAC). Scan-before-transfer is
// live: default ON, honors the Night Ops switch, and POSTs with the upload.
function TransferProfile({ onBackup, backupBusy, scanOnline, scanBeforeTransfer, onScanBeforeTransfer }) {
  const scanLabel = scanOnline
    ? (scanBeforeTransfer ? 'Scan before transfer' : 'Scan before transfer · skipped')
    : 'Scan before transfer · no scan service'
  return (
    <div className="flex flex-col gap-[10px] border border-line bg-surface p-[14px]">
      <span className="font-display text-[9px] tracking-widest text-muted">TRANSFER PROFILE</span>
      <Fact label="Bitrate" value="192 kbps CBR · device default" />
      <Fact label="Container" value="MP3 · 44.1 kHz · stereo · ID3v2.3" />
      <Switch
        checked={!!scanBeforeTransfer}
        disabled={!scanOnline}
        onChange={onScanBeforeTransfer}
        label={scanLabel}
      />
      <Button variant="secondary" size="sm" onClick={onBackup} disabled={backupBusy} style={{ width: '100%' }}>
        {backupBusy ? 'BACKING UP…' : 'BACK UP DEVICE'}
      </Button>
    </div>
  )
}

export default function Staging({
  connected,
  jobBusy,
  dragOver,
  onDragOver,
  onDragLeave,
  onDrop,
  onBrowse,
  fileInputRef,
  onFilesPicked,
  job,
  staged,
  failedFiles,
  scan,
  scanBeforeTransfer,
  onScanBeforeTransfer,
  onBackup,
  backupBusy,
}) {
  return (
    <div className="bg-page flex flex-col gap-3 p-[14px] overflow-y-auto min-h-0">
      <Dropzone
        connected={connected}
        jobBusy={jobBusy}
        dragOver={dragOver}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onBrowse={onBrowse}
      />
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="audio/*,.flac,.m4a,.ogg,.wav,.aac,.opus,.mp3"
        className="hidden"
        disabled={jobBusy}
        onChange={onFilesPicked}
      />
      <QueuePanel job={job} staged={staged} failedFiles={failedFiles} onBrowse={onBrowse} />
      <ScanPanel scan={scan} />
      <TransferProfile
        onBackup={onBackup}
        backupBusy={backupBusy}
        scanOnline={!scan.offline}
        scanBeforeTransfer={scanBeforeTransfer}
        onScanBeforeTransfer={onScanBeforeTransfer}
      />
    </div>
  )
}
