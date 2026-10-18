import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function DeviceRecoveryPanel({ connected, busy = false, onBusy = () => {}, onChanged = async () => {} }) {
  const [snapshot, setSnapshot] = useState(null), [journal, setJournal] = useState(null)
  const [confirmed, setConfirmed] = useState(false), [selected, setSelected] = useState('')
  const [running, setRunning] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('')
  const clear = () => { setSnapshot(null); setJournal(null); setConfirmed(false); setSelected('') }
  useEffect(() => { if (!connected) clear() }, [connected])
  const run = async task => {
    if (running || busy || !connected) return
    setRunning(true); onBusy(true); setError(''); setMessage('')
    try { await task() } catch (e) { clear(); setError(e.message) }
    finally { setRunning(false); onBusy(false) }
  }
  const inspect = () => run(async () => {
    clear()
    const inspected = await api.inspectRecoveryJournal()
    const state = await api.inspectDeviceState()
    setJournal(inspected); setSnapshot(state); setSelected(state.jobs?.[0]?.job_id || '')
  })
  const recover = action => run(async () => {
    if (!confirmed || !journal?.etag || !journal.guarded) return
    const token = journal.etag
    clear()
    const result = await (action === 'recover' ? api.recoverPlaylistJournal(token) : api.repairPlaylists(token))
    setMessage(action === 'recover' ? `Recovery finished: ${String(result.outcome).replaceAll('_', ' ')}. Inspect again before acknowledging earlier operations.`
      : `Repair removed ${result.pruned_count} missing references. Inspect again before acknowledging earlier operations.`)
    await onChanged(result)
  })
  const acknowledge = () => run(async () => {
    const job = snapshot?.jobs?.find(row => row.job_id === selected)
    if (!confirmed || !snapshot?.can_acknowledge || !job?.same_device || !snapshot.etag) return
    const token = snapshot.etag, jobId = job.job_id
    clear()
    const result = await api.acknowledgeDeviceState(jobId, token)
    setMessage('Device state acknowledged. Original outcomes remain in operation history. No automatic retry.')
    await onChanged(result)
  })
  const pending = ['committed', 'uncommitted'].includes(journal?.state)
  const repairable = snapshot?.inventory_error?.fatal_code === 'PLAYLIST_REF_MISSING'
  const chosen = snapshot?.jobs?.find(job => job.job_id === selected)
  const disabled = busy || running || !connected
  return <section className="panel recovery-panel" aria-label="Device recovery" aria-busy={running || undefined}>
    <h2>Device recovery</h2>
    <p>After an interrupted write, inspect this Walkman and compare its tracks and playlists before allowing another attempt.</p>
    <button type="button" className="button" disabled={disabled} onClick={inspect}>Inspect recovery state</button>
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {snapshot && <>
      <p role="status">{pending ? 'An interrupted device write needs recovery.' : 'No pending device journal.'}</p>
      {snapshot.inventory_error && <p role="alert">{snapshot.inventory_error.message}</p>}
      {!!snapshot.warnings?.length && <p role="alert">Device warnings require resolution before acknowledgement: {snapshot.warnings.map(row => row.code).join(', ')}</p>}
      {pending && <details><summary>Files covered by recovery</summary><ul>{(journal.covers?.files || []).map(name => <li key={name}>{name}</li>)}</ul></details>}
      {!pending && !snapshot.inventory_error && <details open><summary>Inspected library: {snapshot.tracks.length} tracks · {snapshot.playlists.length} playlists</summary>
        <ul>{snapshot.tracks.map(row => <li key={row.id}>Track {row.id}: {row.title || 'Untitled'} — {row.artist || 'Unknown artist'}</li>)}</ul>
        <ul>{snapshot.playlists.map(row => <li key={row.id}>Playlist {row.id}: {row.name} ({row.track_ids?.length || 0} tracks)</li>)}</ul>
      </details>}
      {(pending || repairable) ? <>
        {!journal?.guarded && <p role="alert">This engine cannot bind recovery confirmation to the inspected data. Update the packaged engine before recovery.</p>}
        <p>{pending ? 'Recovery restores or finishes the interrupted transaction according to its journal.' : 'Repair removes playlist references to missing tracks.'} Back up the Walkman first. This action writes to the device.</p>
        <label className="toggle"><input type="checkbox" checked={confirmed} disabled={disabled} onChange={e => setConfirmed(e.target.checked)} />I reviewed the recovery state and want to proceed.</label>
        <button type="button" className="button" disabled={disabled || !confirmed || !journal?.etag || !journal.guarded} onClick={() => recover(pending ? 'recover' : 'repair')}>{pending ? 'Recover interrupted write' : 'Repair missing references'}</button>
      </> : !!snapshot.jobs?.length && <>
        <label>Interrupted operation <select aria-label="Interrupted operation" value={selected} disabled={disabled} onChange={e => { setSelected(e.target.value); setConfirmed(false) }}>
          {snapshot.jobs.map(job => <option key={job.job_id} value={job.job_id}>{job.kind || 'Operation'} — {job.files?.[0]?.name || job.job_id}</option>)}
        </select></label>
        <ul>{(chosen?.files || []).map((row, index) => <li key={row.file_id || index}>{row.name}: {row.state}</li>)}</ul>
        {!chosen?.same_device && <p role="status">This operation belongs to a different or unidentified device. It stays blocked.</p>}
        <label className="toggle"><input type="checkbox" checked={confirmed} disabled={disabled || !snapshot.can_acknowledge || !chosen?.same_device} onChange={e => setConfirmed(e.target.checked)} />I compared the inspected library with this operation and understand a manual retry may duplicate tracks.</label>
        <button type="button" className="button" disabled={disabled || !confirmed || !snapshot.can_acknowledge || !chosen?.same_device || !snapshot.etag} onClick={acknowledge}>Acknowledge inspected state</button>
      </>}
    </>}
  </section>
}
