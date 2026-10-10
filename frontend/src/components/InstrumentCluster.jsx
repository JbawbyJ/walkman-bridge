import { Badge } from '../ui'
import { fmtBytes, JOB_TONE } from '../format'

function Cell({ label, children }) {
  return (
    <div className="bg-surface px-4 py-[14px] flex flex-col gap-[7px] min-w-0">
      <span className="font-display text-[9px] tracking-widest text-muted">{label}</span>
      {children}
    </div>
  )
}

function Bar({ pct, color }) {
  return (
    <div className="h-1 bg-sunken relative">
      <div
        className="absolute inset-y-0 left-0 transition-[width] duration-300 ease-in-out"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  )
}

const JOB_BAR = {
  partial: 'var(--color-warning)',
  failed: 'var(--color-danger)',
}

export default function InstrumentCluster({ device, bridgeOffline, host, scan, job }) {
  const connected = !!device.connected
  const used = connected && device.total_bytes != null ? device.total_bytes - device.free_bytes : null
  const usagePct = connected && device.total_bytes ? (used / device.total_bytes) * 100 : 0
  const pct = job ? Math.round((job.progress || 0) * 100) : 0

  return (
    <div className="flex-none grid grid-cols-4 gap-px bg-line border-b border-line">
      <Cell label="DEVICE">
        <div className="flex items-center gap-2">
          <span className={`rl-pulse w-2 h-2 flex-none ${connected ? 'bg-success' : 'bg-idle'}`} />
          <span className="font-display text-[13px] font-semibold truncate">
            {connected ? 'NW-S705F' : bridgeOffline ? 'BRIDGE OFFLINE' : 'NO DEVICE'}
          </span>
        </div>
        <span className="font-mono text-[10px] text-ink2 truncate">
          {connected
            ? `${device.mount_path || '—'} · ${device.track_count ?? '—'} TRACKS`
            : bridgeOffline
              ? `${host} down`
              : 'Connect via USB'}
        </span>
      </Cell>

      <Cell label="STORAGE">
        <span className="font-mono text-[13px]">
          {connected ? `${fmtBytes(used)} / ${fmtBytes(device.total_bytes)}` : '— / —'}
        </span>
        <Bar pct={usagePct} color="var(--color-brand)" />
      </Cell>

      <Cell label="INTEGRITY SCAN">
        <div className="flex items-center gap-2">
          <span className="font-display text-[13px] font-semibold">{scan.headline}</span>
          <Badge tone={scan.tone}>{scan.badge}</Badge>
        </div>
        <span className="font-mono text-[10px] text-ink2">{scan.line}</span>
      </Cell>

      <Cell label="ACTIVE JOB">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[13px]">
            {job ? `${job.job_id.slice(0, 8).toUpperCase()} · ${pct}%` : 'IDLE'}
          </span>
          {job && <Badge tone={JOB_TONE[job.status] || 'neutral'}>{job.status.toUpperCase()}</Badge>}
        </div>
        <Bar pct={pct} color={(job && JOB_BAR[job.status]) || 'var(--color-brand)'} />
      </Cell>
    </div>
  )
}
