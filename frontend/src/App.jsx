import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { backupFailureMessage, backupSavedMessage, backupSuccessPath } from './backup.js'
import { moveTrack, playable, transferable, timeLabel, processingBusy } from './playback.js'
import { resolvePlaylistCode, surfaceError } from './playlistFailure.js'
import { usePlayer } from './usePlayer.js'
import { Brand, Equalizer, Icon, IconButton, ListeningView, NowPlaying, Queue, spokenTime, Transport, Volume } from './components/PlayerPanels.jsx'
import { DevicePanel, JobPanel, StagingPanel } from './components/BridgePanels.jsx'
import LinkImportDialog from './components/LinkImportDialog.jsx'
import MusicManager from './components/MusicManager.jsx'

const terminal = job => ['done', 'partial', 'failed', 'interrupted'].includes(job?.status)
const readPreference = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback } }
const storePreference = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* private storage */ } }

export default function App() {
  const [health, setHealth] = useState(null)
  const [session, setSession] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    let alive = true, timer
    const connect = async () => {
      try { const [result, saved] = await Promise.all([api.health(), api.playbackState()]); if (alive) { setHealth(result); setSession(saved); setError(null) } }
      catch (e) { if (alive) { setError(e.message); timer = setTimeout(connect, 3000) } }
    }
    connect()
    return () => { alive = false; clearTimeout(timer) }
  }, [])
  const product = window.walkmanBridge?.product || health?.product
  useEffect(() => { document.title = product === 'player' ? 'Red Lotus Player' : 'Walkman Bridge' }, [product])
  if (!health || !['bridge', 'player'].includes(product)) return <div className="startup"><Brand product={product || 'bridge'} /><div><Icon name="disc" size={42} /><h1>Waking the audio system.</h1><p role="status">{error || 'Connecting to your local media service…'}</p></div></div>
  return <Workspace key={product} product={product} version={health.version} session={session} />
}

