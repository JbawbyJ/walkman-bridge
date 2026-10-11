import { useState } from 'react'
import { fmtBytes } from '../format.js'
import { playable, transferable, timeLabel } from '../playback.js'
import { Icon, IconButton, SectionHeading, Status } from './PlayerPanels.jsx'
import { CoverArt } from './CoverArt.jsx'
import lotus from '../assets/lotus.png'

export function DevicePanel({ device, tracks, etag, onBackup, backupBusy, busy, onDelete }) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('title')
  const connected = device?.connected
  const used = device?.total_bytes != null && device?.free_bytes != null ? device.total_bytes - device.free_bytes : null
  const percent = device?.total_bytes ? Math.max(0, Math.min(100, used / device.total_bytes * 100)) : 0
  const visible = tracks.filter(track => [track.title, track.artist, track.album].join(' ').toLowerCase().includes(search.toLowerCase())).sort((a, b) => String(a[sort] || '').localeCompare(String(b[sort] || '')))
  return <aside className="device-column"><section className="panel device-panel"><SectionHeading aside={<span className={connected ? 'connection connected' : 'connection'}><i />{connected ? 'CONNECTED' : 'DISCONNECTED'}</span>}>Walkman device</SectionHeading>
    <div className="device-summary"><div className={`device-illustration ${connected ? 'online' : ''}`} aria-hidden="true"><span>SONY</span><div className="device-screen"><img src={lotus} alt="" /><small>RED LOTUS</small></div><i className="device-wheel"><Icon name="play" size={16} /></i><b>W.</b></div><div className="device-copy"><h3>{connected ? device.model || 'NW-S705F' : 'No device connected'}</h3><p>{connected ? `${device.track_count ?? tracks.length} tracks on device` : 'Connect your Walkman via USB to view its music.'}</p><span className="micro">STORAGE</span><div className="storage-meter" role="meter" aria-label="Device storage used" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(percent)}><i style={{ width: `${percent}%` }} /></div><small>{fmtBytes(used)} / {fmtBytes(device?.total_bytes)}</small><button className="button" disabled={!connected || busy} onClick={onBackup}>{backupBusy ? 'Backing up…' : 'Back up device'}</button></div></div>
  </section><section className="panel ledger-panel"><SectionHeading aside={<span className="micro">{tracks.length} TRACKS</span>}>Device ledger</SectionHeading><div className="ledger-tools"><input aria-label="Search device tracks" placeholder="Search device…" value={search} onChange={e => setSearch(e.target.value)} /><select aria-label="Sort device tracks" value={sort} onChange={e => setSort(e.target.value)}><option value="title">Title</option><option value="artist">Artist</option><option value="album">Album</option></select></div>
    <div className="device-track-list">{visible.length ? visible.map(track => <div className="device-track" key={track.id}><Icon name="disc" size={16} /><div><strong>{track.title || 'Untitled'}</strong><small>{track.artist || 'Unknown artist'} · {track.album || 'Unknown album'}</small></div><time>{timeLabel(track.duration_seconds ?? track.durationSeconds)}</time><IconButton icon="close" label={`Delete ${track.title || 'track'} from device`} disabled={busy || !etag || !connected} onClick={() => onDelete(track)} /></div>) : <div className="empty-state small"><Icon name="usb" size={30} /><strong>{connected ? search ? 'No matching tracks' : 'No tracks on this device' : 'Your device ledger appears here.'}</strong><p>{connected ? 'Cleared music can be staged for transfer.' : 'Local playback is available while disconnected.'}</p></div>}</div><div className="panel-note">{connected ? 'Device database · refreshed automatically' : 'Waiting for USB connection'}</div></section></aside>
}

