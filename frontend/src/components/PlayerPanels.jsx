import { useEffect, useRef, useState } from 'react'
import lotus from '../assets/lotus.png'
import { BAND_FREQUENCIES, PRESETS } from '../dsp.js'
import { playable, timeLabel } from '../playback.js'
import { fmtBytes } from '../format.js'
import { supplementalMetadata } from '../mediaImport.js'
import { CoverArt } from './CoverArt.jsx'

export function spokenTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'unknown time'
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)} minutes ${whole % 60} seconds`
}

export function Icon({ name, size = 20 }) {
  const shapes = {
    play: <path d="m8 4 12 8-12 8Z" />,
    pause: <><path d="M7 5h3v14H7zM14 5h3v14h-3z" /></>,
    previous: <><path d="M6 5v14m13-14L8 12l11 7Z" /></>,
    next: <><path d="M18 5v14M5 5l11 7-11 7Z" /></>,
    stop: <path d="M6 6h12v12H6z" />,
    shuffle: <path d="M3 6h3c5 0 7 12 12 12h3m-4-4 4 4-4 4M3 18h3c2 0 3-2 5-5m2-3c2-3 3-4 5-4h3m-4-4 4 4-4 4" />,
    repeat: <path d="m17 2 4 4-4 4M3 10V8a2 2 0 0 1 2-2h16M7 22l-4-4 4-4m14 0v2a2 2 0 0 1-2 2H3" />,
    volume: <><path d="M3 9h4l5-4v14l-5-4H3Zm13-1a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></>,
    plus: <path d="M12 4v16M4 12h16" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    up: <path d="m6 15 6-6 6 6" />,
    down: <path d="m6 9 6 6 6-6" />,
    usb: <path d="M12 21V3m-3 3 3-3 3 3M6 9v4l6 4m6-8v4l-6 4M5 7h2v2H5zm12 0h2v2h-2z" />,
    disc: <><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/><path d="M6 11a6 6 0 0 1 5-5m2 12a6 6 0 0 0 5-5"/></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name] || shapes.disc}</svg>
}

export function IconButton({ label, icon, children, active, className = '', ...props }) {
  return <button type="button" className={`icon-button ${active ? 'active' : ''} ${className}`} title={label} aria-label={label} {...(active !== undefined ? { 'aria-pressed': active } : {})} {...props}><Icon name={icon} />{children}</button>
}

export function Brand({ product, version }) {
  const win = window.walkmanBridge
  return <header className="app-titlebar">
    <div className="brand-mark"><img src={lotus} alt="Red Lotus" /><div><strong>RED LOTUS</strong><span>{product === 'player' ? 'PLAYER' : 'WALKMAN BRIDGE'}</span></div></div>
    <div className="edition">{product === 'player' ? 'PERSONAL AUDIO SYSTEM' : 'LOCAL AUDIO · DEVICE CONTROL'}<small>v{version || '0.2.0'}</small></div>
    {win && <div className="window-controls"><button title="Minimize" aria-label="Minimize window" onClick={() => win.minimize?.()}>—</button><button title="Maximize or restore" aria-label="Maximize or restore window" onClick={() => win.maximize?.()}>□</button><button title="Close" aria-label="Close application" onClick={() => win.close?.()}>×</button></div>}
  </header>
}

export function SectionHeading({ children, aside, level = 2 }) {
  const Tag = level === 1 ? 'h1' : level === 3 ? 'h3' : 'h2'
  return <div className="section-heading"><Tag>{children}</Tag>{aside}</div>
}

export const STATE_LABELS = { queued: 'Queued', downloading: 'Downloading', scanning: 'Scanning', analyzing: 'Reading metadata', awaiting_permission: 'Permission needed', blocked: 'Blocked', failed: 'Failed', ready: 'Ready', needs_scan: 'Scan required', transcoding: 'Converting', transferring: 'Transferring', transferred: 'On device', interrupted: 'Interrupted', unknown: 'Verify device' }
export function Status({ state }) { return <span className={`status state-${state || 'unknown'}`}><i />{STATE_LABELS[state] || 'Unknown'}</span> }

export function Visualizer({ graph, playing, mode, onMode }) {
  const canvas = useRef(null)
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const changed = () => setReduced(query.matches)
    query.addEventListener('change', changed)
    return () => query.removeEventListener('change', changed)
  }, [])
  useEffect(() => {
    const target = canvas.current
    const ctx = target.getContext('2d')
    let frame = 0, last = 0
    const analyser = graph?.analyser
    const values = new Uint8Array(analyser?.frequencyBinCount || 1024)
    const draw = time => {
      const box = target.getBoundingClientRect(), dpr = window.devicePixelRatio || 1
      const w = Math.round(box.width * dpr), h = Math.round(box.height * dpr)
      if (target.width !== w || target.height !== h) { target.width = w; target.height = h }
      ctx.clearRect(0, 0, w, h)
      ctx.strokeStyle = '#493019'; ctx.lineWidth = dpr
      for (let y = 1; y < 4; y++) { ctx.beginPath(); ctx.moveTo(0, h * y / 4); ctx.lineTo(w, h * y / 4); ctx.stroke() }
      if (!reduced && playing && analyser && mode !== 'off') {
        if (mode === 'waveform') {
          analyser.getByteTimeDomainData(values)
          ctx.beginPath(); ctx.strokeStyle = '#ffb543'; ctx.lineWidth = 1.5 * dpr
          for (let i = 0; i < values.length; i++) { const x = i / (values.length - 1) * w, y = values[i] / 255 * h; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y) }
          ctx.stroke()
        } else {
          analyser.getByteFrequencyData(values)
          const bars = Math.max(24, Math.floor(w / (8 * dpr)))
          for (let i = 0; i < bars; i++) {
            const low = Math.floor((Math.exp(i / bars * Math.log(values.length)) - 1))
            const high = Math.max(low + 1, Math.floor(Math.exp((i + 1) / bars * Math.log(values.length)) - 1))
            let level = 0
            for (let k = low; k < high; k++) level = Math.max(level, values[k] || 0)
            const bh = level / 255 * h * 0.9
            ctx.fillStyle = '#e88a29'; ctx.fillRect(i * w / bars, h - bh, Math.max(1, w / bars - 3 * dpr), bh)
          }
        }
      } else {
        ctx.strokeStyle = '#936124'; ctx.beginPath(); ctx.moveTo(0, h - 3 * dpr); ctx.lineTo(w, h - 3 * dpr); ctx.stroke()
      }
      if (playing && !reduced && mode !== 'off') frame = requestAnimationFrame(t => { if (t - last >= 32) { last = t; draw(t) } else frame = requestAnimationFrame(draw) })
    }
    draw(0)
    const resize = new ResizeObserver(() => { if (!playing || reduced || mode === 'off') draw(0) })
    resize.observe(target)
    return () => { cancelAnimationFrame(frame); resize.disconnect() }
  }, [graph, playing, reduced, mode])
  return <div className="visualizer"><canvas ref={canvas} role="img" aria-label={reduced ? 'Visualization paused for reduced motion' : `${mode} audio visualization`} /><div className="visualizer-caption"><span>{reduced ? 'REDUCED MOTION' : playing ? 'LIVE SIGNAL' : 'SIGNAL IDLE'}</span><button type="button" onClick={onMode} aria-label={`Visualization: ${mode}. Change mode`}>{mode.toUpperCase()}</button></div></div>
}

export function Transport({ player, available, compact = false }) {
  return <div className={`transport ${compact ? 'compact' : ''}`}>
    <IconButton label="Shuffle" icon="shuffle" active={player.shuffle} onClick={player.toggleShuffle} />
    <IconButton label="Previous track" icon="previous" onClick={() => player.advance(-1)} disabled={!available} />
    <IconButton label={player.playing ? 'Pause' : 'Play'} icon={player.playing ? 'pause' : 'play'} className="play-button" onClick={player.toggle} disabled={!available} />
    <IconButton label="Next track" icon="next" onClick={() => player.advance(1)} disabled={!available} />
    <IconButton label={`Repeat: ${player.repeat}`} icon="repeat" active={player.repeat !== 'off'} onClick={player.cycleRepeat}>{player.repeat === 'one' && <sup>1</sup>}</IconButton>
  </div>
}

export function Volume({ player, rotary = false }) {
  const drag = useRef(null)
  return <div className={`volume-control ${rotary ? 'rotary-volume' : ''}`}>
    {rotary && <button type="button" className="volume-dial" role="slider" aria-label="Rotary volume" aria-orientation="vertical" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(player.volume * 100)} aria-valuetext={`${Math.round(player.volume * 100)} percent`} style={{ '--turn': `${-130 + player.volume * 260}deg`, touchAction: 'none' }}
      onPointerDown={event => { drag.current = { y: event.clientY, value: player.volume }; event.currentTarget.setPointerCapture(event.pointerId) }}
      onPointerMove={event => { if (drag.current) player.volumeChange(drag.current.value + (drag.current.y - event.clientY) / 150) }}
      onPointerUp={event => { drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId) }} onPointerCancel={() => { drag.current = null }}
      onKeyDown={event => { const changes = { ArrowUp: .02, ArrowRight: .02, ArrowDown: -.02, ArrowLeft: -.02, PageUp: .1, PageDown: -.1 }; if (event.key in changes) { event.preventDefault(); player.volumeChange(player.volume + changes[event.key]) } else if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); player.volumeChange(event.key === 'Home' ? 0 : 1) } }}><i /></button>}
    <span className="volume-label"><Icon name="volume" size={16} /><span>VOLUME</span><output>{Math.round(player.volume * 100)}%</output></span>
    <input aria-label="Volume" aria-valuetext={`${Math.round(player.volume * 100)} percent`} type="range" min="0" max="1" step="0.01" value={player.volume} onChange={e => player.volumeChange(Number(e.target.value))} />
  </div>
}

export function Artwork({ item }) {
  return <CoverArt item={item} variant="deck" />
}

export function NowPlaying({ player, available, retro = false, mode, onMode }) {
  const item = player.item
  const extra = supplementalMetadata(item)
  return <section className={`now-playing ${retro ? 'retro-deck' : 'panel'}`} aria-label="Now playing">
    {retro ? <div className="deck-brand"><img src={lotus} alt="" /> RED LOTUS <small>HIGH FIDELITY / LOCAL AUDIO</small></div> : <SectionHeading aside={<span className="micro">{player.dsp.enabled ? 'DSP ON' : 'DIRECT'}</span>}>Now playing</SectionHeading>}
    <div className="audio-display">
      <div className="display-topline"><span><i className={player.playing ? 'lit' : ''} /><span role="status">{player.loading ? 'BUFFERING' : player.playing ? 'PLAYING' : item ? 'PAUSED' : 'STANDBY'}</span></span><span>{item?.mime?.split('/')[1]?.toUpperCase() || 'LOCAL AUDIO'}</span></div>
      <div className="track-display"><Artwork key={item?.id || 'standby'} item={item} /><div className="track-copy"><h1 title={item?.title || item?.name}>{item?.title || item?.name || 'Your next listening session.'}</h1><p>{item ? item.artist || 'Unknown artist' : 'Drop your music. Find your frequency.'}</p><small>{item ? item.album || 'Unknown album' : 'Local files & links · Cleared before playback'}</small>{extra && <small className="track-details">{extra}</small>}</div></div>
      <Visualizer graph={player.graph} playing={player.playing} mode={mode} onMode={onMode} />
      <div className="seek-row"><time>{timeLabel(player.position)}</time><input type="range" aria-label="Playback position" aria-valuetext={`${spokenTime(player.position)} of ${spokenTime(player.duration || item?.duration_seconds)}`} min="0" max={player.duration || 1} step="0.1" value={Math.min(player.position, player.duration || 0)} disabled={!item || !player.duration} onChange={e => player.seek(Number(e.target.value))} /><time>{timeLabel(player.duration || item?.duration_seconds)}</time></div>
    </div>
    {player.error && <p role="status" className="playback-error">{player.error}</p>}
    <div className="deck-controls"><Transport player={player} available={available} /><Volume player={player} rotary={retro} />{retro && <button type="button" className="text-button stop-button" onClick={() => { player.seek(0); player.stop().catch(player.showError) }} disabled={!item}><Icon name="stop" size={14} /> Stop</button>}</div>
    {retro && <div className="deck-foot"><span>10 BAND / STEREO</span><span>RL—01</span></div>}
  </section>
}

export function Equalizer({ player }) {
  const [preset, setPreset] = useState('Flat')
  const dsp = player.dsp
  return <section className="panel equalizer" aria-label="Equalizer">
    <SectionHeading aside={<span className={`micro ${dsp.enabled ? 'amber' : ''}`}>{dsp.enabled ? 'ACTIVE' : 'BYPASSED'}</span>}>Ten-band equalizer</SectionHeading>
    <div className="eq-toolbar"><label className="toggle"><input type="checkbox" checked={dsp.enabled} onChange={e => player.setDsp({ enabled: e.target.checked })} /><span>Enable audio processing</span></label><select aria-label="Equalizer preset" value={preset} onChange={e => { setPreset(e.target.value); player.setDsp({ gains: PRESETS[e.target.value] }) }}>{Object.keys(PRESETS).map(name => <option key={name}>{name}</option>)}<option value="Custom" disabled>Custom</option></select></div>
    <div className={`eq-bands ${!dsp.enabled ? 'bypassed' : ''}`}>{BAND_FREQUENCIES.map((hz, i) => <label key={hz}><output>{dsp.gains[i] > 0 ? '+' : ''}{dsp.gains[i]}</output><input type="range" aria-label={`${hz} Hz gain in decibels`} aria-valuetext={`${dsp.gains[i]} decibels`} min="-6" max="6" step="0.5" value={dsp.gains[i]} onChange={e => { const gains = [...dsp.gains]; gains[i] = Number(e.target.value); setPreset('Custom'); player.setDsp({ gains }) }} /><span>{hz >= 1000 ? `${hz / 1000}k` : hz}</span></label>)}</div>
    <div className="eq-options"><label className="toggle"><input type="checkbox" checked={dsp.loudness} onChange={e => player.setDsp({ loudness: e.target.checked })} />Loudness match</label><label className="toggle"><input type="checkbox" checked={dsp.limiter} onChange={e => player.setDsp({ limiter: e.target.checked })} />Output limiter</label><button className="text-button" onClick={() => { setPreset('Flat'); player.setDsp({ gains: PRESETS.Flat }) }}>Reset bands</button></div>
    <div className="panel-note">Headroom {dsp.preampDb.toFixed(1)} dB · Playback only · Source preserved</div>
  </section>
}

export function Queue({ items, player, onImport, onImportLink, onRemove, onMove, onRescan, busy, quota, loading = false }) {
  const [search, setSearch] = useState('')
  const visible = items.filter(item => [item.title, item.artist, item.name].join(' ').toLowerCase().includes(search.toLowerCase()))
  const titleOf = item => item.title || item.name || 'track'
  return <section className="panel queue-panel" aria-label="Playback queue" aria-busy={loading || undefined}>
    <SectionHeading aside={<span className="micro">{items.length} TRACKS</span>}>Playback queue</SectionHeading>
    <div className="queue-tools"><input type="search" autoComplete="off" aria-label="Search playback queue" placeholder="Find a track…" value={search} onChange={e => setSearch(e.target.value)} /><div className="import-actions"><button type="button" className="button" onClick={onImport} disabled={busy}><Icon name="plus" size={15} /> Add files</button><button type="button" className="button" onClick={onImportLink} disabled={busy}>Import link</button></div></div>
    <div className="queue-list">{loading ? <div className="empty-state" role="status"><Icon name="disc" size={38} /><strong>Loading your queue</strong><p>Your listening queue will appear here.</p></div> : visible.length ? <ul>{visible.map(item => <li className={`queue-row ${player.id === item.id ? 'selected' : ''}`} key={item.id}><span className="track-number">{String(items.indexOf(item) + 1).padStart(2, '0')}</span><CoverArt item={item} variant="thumb" /><button type="button" className="track-select" aria-current={player.id === item.id ? 'true' : undefined} aria-label={`Play ${titleOf(item)}`} disabled={!playable(item)} onClick={() => player.play(item.id)}><strong>{item.title || item.name}</strong><small>{item.artist || 'Unknown artist'}</small></button><div className="queue-meta">{playable(item) ? <span>{timeLabel(item.duration_seconds)}</span> : <Status state={item.status} />}</div><details className="queue-row-menu"><summary aria-label={`Actions for ${titleOf(item)}`}>⋯</summary><div className="row-actions"><IconButton label={`Move ${titleOf(item)} up`} icon="up" disabled={busy || items.indexOf(item) === 0} onClick={() => onMove(item.id, -1)} /><IconButton label={`Move ${titleOf(item)} down`} icon="down" disabled={busy || items.indexOf(item) === items.length - 1} onClick={() => onMove(item.id, 1)} />{!playable(item) && <button type="button" className="text-button" disabled={busy} onClick={() => onRescan(item.id)}>Rescan</button>}<IconButton label={`Remove ${titleOf(item)} from queue`} icon="close" disabled={busy} onClick={() => onRemove(item.id)} /></div></details></li>)}</ul> : <div className="empty-state" role="status"><Icon name="disc" size={38} /><strong>{search ? 'No matching tracks' : 'Make room for your music.'}</strong><p>{search ? 'Try a different title or artist.' : 'Add audio files or drop them anywhere in this window. Your queue stays here between sessions.'}</p>{!search && <button type="button" className="button" disabled={busy} onClick={onImport}>Choose audio files</button>}</div>}</div>
    <div className="panel-note"><span>{items.filter(playable).length} cleared for playback</span><span>{fmtBytes(quota?.used_bytes)} / {fmtBytes(quota?.limit_bytes)}</span></div>
  </section>
}

export function ListeningView({ player, available, mode, onMode, showEq, onToggleEq, queueProps, loading = false }) {
  return <div className="listening-column" role="region" aria-label="Listening" aria-busy={loading || undefined}>
    <NowPlaying player={player} available={available} retro mode={mode} onMode={onMode} />
    <div className="bridge-eq-toggle"><button type="button" className="text-button" aria-expanded={showEq} onClick={onToggleEq}>{showEq ? '− Hide' : '+ Show'} equalizer</button><span className="micro" role="status">{player.dsp.enabled ? 'PROCESSING ON' : 'DSP BYPASSED'}</span></div>
    {showEq && <Equalizer player={player} />}
    <Queue {...queueProps} loading={loading} />
  </div>
}
