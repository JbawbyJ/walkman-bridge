import { useEffect, useRef, useState } from 'react'
import { linkImportUrl } from '../mediaImport.js'

export default function LinkImportDialog({ opener, onClose, onImport }) {
  const dialog = useRef(null), input = useRef(null), alive = useRef(true), submitting = useRef(false)
  const [url, setUrl] = useState(''), [pending, setPending] = useState(false), [error, setError] = useState(null)
  useEffect(() => {
    alive.current = true
    const element = dialog.current
    element.showModal()
    input.current.focus()
    return () => {
      alive.current = false
      element.close()
      // A just-admitted import disables its opener; the workspace remains an
      // accessible focus destination while persistent job polling continues.
      queueMicrotask(() => {
        if (opener?.isConnected && !opener.disabled) opener.focus()
        else document.querySelector('main')?.focus()
      })
    }
  }, [opener])
  const submit = async event => {
    event.preventDefault()
    if (submitting.current) return
    let normalized
    try { normalized = linkImportUrl(url) } catch (e) { setError(e.message); input.current.focus(); return }
    submitting.current = true; setPending(true); setError(null)
    try { await onImport(normalized); if (alive.current) onClose() }
    catch (e) { if (alive.current) { setError(e.message || 'The import request failed. Try again.'); input.current.focus() } }
    finally { submitting.current = false; if (alive.current) setPending(false) }
  }
  return <dialog ref={dialog} className="link-import-dialog" aria-labelledby="link-import-title" aria-describedby="link-import-help" onCancel={event => { event.preventDefault(); onClose() }}>
    <div className="dialog-heading"><h2 id="link-import-title">Import a music link</h2><button type="button" className="icon-button" aria-label="Close link importer" onClick={onClose}>×</button></div>
    <p id="link-import-help">Add one public YouTube or SoundCloud track to your local queue. Downloaded audio is scanned before playback.</p>
    <form onSubmit={submit} noValidate aria-busy={pending}>
      <label htmlFor="music-link">Track URL</label>
      <input ref={input} id="music-link" type="url" inputMode="url" autoComplete="off" spellCheck={false} maxLength={2048} placeholder="https://…" value={url} readOnly={pending} aria-invalid={!!error} aria-describedby={`link-import-limits${error ? ' link-import-error' : ''}`} onChange={event => { setUrl(event.target.value); setError(null) }} />
      <small id="link-import-limits">Single tracks only · No playlists, live streams, or sign-in</small>
      {error && <p id="link-import-error" className="link-import-error" role="alert">{error}</p>}
      {pending && <p role="status">Requesting import… You can close this window; admitted downloads continue in Import & clearance.</p>}
      <div className="dialog-actions"><button type="button" className="button" onClick={onClose}>Close</button><button type="submit" className="button primary" disabled={pending}>{pending ? 'Requesting…' : 'Import to queue'}</button></div>
    </form>
  </dialog>
}