export function JobPanel({ job, pollingError }) {
  if (!job) return <div className="scan-idle"><span className="scan-seal">✓</span><div><strong>Clearance before playback.</strong><p role={pollingError ? 'status' : undefined}>{pollingError || 'Imported audio is checked by Microsoft Defender before it becomes available.'}</p></div></div>
  const progress = Number.isFinite(job.progress) ? Math.max(0, Math.min(1, job.progress)) : undefined
  const finished = ['done', 'partial', 'failed', 'interrupted'].includes(job.status)
  return <section className={`job-panel ${finished ? 'finished' : 'active-job'}`} aria-label="Current operation">
    <div className="job-title"><strong>{String(job.kind || 'Media').replaceAll('_', ' ')} · {job.phase || job.status}</strong>{progress !== undefined && <span>{Math.round(progress * 100)}%</span>}</div>
    {!finished && <progress aria-label="Operation progress" value={progress} max="1" />}
    <p role="status">{pollingError || job.message || 'Waiting for operation update'}</p>
    {job.needs_reconcile && <p className="reconcile-warning">Device state needs verification. Refresh the ledger before any retry.</p>}
    <details className="job-details" key={`${job.job_id}:${finished}`}><summary>Operation details{job.files?.length ? ` · ${job.files.length} files` : ''}</summary>
      <div className="job-files">{(job.files || []).map((file, i) => <div key={file.file_id || `${file.media_id}-${i}`}><span title={file.detail || file.scan?.reason}>{file.name || 'Unnamed file'}</span><Status state={file.state} />{file.detail && <small>{file.detail}</small>}{file.state === 'awaiting_permission' && <small>Respond to the Windows permission prompt to continue.</small>}</div>)}</div>
      {job.log_tail?.length > 0 && <details className="diagnostics"><summary>Operation log</summary><pre>{job.log_tail.join('\n')}</pre></details>}
    </details>
  </section>
}

export function StagingPanel({ items, selection, onSelection, onTransfer, onRescan, onImport, onImportLink, busy, connected }) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('added')
  const visible = items.filter(item => `${item.title || ''} ${item.name}`.toLowerCase().includes(search.toLowerCase()))
  if (sort === 'name') visible.sort((a, b) => a.name.localeCompare(b.name))
  if (sort === 'size') visible.sort((a, b) => (b.size_bytes || 0) - (a.size_bytes || 0))
  // Selection insertion order carries a playlist's explicit order into transfer.
  const selected = [...selection].map(id => items.find(item => item.id === id)).filter(item => item && transferable(item))
  const readyVisible = visible.filter(transferable)
  const allChecked = readyVisible.length > 0 && readyVisible.every(item => selection.has(item.id))
  return <aside className="panel staging-panel"><SectionHeading aside={<span className="micro">{items.length} FILES</span>}>Transfer & scan</SectionHeading><div className="staging-heading"><h3>STAGING AREA</h3><div className="import-actions"><button className="text-button" disabled={busy} onClick={onImport}>+ Add files</button><button className="text-button" disabled={busy} onClick={onImportLink}>Import link</button></div></div><div className="staging-tools"><input aria-label="Search staging area" placeholder="Search staged files…" value={search} onChange={e => setSearch(e.target.value)} /><select aria-label="Sort staged files" value={sort} onChange={e => setSort(e.target.value)}><option value="added">Added</option><option value="name">Name</option><option value="size">Size</option></select></div><label className="select-all toggle"><input type="checkbox" checked={allChecked} disabled={!readyVisible.length || busy} onChange={e => { const next = new Set(selection); readyVisible.forEach(item => e.target.checked ? next.add(item.id) : next.delete(item.id)); onSelection(next) }} />Select cleared files</label><div className="staged-files">{visible.map(item => <div className="staged-file" key={item.id}><input type="checkbox" aria-label={`Stage ${item.name} for transfer`} checked={selection.has(item.id)} disabled={!transferable(item) || busy} onChange={e => { const next = new Set(selection); e.target.checked ? next.add(item.id) : next.delete(item.id); onSelection(next) }} /><CoverArt item={item} variant="thumb" /><div><strong title={item.name}>{item.name}</strong><small>{fmtBytes(item.size_bytes)}</small><Status state={item.needs_reconcile ? 'unknown' : item.status} />{item.scan?.reason && !playable(item) && <p className="scan-reason">{item.scan.reason}</p>}</div>{!playable(item) && <button className="text-button" disabled={busy} onClick={() => onRescan(item.id)}>Rescan</button>}</div>)}{!visible.length && <div className="empty-state small"><Icon name="plus" size={26} /><strong>{search ? 'No matching files' : 'Nothing staged yet.'}</strong><p>Add music to your local queue, then select cleared files for your Walkman.</p></div>}</div><div className="transfer-footer"><div><span>{selected.length} selected</span><span>{fmtBytes(selected.reduce((total, item) => total + (item.size_bytes || 0), 0))}</span></div><button className="button primary" disabled={!connected || busy || !selected.length} onClick={() => onTransfer(selected.map(item => item.id))}>{busy ? 'Operation in progress' : `Transfer ${selected.length || ''} ${selected.length === 1 ? 'file' : 'files'}`}</button><small>{connected ? 'Walkman format · MP3 / 192 kbps / 44.1 kHz' : 'Connect a Walkman to transfer'}</small></div></aside>
}

