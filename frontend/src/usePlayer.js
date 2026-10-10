import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { createAudioGraph, dspSettings } from './dsp.js'
import { nextTrackId, playable, restoreSession } from './playback.js'

export function usePlayer(items, product, onCodecError, session) {
  const [state, setState] = useState(() => {
    const ids = session?.playlist?.media_ids
    const stale = Array.isArray(ids) && !ids.includes(session?.media_id)
    return { ...restoreSession({ ...session, id: stale ? ids[0] || null : session?.media_id,
      position: stale ? 0 : session?.position_seconds }), duration: 0, error: null, loading: false }
  })
  const [dsp, setDsp] = useState(() => dspSettings())
  const [graph, setGraph] = useState(null)
  const [activePlaylist, setActivePlaylist] = useState(() => session?.playlist || null)
  const playlistRef = useRef(activePlaylist)
  const audio = useRef(null), graphRef = useRef(null), latest = useRef({}), fallback = useRef(new Set()), stopped = useRef(false)
  const saveChain = useRef(Promise.resolve()), lastSave = useRef(0)
  const selectedId = useRef(state.id), positionRef = useRef(state.position), wantsPlay = useRef(false), generation = useRef(0)
  const leaseChain = useRef(Promise.resolve()), leasedId = useRef(null), loadingId = useRef(null)
  const removing = useRef(new Set()), preparing = useRef(new Set())
  const sessionKey = `nightops:${product}:session`
  latest.current = { items, state, dsp, onCodecError, sessionKey }

  const persist = () => {
    const s = latest.current.state
    const position = audio.current?.getAttribute('src') && audio.current.dataset.mediaId === selectedId.current && Number.isFinite(audio.current.currentTime) ? audio.current.currentTime : positionRef.current
    const body = { media_id: selectedId.current, position_seconds: position, volume: s.volume, shuffle: s.shuffle, repeat: s.repeat, playlist_id: playlistRef.current?.id || null }
    lastSave.current = Date.now()
    // Serialize writes so an older network response can never overwrite a later
    // seek/pause. Electron awaits stop's flush before draining the service.
    const saving = saveChain.current.catch(() => {}).then(() => api.savePlaybackState(body))
    saveChain.current = saving
    return saving
  }
  const acquireLease = (id, request) => {
    const acquiring = leaseChain.current.catch(() => {}).then(async () => {
      if (request !== generation.current || removing.current.has(id)) return false
      if (leasedId.current !== id) {
        await api.acquirePlaybackLease(id)
        leasedId.current = id
      }
      return true
    })
    leaseChain.current = acquiring
    return acquiring
  }
  const releaseLease = () => {
    // Enqueue synchronously. Reading the lease inside this ordered operation
    // handles an in-flight acquisition, including Stop -> Play of the same ID.
    const releasing = leaseChain.current.catch(() => {}).then(async () => {
      const id = leasedId.current
      if (id) { await api.releasePlaybackLease(id); leasedId.current = null }
    })
    leaseChain.current = releasing
    return releasing
  }
  const unload = () => {
    const element = audio.current
    if (element) { element.pause(); element.removeAttribute('src'); delete element.dataset.mediaId; element.load() }
  }
  const stop = () => {
    const saved = persist()
    if (audio.current?.getAttribute('src')) positionRef.current = audio.current.currentTime || 0
    stopped.current = true; wantsPlay.current = false; generation.current++; loadingId.current = null
    unload()
    setState(s => ({ ...s, playing: false, loading: false }))
    return Promise.all([saved, releaseLease()])
  }
  const pausePlayback = () => {
    wantsPlay.current = false; generation.current++; loadingId.current = null
    if (!audio.current?.getAttribute('src')) { stop().catch(() => {}); return }
    audio.current.pause()
    setState(s => ({ ...s, playing: false, loading: false }))
  }
  const ensureGraph = async item => {
    if (!graphRef.current) {
      graphRef.current = createAudioGraph(audio.current)
      setGraph(graphRef.current)
    }
    graphRef.current.update({ ...latest.current.dsp, integratedLufs: item.integrated_lufs })
    await graphRef.current.resume()
  }
  const loadTrack = async (item, { position = 0, revision = '', playing = false } = {}) => {
    if (!audio.current || removing.current.has(item.id)) return
    const element = audio.current
    const changed = selectedId.current !== item.id || !element.getAttribute('src') || revision || element.error
    const request = ++generation.current
    stopped.current = false; wantsPlay.current = playing; loadingId.current = item.id
    if (changed) unload()
    selectedId.current = item.id
    positionRef.current = changed ? position : element.currentTime || 0
    setState(s => ({ ...s, id: item.id, error: null, loading: playing, ...(changed ? { playing: false, position, duration: item.duration_seconds || 0 } : {}) }))
    try {
      const acquired = await acquireLease(item.id, request)
      if (!acquired || request !== generation.current || audio.current !== element) return
      if (changed) {
        element.dataset.mediaId = item.id
        element.dataset.pendingPosition = String(position)
        element.src = api.mediaUrl(item.id, revision)
        element.load()
      }
      if (!playing) return
      await ensureGraph(item)
      if (request !== generation.current || !wantsPlay.current || audio.current !== element) return
      await element.play()
    } catch (error) {
      if (request !== generation.current || error.name === 'AbortError') return
      // The media error event handles codec errors. Network and autoplay
      // failures must never trigger a format conversion.
      if (!element.error) { wantsPlay.current = false; setState(s => ({ ...s, error: error.message, loading: false, playing: false })) }
    } finally {
      if (request === generation.current) loadingId.current = null
    }
  }
  const play = (id = selectedId.current, options = {}) => {
    const available = row => playable(row) && !removing.current.has(row.id)
    if (id && playlistRef.current && !playlistRef.current.media_ids.includes(id)) {
      playlistRef.current = null; setActivePlaylist(null)
    }
    const item = latest.current.items.find(row => row.id === id && available(row))
      || (!id ? collectionItems().find(available) : null)
    return item ? loadTrack(item, { ...options, playing: true }) : Promise.resolve()
  }
  const advance = (direction = 1, ended = false) => {
    const s = latest.current.state
    if (direction < 0 && audio.current?.currentTime > 3) { audio.current.currentTime = 0; return }
    const id = nextTrackId(collectionItems().filter(item => !removing.current.has(item.id)), selectedId.current, { direction, repeat: ended ? s.repeat : s.repeat === 'one' ? 'all' : s.repeat, shuffle: s.shuffle })
    if (id) {
      if (id === s.id && audio.current) audio.current.currentTime = 0
      play(id)
    } else pausePlayback()
  }
  const forget = () => {
    const saved = stop()
    // Clear synchronously so completion cannot overwrite a newer selection.
    selectedId.current = null; positionRef.current = 0
    setState(s => ({ ...s, id: null, position: 0, duration: 0, playing: false }))
    return saved
  }
  const removeFromQueue = async id => {
    removing.current.add(id)
    try {
      if (selectedId.current === id) await forget()
      await api.removeQueueItem(id)
      // Keep selection blocked until a queue refresh confirms its removal.
    } catch (error) { removing.current.delete(id); throw error }
  }

  useEffect(() => {
    const element = new Audio()
    element.preload = 'metadata'
    element.volume = latest.current.state.volume
    audio.current = element
    stopped.current = false
    const updateTime = () => {
      if (element.getAttribute('src') && element.dataset.mediaId === selectedId.current) {
        positionRef.current = element.currentTime || 0
        setState(s => ({ ...s, position: positionRef.current, duration: Number.isFinite(element.duration) ? element.duration : s.duration }))
      }
    }
    const loaded = () => {
      const pending = Number(element.dataset.pendingPosition)
      if (Number.isFinite(pending) && pending > 0 && Number.isFinite(element.duration)) element.currentTime = Math.min(pending, Math.max(0, element.duration - 0.05))
      delete element.dataset.pendingPosition
      updateTime()
    }
    const playing = () => {
      if (!wantsPlay.current || stopped.current) { element.pause(); return }
      setState(s => ({ ...s, playing: true, loading: false, error: null }))
    }
    const pause = () => { if (!element.paused) return; setState(s => ({ ...s, playing: false })); persist().catch(() => setState(s => ({ ...s, error: 'Playback is paused, but its position could not be saved.' }))) }
    const ended = () => advance(1, true)
    const waiting = () => setState(s => ({ ...s, loading: true }))
    const error = () => {
      const id = element.dataset.mediaId
      if (!id || id !== selectedId.current) return
      const code = element.error?.code
      if ([3, 4].includes(code) && id && !fallback.current.has(id)) {
        fallback.current.add(id)
        preparing.current.add(id)
        setState(s => ({ ...s, loading: false, playing: false, error: 'Preparing a cleared lossless copy for this codec…' }))
        latest.current.onCodecError(id)
      } else setState(s => ({ ...s, loading: false, playing: false, error: [3, 4].includes(code) ? 'This audio could not be decoded. Try rescanning or another source.' : 'Playback connection failed. Press Play to try again.' }))
    }
    const events = { timeupdate: updateTime, durationchange: updateTime, loadedmetadata: loaded, playing, pause, ended, waiting, error }
    Object.entries(events).forEach(([name, handler]) => element.addEventListener(name, handler))
    const stopEvent = () => { stop().catch(() => {}) }
    const unsubscribe = window.walkmanBridge?.onStopPlayback?.(stop)
    window.addEventListener('walkman:stop-playback', stopEvent)
    window.addEventListener('pagehide', stopEvent)
    return () => {
      stop().catch(() => {})
      Object.entries(events).forEach(([name, handler]) => element.removeEventListener(name, handler))
      element.pause(); element.removeAttribute('src'); element.load()
      graphRef.current?.dispose(); graphRef.current = null; audio.current = null
      if (typeof unsubscribe === 'function') unsubscribe()
      window.removeEventListener('walkman:stop-playback', stopEvent)
      window.removeEventListener('pagehide', stopEvent)
    }
  }, [])

  useEffect(() => {
    if (audio.current) audio.current.volume = state.volume
    const delay = Math.max(0, 1000 - (Date.now() - lastSave.current))
    const timer = setTimeout(() => { persist().catch(() => setState(s => ({ ...s, error: 'Playback continues, but session changes could not be saved.' }))) }, delay)
    return () => clearTimeout(timer)
  }, [state.id, Math.floor(state.position), state.volume, state.shuffle, state.repeat, sessionKey])

  const trackLoudness = items.find(item => item.id === state.id)?.integrated_lufs
  useEffect(() => {
    graphRef.current?.update({ ...dsp, integratedLufs: trackLoudness })
  }, [dsp, state.id, trackLoudness])

  useEffect(() => {
    for (const id of removing.current) if (!items.some(item => item.id === id)) removing.current.delete(id)
    const current = items.find(item => item.id === selectedId.current)
    if (!current && selectedId.current && (items.length || audio.current?.getAttribute('src'))) {
      forget().catch(() => {})
      return
    }
    if (current && !playable(current) && audio.current?.getAttribute('src')) {
      if (preparing.current.has(current.id)) { unload(); setState(s => ({ ...s, playing: false, loading: false })) }
      else stop().catch(() => {})
    }
    if (current && playable(current) && !audio.current?.getAttribute('src') && !stopped.current && !preparing.current.has(current.id) && loadingId.current !== current.id) {
      // Metadata restoration acquires a lease, but never requests playback.
      loadTrack(current, { position: positionRef.current })
    }
  }, [items])

  const seek = seconds => {
    if (!audio.current || !Number.isFinite(seconds)) return
    if (Number.isFinite(audio.current.duration)) audio.current.currentTime = Math.max(0, Math.min(audio.current.duration, seconds))
    positionRef.current = Math.max(0, seconds)
    setState(s => ({ ...s, position: Math.max(0, seconds) }))
  }

  const collectionItems = () => playlistRef.current
    ? playlistRef.current.media_ids.map(id => latest.current.items.find(item => item.id === id)).filter(Boolean)
    : latest.current.items
  const clearPlaylist = () => {
    playlistRef.current = null; setActivePlaylist(null)
    return persist()
  }
  const setPlaylist = async playlist => {
    const stopping = stop(), request = generation.current
    await stopping
    if (request !== generation.current) return
    const chosen = { id: playlist.id, name: playlist.name, media_ids: [...playlist.media_ids] }
    playlistRef.current = chosen; setActivePlaylist(chosen)
    const first = collectionItems().find(playable)
    if (first) await play(first.id)
    else await forget()
    await persist()
  }
  const syncPlaylists = catalog => {
    if (!playlistRef.current || !Array.isArray(catalog)) return
    const updated = catalog.find(list => list.id === playlistRef.current.id)
    if (!updated) { clearPlaylist().catch(error => setState(s => ({ ...s, error: error.message }))); return }
    if (JSON.stringify(updated.media_ids) === JSON.stringify(playlistRef.current.media_ids) && updated.name === playlistRef.current.name) return
    playlistRef.current = updated; setActivePlaylist(updated)
    if (selectedId.current && !updated.media_ids.includes(selectedId.current)) forget().catch(error => setState(s => ({ ...s, error: error.message })))
  }

  return {
    ...state, graph, dsp, audio,
    activePlaylist, setPlaylist, clearPlaylist, syncPlaylists,
    item: items.find(item => item.id === state.id),
    play, stop, advance, seek, forget, removeFromQueue,
    showError: error => setState(s => ({ ...s, error: error.message || String(error) })),
    toggle: () => state.playing ? pausePlayback() : play(selectedId.current, { position: positionRef.current }),
    volumeChange: volume => setState(s => ({ ...s, volume: Math.max(0, Math.min(1, volume)) })),
    toggleShuffle: () => setState(s => ({ ...s, shuffle: !s.shuffle })),
    cycleRepeat: () => setState(s => ({ ...s, repeat: { off: 'all', all: 'one', one: 'off' }[s.repeat] })),
    setDsp: input => setDsp(previous => dspSettings({ ...previous, ...input })),
    retryPrepared: id => {
      preparing.current.delete(id)
      const item = latest.current.items.find(row => row.id === id && playable(row))
      if (item && id === selectedId.current && !stopped.current) return loadTrack(item, { position: positionRef.current, revision: Date.now(), playing: wantsPlay.current })
    },
    cancelPrepared: id => { preparing.current.delete(id); if (id === selectedId.current) { wantsPlay.current = false; generation.current++ } },
  }
}
