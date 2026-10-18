import { Icon } from './PlayerPanels.jsx'

// One voice for Listening, Walkman, and Transfer placeholders.
// Titles are short status lines. Messages are calm sentences.
export const VIEW_STATE_COPY = {
  listening: {
    loading: {
      title: 'Loading your queue',
      message: 'Your listening queue will appear here.',
    },
    empty: {
      title: 'Make room for your music',
      message: 'Add audio files or drop them anywhere in this window. Your queue stays here between sessions.',
    },
    noMatch: {
      title: 'No matching tracks',
      message: 'Try a different title or artist.',
    },
    playback: {
      title: 'Playback needs attention',
    },
  },
  walkman: {
    checking: {
      title: 'Checking for a Walkman',
      message: 'Looking for a USB device.',
    },
    loading: {
      title: 'Loading the device library',
      message: 'Tracks already on the Walkman will appear here.',
    },
    disconnected: {
      title: 'Your device ledger appears here',
      message: 'Local playback is available while disconnected.',
    },
    empty: {
      title: 'No tracks on this device',
      message: 'Cleared music can be staged for transfer.',
    },
    noMatch: {
      title: 'No matching tracks',
      message: 'Try a different title or artist.',
    },
  },
  transfer: {
    loading: {
      title: 'Loading staged files',
      message: 'Cleared music ready for the Walkman will appear here.',
    },
    empty: {
      title: 'Nothing staged yet',
      message: 'Add music to your local queue, then select cleared files for your Walkman.',
    },
    noMatch: {
      title: 'No matching files',
      message: 'Try a different file name.',
    },
  },
}

export function ViewState({ variant = 'empty', title, message, icon = null, onDismiss = null, children = null, role }) {
  const resolvedRole = role || (variant === 'error' ? 'alert' : 'status')
  const alert = resolvedRole === 'alert'
  const classes = ['view-state', `view-state-${variant}`]
  if (variant === 'empty' || variant === 'loading') classes.push('empty-state')
  if (alert) classes.push('playlist-failure')
  if (variant === 'error' && !alert) classes.push('playback-error')
  return <div className={classes.join(' ')} role={resolvedRole} {...(alert ? { 'aria-live': 'assertive' } : {})}>
    {icon ? <Icon name={icon} size={32} /> : null}
    <div className="view-state-copy">
      <strong>{title}</strong>
      {message ? <p>{message}</p> : null}
    </div>
    {children}
    {onDismiss ? <button type="button" className="view-state-dismiss" onClick={onDismiss}>Dismiss</button> : null}
  </div>
}
