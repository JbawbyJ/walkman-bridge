import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { playable, transferable } from '../playback.js'
import { fmtBytes } from '../format.js'
import '../music-manager.css'

const label = track => track.title || track.name || `Track ${track.id}`
const unique = values => [...new Set(values)]

export default function MusicManager({ opener, onClose, onChanged, product, items, busy = false,
  onPlayPlaylist, onRemoveMedia, onStagePlaylist, deviceConnected = false, deviceTracks = [] }) {
  const dialog = useRef(null), running = useRef(false), alive = useRef(true)
  const [tab, setTab] = useState('files'), [search, setSearch] = useState(''), [sort, setSort] = useState('title')
  const [chosen, setChosen] = useState(new Set()), [lists, setLists] = useState([]), [sony, setSony] = useState({ items: [], etag: null })
  const [current, setCurrent] = useState(null), [newName, setNewName] = useState(''), [newFromSelection, setNewFromSelection] = useState(false)
  const [pending, setPending] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState(null), [notice, setNotice] = useState(null)
  const [editing, setEditing] = useState(null), [confirmation, setConfirmation] = useState(null)
  const [addTo, setAddTo] = useState('')
  const [sonyError, setSonyError] = useState(null)
  const disabled = pending || busy
  const native = tab === 'sony'
  const catalog = native ? sony.items : lists
  const selected = catalog.find(list => list.id === current)
  const members = native ? deviceTracks : items
  const memberIds = selected ? native ? selected.track_ids : selected.media_ids : []
  const filtered = items.filter(item => `${label(item)} ${item.artist || ''} ${item.album || ''} ${item.name}`.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => sort === 'size' ? (b.size_bytes || 0) - (a.size_bytes || 0) : String(a[sort] || '').localeCompare(String(b[sort] || '')))

  const reload = async () => {
    const local = await api.playlists()
    if (alive.current) { setLists(local.items || []); setLoading(false) }
    if (product === 'bridge' && deviceConnected) {
      try { const device = await api.devicePlaylists(); if (alive.current) { setSony(device); setSonyError(null) } }
      catch (e) { if (alive.current) { setSony({ items: [], etag: null }); setSonyError(e.message) } }
    } else if (alive.current) { setSony({ items: [], etag: null }); setSonyError(null) }
  }
  useEffect(() => {
    alive.current = true; dialog.current.showModal()
    reload().catch(e => { if (alive.current) { setError(e.message); setLoading(false) } })
    return () => { alive.current = false; dialog.current?.close(); queueMicrotask(() => opener?.isConnected && opener.focus()) }
  }, [])
  useEffect(() => { setChosen(previous => new Set([...previous].filter(id => items.some(item => item.id === id)))) }, [items])
  const mutate = async (action, message) => {
    if (running.current || busy) return
    running.current = true; setPending(true); setError(null); setNotice(null)
    try { await action(); await onChanged?.(); await reload(); if (alive.current) setNotice(message) }
    catch (e) {
      if (alive.current) setError(e.message || 'The change could not be confirmed. Refresh before retrying.')
      // A stale or uncertain device response never replays the mutation.
      try { await reload() } catch { /* keep the original failure visible */ }
    } finally { running.current = false; if (alive.current) setPending(false) }
  }
  const switchTab = value => { setTab(value); setCurrent(null); setNewName(''); setConfirmation(null); setEditing(null); setError(null); setNewFromSelection(false) }
  const toggle = id => setChosen(previous => { const next = new Set(previous); next.has(id) ? next.delete(id) : next.add(id); return next })
  const saveMembers = ids => mutate(() => native
    ? api.updateDevicePlaylist(selected.id, { track_ids: ids }, sony.etag)
    : api.updatePlaylist(selected.id, { media_ids: ids }, selected.etag), 'Playlist updated.')
  const createList = event => {
    event.preventDefault()
    mutate(async () => {
      const result = native ? await api.createDevicePlaylist({ name: newName, track_ids: [] }, sony.etag)
        : await api.createPlaylist(newName, newFromSelection ? items.filter(item => chosen.has(item.id)).map(item => item.id) : [])
      setCurrent(native ? result.playlist.id : result.id); setNewName(''); setNewFromSelection(false)
    }, 'Playlist created.')
  }
  const rename = name => mutate(() => native ? api.updateDevicePlaylist(selected.id, { name }, sony.etag)
    : api.updatePlaylist(selected.id, { name }, selected.etag), 'Playlist renamed.')
  const removeConfirmed = () => {
    const action = confirmation; setConfirmation(null)
    mutate(async () => {
      if (action.kind === 'files') {
        for (const id of action.ids) await onRemoveMedia(id)
        setChosen(new Set()); setEditing(null)
      } else {
        if (action.kind === 'sony') await api.deleteDevicePlaylist(action.id, sony.etag)
        else await api.deletePlaylist(action.id, action.etag)
        setCurrent(null)
      }
    }, action.kind === 'files' ? 'Managed copies removed. Original files are untouched.' : 'Playlist removed; its music files are retained.')
  }
  return <dialog ref={dialog} className="music-manager" aria-labelledby="music-manager-title" onCancel={e => { e.preventDefault(); if (!pending) onClose() }}>
    <header className="manager-heading"><div><h2 id="music-manager-title">Manage music</h2><p>Your music, organized for listening.</p></div><button aria-label="Close music manager" disabled={pending} onClick={onClose}>×</button></header>
    <nav className="manager-tabs" aria-label="Music management"><button aria-pressed={tab === 'files'} onClick={() => switchTab('files')} disabled={pending}>Files</button><button aria-pressed={tab === 'playlists'} onClick={() => switchTab('playlists')} disabled={pending}>Playlists</button>{product === 'bridge' && <button aria-pressed={native} onClick={() => switchTab('sony')} disabled={pending}>Sony playlists</button>}</nav>
    {(error || notice) && <p className={error ? 'manager-error' : 'manager-notice'} role={error ? 'alert' : 'status'}>{error || notice}</p>}
    {native && sonyError && <p className="manager-error" role="alert">Sony playlists are unavailable: {sonyError} Use Refresh lists after resolving the device issue.</p>}
    {pending && <p role="status" className="manager-notice">Saving changes… Wait for the result before disconnecting a device.</p>}
    {confirmation && <section className="manager-confirm" role="alert"><p>{confirmation.kind === 'files' ? `Remove ${confirmation.ids.length} managed music file(s) and their playlist memberships? Your originals stay untouched.` : `Delete “${confirmation.name}”? Its music files will remain.`}</p><button onClick={removeConfirmed} disabled={disabled}>Confirm removal</button><button onClick={() => setConfirmation(null)}>Cancel</button></section>}
    <div className="manager-content">
      {tab === 'files' ? <>
        <div className="manager-toolbar"><input aria-label="Search managed music" placeholder="Search title, artist, album…" value={search} onChange={e => setSearch(e.target.value)} /><label>Sort<select value={sort} onChange={e => setSort(e.target.value)}><option value="title">Title</option><option value="artist">Artist</option><option value="album">Album</option><option value="size">Size</option></select></label></div>
        <div className="manager-selection"><label><input type="checkbox" checked={filtered.length > 0 && filtered.every(item => chosen.has(item.id))} onChange={e => setChosen(previous => { const next = new Set(previous); for (const item of filtered) e.target.checked ? next.add(item.id) : next.delete(item.id); return next })} />Select shown</label><span>{chosen.size} selected · {items.length} files</span><button disabled={disabled || !chosen.size} onClick={() => { setTab('playlists'); setNewFromSelection(true); setCurrent(null) }}>New playlist from selection</button><button disabled={disabled || !chosen.size} onClick={() => setConfirmation({ kind: 'files', ids: [...chosen] })}>Remove selected</button></div>
        <div className="manager-file-layout"><div className="manager-track-list" aria-label="Managed music files">{filtered.map(item => <div className="manager-file-row" key={item.id}><input type="checkbox" aria-label={`Select ${label(item)}`} checked={chosen.has(item.id)} onChange={() => toggle(item.id)} /><div><strong>{label(item)}</strong><small>{item.artist || 'Unknown artist'} · {item.album || 'Unknown album'}</small><small>{item.name} · {fmtBytes(item.size_bytes || 0)} · {item.status}</small></div><button disabled={disabled} onClick={() => setEditing(item)}>Edit details</button></div>)}{!filtered.length && <p>No matching imported files.</p>}</div>
          {editing && <MetadataEditor key={editing.id} item={editing} disabled={disabled} onCancel={() => setEditing(null)} onSave={changes => mutate(async () => { await api.updateMetadata(editing.id, changes); setEditing(null) }, 'Music details saved. The next transfer uses the updated tags.')} />}</div>
        {!!chosen.size && !!lists.length && <div className="manager-toolbar"><select aria-label="Playlist for selected files" value={addTo} onChange={e => setAddTo(e.target.value)}><option value="">Add selection to a playlist…</option>{lists.map(list => <option key={list.id} value={list.id}>{list.name}</option>)}</select><button disabled={disabled || !addTo} onClick={() => { const list = lists.find(row => row.id === addTo); if (list) mutate(() => api.updatePlaylist(list.id, { media_ids: unique([...list.media_ids, ...chosen]) }, list.etag), 'Files added to playlist.') }}>Add to playlist</button></div>}
        <p className="manager-help">These are the apps’ managed copies. Editing details or removing music here does not change your original files.</p>
      </> : native && !deviceConnected ? <p>Connect your Sony to view and manage its playlists.</p> : <>
        <form className="manager-toolbar" onSubmit={createList}><input aria-label={native ? 'New Sony playlist name' : 'New playlist name'} placeholder={newFromSelection ? `Name for ${chosen.size} selected files…` : 'New playlist name…'} value={newName} maxLength={native ? 60 : 120} required onChange={e => setNewName(e.target.value)} /><button disabled={disabled || loading || (native && !sony.etag) || !newName.trim()}>Create playlist</button></form>
        {native && <p className="manager-help">These playlists appear in the Sony’s playlist menu. Add music already on the device; use Transfer to send imported files first.</p>}
        <div className="manager-playlist-layout"><aside aria-label={native ? 'Sony playlists' : 'Saved playlists'}>{catalog.map(list => <button key={list.id} aria-pressed={current === list.id} onClick={() => setCurrent(list.id)} disabled={pending}><strong>{list.name}</strong><small>{(native ? list.track_ids : list.media_ids).length} tracks</small></button>)}{!catalog.length && <p>{loading ? 'Loading playlists…' : 'No playlists yet.'}</p>}</aside>
          {selected ? <PlaylistEditor key={`${tab}:${selected.id}`} playlist={selected} ids={memberIds} tracks={members} disabled={disabled} native={native} onRename={rename} onSave={saveMembers} onDelete={() => setConfirmation({ kind: tab, id: selected.id, name: selected.name, etag: selected.etag })} onPlay={!native ? () => mutate(async () => { await onPlayPlaylist(selected); onClose() }, 'Playlist selected.') : null} onStage={!native && product === 'bridge' ? () => { onStagePlaylist(selected.media_ids); onClose() } : null} /> : <p className="manager-empty">Choose a playlist to edit its name, tracks and order.</p>}</div>
      </>}
    </div>
    <footer className="manager-footer"><button onClick={() => mutate(async () => {}, 'Lists refreshed.')} disabled={disabled}>Refresh lists</button><button className="primary" disabled={pending} onClick={onClose}>Done</button></footer>
  </dialog>
}

