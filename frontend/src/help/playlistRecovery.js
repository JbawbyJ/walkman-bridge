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
    action: 'Inspect that record, then recover it. In-app inspect and recover are coming soon.',
    actionId: 'inspect_recover',
  },
  PLAYLIST_SLOTS_EXHAUSTED: {
    title: 'The Walkman playlist table is full',
    explanation: 'The device playlist table has used all 2048 slots, so another playlist cannot be saved.',
    action: 'Delete playlists you no longer need, or combine them, until a slot is free.',
    actionId: 'free_slots',
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
