import { Badge, Button } from '../ui'

export function ErrorStrip({ message, onDismiss }) {
  return (
    <div className="flex-none flex items-center justify-between gap-3 px-4 py-2 bg-surface border-b border-danger">
      <span className="font-mono text-[11px] text-danger">ERR · {message}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss error"
        className="font-mono text-[13px] leading-none text-danger hover:opacity-60 transition-opacity"
      >
        ✕
      </button>
    </div>
  )
}

// Dest path after a live POST /api/backup. Presentational — App formats copy.
export function BackupSavedStrip({ message, onDismiss }) {
  return (
    <div className="flex-none flex items-center justify-between gap-3 px-4 py-2 bg-surface border-b border-success">
      <span className="font-mono text-[11px] text-success min-w-0 truncate">{message}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss backup path"
        className="font-mono text-[13px] leading-none text-success hover:opacity-60 transition-opacity"
      >
        ✕
      </button>
    </div>
  )
}

// Shown while a device is connected and no backup has been acknowledged for
// it. The project's own rule (README "Before your first real transfer") is
// backup-first; PROCEED records the acknowledgement in localStorage.
export function FirstRunStrip({ onBackup, onProceed, busy }) {
  return (
    <div className="flex-none flex items-center gap-[14px] px-4 py-[10px] bg-surface border-b border-warning">
      <Badge tone="warning">UNSECURED DEVICE</Badge>
      <span className="text-[13px] text-ink2 mr-auto">No backup on record for this device.</span>
      <Button variant="primary" size="sm" onClick={onBackup} disabled={busy}>
        {busy ? 'BACKING UP…' : 'BACK UP NOW'}
      </Button>
      <Button variant="ghost" size="sm" onClick={onProceed}>
        PROCEED
      </Button>
    </div>
  )
}