function Workspace({ product, version, session }) {
  const [items, setItems] = useState([]), [quota, setQuota] = useState(null)
  const [device, setDevice] = useState({ connected: false }), [tracks, setTracks] = useState([]), [etag, setEtag] = useState(null)
  const [jobs, setJobs] = useState([]), [operations, setOperations] = useState({ busy: false }), [pending, setPending] = useState(false)
  const [error, setError] = useState(null), [errorFatal, setErrorFatal] = useState(null), [pollError, setPollError] = useState(null), [pollFatal, setPollFatal] = useState(null), [jobError, setJobError] = useState(null), [notice, setNotice] = useState(null)
  const [ready, setReady] = useState(false)
  const [selection, setSelection] = useState(new Set()), [backupBusy, setBackupBusy] = useState(false)
  const [backupAck, setBackupAck] = useState(() => readPreference('nightops:backup-ack', false))
  const [deleteTrack, setDeleteTrack] = useState(null)
  const [linkDialog, setLinkDialog] = useState(null)
  const [musicManager, setMusicManager] = useState(null)
  const [bridgeView, setBridgeView] = useState('listening')
  const [dragOver, setDragOver] = useState(false)
  const [showQueue, setShowQueue] = useState(() => readPreference(`nightops:${product}:queue-wing`, true))
  const [showEq, setShowEq] = useState(() => readPreference(`nightops:${product}:eq-wing`, false))
  const [visualMode, setVisualMode] = useState(() => readPreference(`nightops:${product}:visual`, 'spectrum'))
  const fileInput = useRef(null), latest = useRef({}), mounted = useRef(true), prepareJobs = useRef(new Map()), dragDepth = useRef(0)
  const isBridge = product === 'bridge'
  const busy = pending || backupBusy || processingBusy(operations) || jobs.some(job => !terminal(job))
  const currentJob = jobs.find(job => !terminal(job)) || jobs[0]
  const fatalFrom = value => value?.fatal_code ?? value?.detail?.fatal_code ?? null
  const clearError = () => { setError(null); setErrorFatal(null) }
  const showError = (message, fatal = null) => { setError(message); setErrorFatal(fatal ?? null) }
  const failedJob = currentJob && ['failed', 'interrupted'].includes(currentJob.status) ? currentJob : null
  const surface = surfaceError({ error, pollError, notice, fatalCode: resolvePlaylistCode(errorFatal, pollFatal, failedJob), view: isBridge ? bridgeView : 'listening' })
  const dismissNotice = () => { clearError(); setNotice(null) }

  const addJob = (job_id, kind) => setJobs(previous => [{ job_id, kind, status: 'pending', phase: 'queued', progress: 0, files: [], message: 'Operation admitted' }, ...previous.filter(job => job.job_id !== job_id)].slice(0, 12))
  const prepare = async id => {
    try { const result = await api.prepare(id); prepareJobs.current.set(result.job_id, id); addJob(result.job_id, 'prepare') }
    catch (e) { showError(`Lossless preparation failed: ${e.message}`, fatalFrom(e)) }
  }
  const player = usePlayer(items, product, prepare, session)
  latest.current = { jobs, player, items, busy }

  const refresh = async () => {
    const queue = await api.queue()
    if (!mounted.current) return
    const next = queue.items || []
    setItems(next); setQuota(queue.quota)
    if (Array.isArray(queue.playlists)) latest.current.player.syncPlaylists(queue.playlists)
    setSelection(previous => new Set([...previous].filter(id => next.some(item => item.id === id && transferable(item)))))
    if (latest.current.player.item && !next.some(item => item.id === latest.current.player.id)) await latest.current.player.forget()
    const ops = await api.operations()
    if (!mounted.current) return
    setOperations(ops)
    if (isBridge) {
      const d = await api.device()
      if (!mounted.current) return
      setDevice(d)
      if (d.connected) {
        const ledger = await api.tracks()
        if (!mounted.current) return
        setTracks(ledger.items || []); setEtag(ledger.etag)
      } else { setTracks([]); setEtag(null) }
    }
  }

  useEffect(() => {
    mounted.current = true
    let timer
    const poll = async () => {
      try { await refresh(); if (mounted.current) { setPollError(null); setPollFatal(null) } }
      catch (e) { if (mounted.current) { setPollError(`Connection interrupted: ${e.message}`); setPollFatal(fatalFrom(e)) } }
      if (mounted.current) { setReady(true); timer = setTimeout(poll, 3000) }
    }
    poll()
    api.latestJob().then(job => { if (mounted.current && job) setJobs(previous => previous.some(existing => existing.job_id === job.job_id) ? previous : [...previous, job]) }).catch(e => { if (mounted.current) setJobError(`Cannot recover previous operation: ${e.message}`) })
    return () => { mounted.current = false; clearTimeout(timer) }
  }, [])

  useEffect(() => {
    let timer, alive = true
    const poll = async () => {
      const active = latest.current.jobs.filter(job => !terminal(job))
      for (const job of active) {
        try {
          const result = await api.job(job.job_id)
          if (!alive) return
          setJobError(null)
          setJobs(previous => previous.map(existing => existing.job_id === result.job_id ? result : existing))
          if (terminal(result)) {
            await refresh()
            const mediaId = prepareJobs.current.get(result.job_id)
            if (mediaId) {
              prepareJobs.current.delete(result.job_id)
              // Refresh state is committed before retry so its strict ready gate
              // evaluates the cleared derivative rather than a scanning record.
              if (result.status === 'done') setTimeout(() => { if (mounted.current) latest.current.player.retryPrepared(mediaId) }, 0)
              else { latest.current.player.cancelPrepared(mediaId); showError(result.message || 'Lossless preparation did not complete.', result) }
            }
          }
        } catch (e) { if (alive) setJobError(`Operation status unavailable: ${e.message}. Its outcome is not yet known.`) }
      }
      if (alive) timer = setTimeout(poll, 1000)
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [])

  useEffect(() => {
    const enter = event => { event.preventDefault(); if (event.dataTransfer?.types?.includes('Files')) { dragDepth.current++; setDragOver(true) } }
    const leave = event => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragOver(false) }
    const over = event => event.preventDefault()
    const drop = event => { event.preventDefault(); dragDepth.current = 0; setDragOver(false); if (!linkDialog && !musicManager) importFiles(event.dataTransfer.files) }
    const keyboard = event => {
      if (event.target.closest('input, textarea, select, button, summary, [contenteditable="true"]') || event.altKey || event.ctrlKey || event.metaKey || deleteTrack || linkDialog || musicManager) return
      if (event.code === 'Space') { event.preventDefault(); latest.current.player.toggle() }
    }
    window.addEventListener('dragenter', enter); window.addEventListener('dragleave', leave); window.addEventListener('dragover', over); window.addEventListener('drop', drop); window.addEventListener('keydown', keyboard)
    return () => { window.removeEventListener('dragenter', enter); window.removeEventListener('dragleave', leave); window.removeEventListener('dragover', over); window.removeEventListener('drop', drop); window.removeEventListener('keydown', keyboard) }
  }, [deleteTrack, linkDialog, musicManager])

  const action = async task => {
    if (latest.current.busy) return
    setPending(true); clearError()
    try { await task(); await refresh() } catch (e) { showError(e.message, fatalFrom(e)) } finally { setPending(false) }
  }
  const importFiles = files => {
    const list = Array.from(files || [])
    if (!list.length) return
    if (latest.current.busy) { showError('Wait for the current operation to finish before adding files.'); return }
    action(async () => { const result = await api.importFiles(list); addJob(result.job_id, 'import') })
  }
  const importLink = async url => {
    if (latest.current.busy) throw new Error('Wait for the current operation to finish before importing another track.')
    setPending(true); clearError()
    try {
      const result = await api.importLink(url)
      addJob(result.job_id, 'link_import')
      // The operation panel polls durable states after admission. A later
      // queue refresh failure must not encourage duplicate link submission.
    } catch (e) { showError(e.message, fatalFrom(e)); throw e }
    finally { setPending(false) }
  }
  const rescan = id => action(async () => { if (player.id === id) await player.stop(); const result = await api.rescan(id); addJob(result.job_id, 'rescan') })
  const remove = id => action(() => latest.current.player.removeFromQueue(id))
  const move = (id, direction) => action(async () => { const next = moveTrack(latest.current.items, id, direction); await api.orderQueue(next.map(item => item.id)); setItems(next) })
  const transfer = ids => action(async () => { const result = await api.transfer(ids); addJob(result.job_id, 'transfer'); setSelection(new Set()) })
  const backup = async () => {
    if (busy) return
    setBackupBusy(true); clearError(); setNotice(null)
    try {
      const result = await api.backup(), path = backupSuccessPath(result)
      if (result.ok && path) { setNotice(backupSavedMessage(path)); setBackupAck(true); storePreference('nightops:backup-ack', true) }
      else showError('The service did not confirm a completed backup. Check the operation before transferring.')
    } catch (e) { showError(backupFailureMessage(e), fatalFrom(e)) } finally { setBackupBusy(false); refresh().catch(e => { setPollError(e.message); setPollFatal(fatalFrom(e)) }) }
  }
  const confirmDelete = () => {
    const track = deleteTrack
    setDeleteTrack(null)
    action(async () => { await api.deleteTrack(track.id, etag); setNotice(`Removed ${track.title || 'track'} from the device.`) })
  }
  const available = items.some(playable)
  const queueProps = { items, player, busy, quota, onImport: () => fileInput.current?.click(), onImportLink: event => { if (!latest.current.busy) setLinkDialog({ opener: event.currentTarget }) }, onRemove: remove, onMove: move, onRescan: rescan }
  const changeWing = (name, value) => { (name === 'queue' ? setShowQueue : setShowEq)(value); storePreference(`nightops:${product}:${name}-wing`, value) }
  const changeVisual = () => { const next = { spectrum: 'waveform', waveform: 'off', off: 'spectrum' }[visualMode] || 'spectrum'; setVisualMode(next); storePreference(`nightops:${product}:visual`, next) }
  const openManager = event => setMusicManager({ opener: event.currentTarget })
  const removeManagedMedia = async id => {
    if (latest.current.busy) throw new Error('Wait for the current operation to finish before removing music.')
    await latest.current.player.removeFromQueue(id)
    await refresh()
  }
  const stagePlaylist = ids => {
    setSelection(new Set(ids.filter(id => items.some(item => item.id === id && transferable(item)))))
    setBridgeView('transfer')
  }

  const dismissFailure = error || notice ? dismissNotice : null
  return <div className={`nightops-app ${isBridge ? 'bridge-composition' : 'player-composition'}`}>
    <a className="skip-link" href="#workspace">Skip to content</a>
    <Brand product={product} version={version} />
    <input ref={fileInput} aria-label="Choose audio files" type="file" multiple accept="audio/*,.mp3,.flac,.wav,.m4a,.aac,.ogg,.opus,.wma,.aif,.aiff" hidden onChange={e => { importFiles(e.target.files); e.target.value = '' }} />
    {surface.strip && <div className={`notice-strip ${surface.errorStyle ? 'error' : ''}`} role={surface.role}><span>{surface.strip}</span><button type="button" aria-label="Dismiss notification" onClick={dismissNotice}>{pollError && !error && !notice ? 'Reconnecting…' : '×'}</button></div>}
    {operations.draining && <div className="notice-strip" role="status">Finishing admitted operations before closing… Playback has stopped.</div>}
    {isBridge && <nav className="workspace-tabs" aria-label="Bridge views">{[['listening', 'Listening'], ['device', 'Walkman'], ['transfer', 'Transfer']].map(([view, label]) => <button type="button" key={view} className={bridgeView === view ? 'active' : ''} aria-pressed={bridgeView === view} aria-label={view === 'transfer' && selection.size > 0 ? `Transfer, ${selection.size} files selected` : undefined} onClick={() => setBridgeView(view)}>{label}{view === 'transfer' && selection.size > 0 && <span>{selection.size}</span>}</button>)}<span className={device.connected ? 'connection connected' : 'connection'} role="status" aria-live="polite"><i />{device.connected ? device.model || 'CONNECTED' : 'NO DEVICE'}</span></nav>}
    {!isBridge && <nav className="player-wing-controls" aria-label="Player panels"><button type="button" className={showQueue ? 'active' : ''} aria-pressed={showQueue} onClick={() => changeWing('queue', !showQueue)}>Playback queue</button><span>CRAFTED FOR THE LISTENING HOURS</span><button type="button" className={showEq ? 'active' : ''} aria-pressed={showEq} onClick={() => changeWing('eq', !showEq)}>Equalizer</button></nav>}
    <main id="workspace" tabIndex={-1} aria-label={isBridge ? { listening: 'Listening', device: 'Walkman', transfer: 'Transfer' }[bridgeView] : 'Player'} className={isBridge ? `bridge-workspace view-${bridgeView}` : `player-workspace ${showQueue ? 'has-queue' : ''} ${showEq ? 'has-eq' : ''}`}>
      {(currentJob || jobError) && <div className="workspace-operation"><JobPanel job={currentJob} pollingError={jobError} /></div>}
      {isBridge ? <>
        {bridgeView === 'listening' && <ListeningView player={player} available={available} mode={visualMode} onMode={changeVisual} showEq={showEq} onToggleEq={() => changeWing('eq', !showEq)} queueProps={queueProps} loading={!ready} />}
        {bridgeView !== 'listening' && device.connected && !backupAck && <div className="backup-warning"><Icon name="usb" size={18} /><span><strong>Protect the music already on your Walkman.</strong> Create a full backup before your first transfer.</span><button type="button" disabled={busy} onClick={backup}>Back up now</button><button type="button" onClick={() => { setBackupAck(true); storePreference('nightops:backup-ack', true) }}>I already have a backup</button></div>}
        {bridgeView === 'device' && <DevicePanel device={device} tracks={tracks} etag={etag} busy={busy} onBackup={backup} backupBusy={backupBusy} onDelete={setDeleteTrack} loading={!ready} failure={surface.alert} onDismissFailure={dismissFailure} />}
        {bridgeView === 'transfer' && <StagingPanel items={items} selection={selection} onSelection={setSelection} onTransfer={transfer} onRescan={rescan} onImport={queueProps.onImport} onImportLink={queueProps.onImportLink} busy={busy} connected={device.connected} loading={!ready} failure={surface.alert} onDismissFailure={dismissFailure} />}
      </> : <>
        {showQueue && <div className="player-wing queue-wing"><Queue {...queueProps} /></div>}
        <NowPlaying player={player} available={available} retro mode={visualMode} onMode={changeVisual} />
        {showEq && <div className="player-wing eq-wing"><Equalizer player={player} /></div>}
      </>}
    </main>
    <footer className={isBridge ? 'persistent-transport' : 'player-statusbar'}>
      {isBridge ? <div className="footer-track"><Icon name="disc" size={24} /><div><strong>{player.item?.title || player.item?.name || 'No track selected'}</strong><small>{player.item?.artist || 'Red Lotus'}</small></div></div> : <span className="library-count"><i className="status-light" />{items.filter(playable).length} CLEARED TRACKS</span>}
      <Transport player={player} available={available} compact />
      {isBridge && <><time aria-label={`Elapsed ${spokenTime(player.position)} of ${spokenTime(player.duration)}`}>{timeLabel(player.position)} / {timeLabel(player.duration)}</time><Volume player={player} /></>}
      {player.activePlaylist && <div className="playlist-context"><span title={player.activePlaylist.name}>{player.activePlaylist.name}</span><button type="button" className="text-button" aria-label={`Leave playlist ${player.activePlaylist.name} and show all music`} onClick={player.clearPlaylist}>All music</button></div>}
      <button type="button" className="button manage-music" onClick={openManager}>Manage music</button>
    </footer>
    {dragOver && <div className="drop-overlay"><Icon name="plus" size={48} /><strong>Drop into your listening queue.</strong><span>Files are copied locally and scanned before playback.</span></div>}
    {deleteTrack && <DeleteDialog track={deleteTrack} onCancel={() => setDeleteTrack(null)} onConfirm={confirmDelete} />}
    {linkDialog && <LinkImportDialog opener={linkDialog.opener} onClose={() => setLinkDialog(null)} onImport={importLink} />}
    {musicManager && <MusicManager {...musicManager} onClose={() => setMusicManager(null)} onChanged={refresh} product={product} items={items} onPlayPlaylist={player.setPlaylist} onRemoveMedia={removeManagedMedia} onStagePlaylist={stagePlaylist} busy={busy} deviceConnected={device.connected} deviceTracks={tracks} />}
  </div>
}

function DeleteDialog({ track, onCancel, onConfirm }) {
  const dialog = useRef(null)
  useEffect(() => { dialog.current.showModal(); return () => dialog.current?.close() }, [])
  return <dialog ref={dialog} className="delete-dialog" aria-labelledby="delete-dialog-title" onCancel={onCancel}><h2 id="delete-dialog-title">Remove from your Walkman?</h2><p>“{track.title || 'Untitled track'}” will be removed from the device database. Keep a verified backup before making changes.</p><div><button type="button" className="button" autoFocus onClick={onCancel}>Keep track</button><button type="button" className="button danger" onClick={onConfirm}>Remove from device</button></div></dialog>
}
