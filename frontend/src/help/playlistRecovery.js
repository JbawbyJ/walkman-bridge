// Copy for Sony playlist failures, keyed by the device fatal_code.
// Not imported by the UI yet. Match the code only; never parse the message.
// In-app repair / inspect / recover controls are not wired in this version.
//
// path comes from the device (detail.fatal_path). Append it as plain text only,
// never through innerHTML or markdown, and omit it when absent.
// Callers of formatPlaylistRecovery should pass recoveryAction from
// error.recovery_action (detail.recovery_action). A non-empty recoveryAction
// overrides the action id chosen from the code.

const PATH_CODES = new Set(['DEVICE_FILE_LOCKED', 'DEVICE_FILE_READ_ONLY'])

export const PLAYLIST_RECOVERY_HELP = {
  PLAYLIST_REF_MISSING: {
    title: 'Playlist lists songs that are gone',
    explanation: 'A playlist on the Walkman still names songs that are no longer on the device.',
    action: 'Repair the playlist to drop those missing songs. In-app repair is coming soon.',
    actionId: 'repair',
  },
  PLAYLIST_JOURNAL_PENDING: {
    title: 'A playlist save was interrupted',
    explanation: 'A previous playlist write stopped halfway, and its unfinished record is still on the Walkman.',
    action: 'Inspect first and read the result, then recover. Recover finishes applying the save only when it was committed; otherwise redo that edit. In-app inspect and recover are coming soon.',
    actionId: 'inspect_recover',
  },
  PLAYLIST_SLOTS_EXHAUSTED: {
    title: 'The Walkman playlist table is full',
    explanation: 'The device playlist table has used all 2048 slots, so another playlist cannot be saved.',
    action: 'Delete playlists you no longer need, or combine them, until a slot is free.',
    actionId: 'free_slots',
  },
  PLAYLIST_LIBRARY_NOT_LOADED: {
    title: 'The Walkman music list did not load',
    explanation: 'The Walkman\'s music list didn\'t load, or a song file on the device couldn\'t be read, so repair stopped without changing anything.',
    action: 'Reconnect the Walkman, wait for the library to load, then try repair again. If it keeps happening, keep your backup and ask for help.',
    actionId: 'reconnect_retry',
  },
  DEVICE_FILE_LOCKED: {
    title: 'A file on the Walkman is open',
    explanation: 'Another program (for example Explorer, a media player, or antivirus) has a file on the Walkman open. Walkman Bridge stopped and nothing was changed.',
    action: 'Close that program and try the change again.',
    actionId: 'close_and_retry',
  },
  DEVICE_ROLLBACK_FAILED: {
    title: 'The playlist save could not be undone',
    explanation: 'The save failed and could not be fully undone.',
    action: 'Stop making changes, keep the Walkman connected, and run Inspect first.',
    actionId: 'inspect_recover',
  },
  DEVICE_FILE_READ_ONLY: {
    title: 'A song file on the Walkman is read-only',
    explanation: 'A song file on the Walkman is marked read-only, so Walkman Bridge stopped before changing anything.',
    action: 'Clear the read-only setting on that file (in Windows: right-click it, choose Properties, untick Read-only), then try again.',
    actionId: 'clear_read_only_retry',
  },
  GENERIC: {
    title: 'Playlist change failed',
    explanation: 'The playlist change failed without a known recovery code.',
    action: 'Back up the Walkman and try again after the app is idle. Do not choose a fix from the message text.',
    actionId: null,
  },
}

const RECOVER_COPY = {
  DEVICE_FILE_LOCKED: {
    explanation: 'Another program (for example Explorer, a media player, or antivirus) has a file on the Walkman open. The save was already committed and only needs finishing.',
    action: 'Close the program and run Recover again.',
    actionId: 'inspect_recover',
  },
  DEVICE_FILE_READ_ONLY: {
    explanation: 'A song file on the Walkman is marked read-only. The save was already committed and only needs finishing.',
    action: 'Clear the read-only setting on that file (in Windows: right-click it, choose Properties, untick Read-only), then run Recover again.',
    actionId: 'inspect_recover',
  },
}

export function playlistRecoveryHelp(fatalCode) {
  return PLAYLIST_RECOVERY_HELP[fatalCode] || PLAYLIST_RECOVERY_HELP.GENERIC
}

function nonEmptyString(value) {
  return typeof value === 'string' && value.length > 0
}

export function formatPlaylistRecovery(code, { path, context, recoveryAction } = {}) {
  const entry = playlistRecoveryHelp(code)
  const recover = context === 'recover' ? RECOVER_COPY[code] : null
  const chosen = recover || entry
  let explanation = chosen.explanation
  if (PATH_CODES.has(code) && nonEmptyString(path)) explanation += ` (${path})`
  return {
    title: entry.title,
    explanation,
    action: chosen.action,
    actionId: nonEmptyString(recoveryAction) ? recoveryAction : chosen.actionId,
  }
}
