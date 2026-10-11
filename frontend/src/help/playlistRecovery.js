// Copy for Sony playlist failures, keyed by the device fatal_code.
// Not imported by the UI yet. Match the code only; never parse the message.
// In-app repair / inspect / recover controls are not wired in this version.

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
    action: 'Reconnect the Walkman, wait for the library to load, then try repair again.',
    actionId: 'reconnect_retry',
  },
  DEVICE_FILE_LOCKED: {
    title: 'A song file on the Walkman is open',
    // {path} comes from the device (detail.fatal_path). Insert it as plain text only,
    // never through innerHTML or markdown, and omit it when absent.
    explanation: 'Another program (for example Explorer, a media player, or antivirus) has a song file on the Walkman open{path}. Nothing was changed.',
    action: 'Close that program and try again.',
    actionId: 'close_and_retry',
  },
  DEVICE_ROLLBACK_FAILED: {
    title: 'The playlist save could not be undone',
    explanation: 'The save failed and could not be fully undone.',
    action: 'Stop making changes, keep the Walkman connected, and run Inspect first.',
    actionId: 'inspect_recover',
  },
  GENERIC: {
    title: 'Playlist change failed',
    explanation: 'The playlist change failed without a known recovery code.',
    action: 'Back up the Walkman and try again after the app is idle. Do not choose a fix from the message text.',
    actionId: null,
  },
}

export function playlistRecoveryHelp(fatalCode) {
  return PLAYLIST_RECOVERY_HELP[fatalCode] || PLAYLIST_RECOVERY_HELP.GENERIC
}