function MetadataEditor({ item, disabled, onCancel, onSave }) {
  const keys = ['title', 'artist', 'album', 'genre', 'year', 'track']
  const [draft, setDraft] = useState(Object.fromEntries(keys.map(key => [key, item[key] ?? ''])))
  return <form className="manager-editor" onSubmit={e => { e.preventDefault(); onSave(draft) }}><h3>Edit music details</h3><p>{item.name}</p>{keys.map(key => <label key={key}>{({ track: 'Track number', year: 'Year' })[key] || key[0].toUpperCase() + key.slice(1)}<input value={draft[key]} maxLength={key === 'year' ? 4 : key === 'track' ? 9 : 512} inputMode={key === 'year' ? 'numeric' : 'text'} onChange={e => setDraft({ ...draft, [key]: e.target.value })} disabled={disabled} /></label>)}<div className="manager-actions"><button className="primary" disabled={disabled}>Save details</button><button type="button" onClick={onCancel}>Cancel</button></div></form>
}

function PlaylistEditor({ playlist, ids, tracks, disabled, native, onRename, onSave, onDelete, onPlay, onStage }) {
  const [name, setName] = useState(playlist.name), [adding, setAdding] = useState(new Set()), [query, setQuery] = useState('')
  useEffect(() => setName(playlist.name), [playlist.name])
  useEffect(() => setAdding(previous => new Set([...previous].filter(id => !ids.includes(id)))), [ids.join(',')])
  const candidates = tracks.filter(track => !ids.includes(track.id) && `${label(track)} ${track.artist || ''}`.toLowerCase().includes(query.toLowerCase()))
  const reorder = (index, direction) => { const next = [...ids]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; onSave(next) }
  const available = ids.map(id => tracks.find(track => track.id === id)).filter(Boolean)
  return <section className="manager-playlist-editor"><form className="manager-toolbar" onSubmit={e => { e.preventDefault(); onRename(name) }}><input aria-label="Playlist name" value={name} maxLength={native ? 60 : 120} required onChange={e => setName(e.target.value)} /><button disabled={disabled || !name.trim() || name === playlist.name}>Rename</button></form>
    <div className="manager-actions">{onPlay && <button disabled={disabled || !available.some(playable)} onClick={onPlay}>Play playlist</button>}{onStage && <button disabled={disabled || !available.length || !available.every(transferable)} onClick={onStage}>Stage for transfer</button>}<button disabled={disabled} onClick={onDelete}>Delete playlist</button></div>
    <ol className="manager-members" aria-label="Playlist track order">{ids.map((id, index) => { const track = tracks.find(row => row.id === id); const title = track ? label(track) : `Unavailable track ${id}`; return <li key={`${id}:${index}`}><span>{title}<small>{track?.artist || ''}</small></span><button aria-label={`Move ${title} up`} disabled={disabled || !index} onClick={() => reorder(index, -1)}>↑</button><button aria-label={`Move ${title} down`} disabled={disabled || index === ids.length - 1} onClick={() => reorder(index, 1)}>↓</button><button aria-label={`Remove ${title} from playlist`} disabled={disabled} onClick={() => onSave(ids.filter((value, at) => at !== index))}>×</button></li> })}</ol>
    {!ids.length && <p>This playlist is empty. Add tracks below.</p>}
    <details className="manager-add-tracks"><summary>Add {native ? 'Sony' : 'imported'} tracks</summary><input aria-label="Find tracks to add" placeholder="Filter tracks…" value={query} onChange={e => setQuery(e.target.value)} /><div>{candidates.map(track => <label key={track.id}><input type="checkbox" checked={adding.has(track.id)} onChange={() => setAdding(previous => { const next = new Set(previous); next.has(track.id) ? next.delete(track.id) : next.add(track.id); return next })} />{label(track)}</label>)}</div><button disabled={disabled || !adding.size} onClick={() => onSave(native ? [...ids, ...adding] : unique([...ids, ...adding]))}>Add selected tracks</button></details>
  </section>
}
