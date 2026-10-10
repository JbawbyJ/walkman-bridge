import lotus from '../assets/lotus.png'

// Window controls render only when the Electron preload injects
// window.walkmanBridge (minimize / maximize / close). pywebview is legacy
// and does not inject it, so that path stays a brand bar. See docs/DESIGN.md.
function WinButton({ onClick, className = '', children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-11 grid place-items-center font-mono text-ink2 cursor-default transition-colors ${className}`}
      style={{ WebkitAppRegion: 'no-drag' }}
    >
      {children}
    </button>
  )
}

export default function Titlebar({ version, host, winApi }) {
  return (
    <div
      className="flex-none flex items-stretch h-[38px] bg-surface border-b border-line"
      style={{ WebkitAppRegion: 'drag' }}
    >
      <div className="flex items-center gap-[10px] px-[14px]">
        <img src={lotus} alt="Red Lotus" className="w-4 h-4 object-contain" style={{ filter: 'invert(1)' }} />
        <span className="font-display text-[11px] font-bold tracking-widest">WALKMAN BRIDGE</span>
        <span className="font-mono text-[10px] text-muted">v{version}</span>
        <span className="font-mono text-[9px] tracking-wider px-2 py-[2px] border border-line text-success">
          LIVE · {host}
        </span>
      </div>
      {winApi && (
        <div className="ml-auto flex" style={{ WebkitAppRegion: 'no-drag' }}>
          <WinButton onClick={() => winApi.minimize?.()} className="text-[12px] hover:bg-sunken">
            –
          </WinButton>
          <WinButton onClick={() => winApi.maximize?.()} className="text-[10px] hover:bg-sunken">
            ▢
          </WinButton>
          <WinButton onClick={() => winApi.close?.()} className="text-[11px] hover:bg-brand hover:text-ink-inverse">
            ✕
          </WinButton>
        </div>
      )}
    </div>
  )
}
