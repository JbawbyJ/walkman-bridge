import { Input, Select } from '../ui'
import { fmtLen } from '../format'

// 'added' is the order the device database enumerates tracks in — the shim
// does not expose timestamps, so it is labelled honestly as device order.
export const SORT_OPTIONS = [
  { value: 'added', label: 'Device order' },
  { value: 'title', label: 'Title A–Z' },
  { value: 'artist', label: 'Artist A–Z' },
  { value: 'album', label: 'Album A–Z' },
]

const COLS = 'grid-cols-[52px_minmax(160px,1.2fr)_minmax(90px,0.8fr)_minmax(90px,0.9fr)_56px_80px]'

function Row({ track, jobBusy, pending, onRemove }) {
  const disabled = jobBusy || pending
  return (
    <div
      className={`grid ${COLS} items-center px-4 py-2 border-b border-line transition-colors duration-300 ease-in-out hover:bg-sunken`}
    >
      <span className="font-mono text-[11px] text-muted">{track.id}</span>
      <span className="text-[13px] truncate">{track.title}</span>
      <span className="text-[13px] text-ink2 truncate">{track.artist}</span>
      <span className="text-[13px] text-ink2 truncate">{track.album}</span>
      <span className="font-mono text-[11px] text-muted">{fmtLen(track.duration_seconds)}</span>
      <button
        type="button"
        onClick={() => onRemove(track.id)}
        disabled={disabled}
        title={jobBusy ? 'Removal disabled while a transfer job is running' : undefined}
        className={`text-right font-display text-[9px] tracking-wider transition-colors ${
          pending
            ? 'text-muted cursor-wait'
            : jobBusy
              ? 'text-muted opacity-40 cursor-not-allowed'
              : 'text-muted hover:text-danger cursor-pointer'
        }`}
      >
        {pending ? 'REMOVING…' : 'REMOVE'}
      </button>
    </div>
  )
}

export default function Ledger({
  tracks,
  filtered,
  connected,
  jobBusy,
  pendingDeletes,
  onRemove,
  search,
  onSearch,
  sort,
  onSort,
}) {
  const n = tracks.length
  const countLabel = `${n} ${n === 1 ? 'TRACK' : 'TRACKS'}${filtered ? ' · FILTERED' : ''}`

  return (
    <div className="bg-page flex flex-col min-h-0 min-w-0">
      <div className="flex-none flex items-center gap-3 px-4 py-[10px] border-b border-line">
        <span className="font-display text-[10px] tracking-widest text-muted mr-auto">ON DEVICE · {countLabel}</span>
        <div className="w-[230px]">
          <Input placeholder="Search title, artist, album" value={search} onChange={(e) => onSearch(e.target.value)} />
        </div>
        <div className="w-[170px]">
          <Select options={SORT_OPTIONS} value={sort} onChange={(e) => onSort(e.target.value)} />
        </div>
      </div>

      <div
        className={`flex-none grid ${COLS} px-4 py-2 border-b border-line font-display text-[9px] tracking-widest text-muted`}
      >
        <span>ID</span>
        <span>TITLE</span>
        <span>ARTIST</span>
        <span>ALBUM</span>
        <span>LEN</span>
        <span className="text-right">ACTION</span>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {n === 0 ? (
          <div className="grid place-items-center px-5 py-[60px] text-center">
            <div className="flex flex-col gap-2">
              <span className="font-display text-[12px] tracking-wider text-muted">
                {connected ? (filtered ? 'NO MATCHES' : 'NO TRACKS ON DEVICE') : 'NO DEVICE LINKED'}
              </span>
              <span className="text-[13px] text-ink2">
                {connected
                  ? filtered
                    ? 'Nothing on the device matches that search.'
                    : 'Drop audio to transfer.'
                  : 'Connect the NW-S705F via USB.'}
              </span>
            </div>
          </div>
        ) : (
          tracks.map((t) => (
            <Row key={t.id} track={t} jobBusy={jobBusy} pending={pendingDeletes.has(t.id)} onRemove={onRemove} />
          ))
        )}
      </div>

      <div className="flex-none px-4 py-[10px] border-t border-line font-mono text-[10px] tracking-wider text-muted">
        127.0.0.1 · LOCAL ONLY
      </div>
    </div>
  )
}
