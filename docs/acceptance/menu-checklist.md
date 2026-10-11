# Menu and screen checklist — Walkman Bridge

Walk the Bridge screens, then the Walkman’s own menus after a sync. Use a backed-up device before any write. Player differences are the last section before the pending fatal-code checks.

Title-bar version should be read and recorded. Source at this revision is **0.4.1**.

| Field | Entry |
| --- | --- |
| Tester | |
| Date | |
| Title-bar version | |
| Device model | |

## Shell

- [ ] The title bar reads RED LOTUS / WALKMAN BRIDGE and shows a version.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Minimize, maximize/restore, and close work while nothing is transferring. Do not close during a device write for this pass.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Close the app while a track is playing. The window closes within a few seconds, and Task Manager shows no Walkman Bridge, Electron, or player process still running.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Close the app right after launching it, while the startup screen is still showing. The window closes within a few seconds, and Task Manager shows no Walkman Bridge, Electron, or player process still running.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Buttons show a visible focus ring. Space plays or pauses when focus is outside a field, button, or dialog.
  - **Result:** pass / fail
  - **Notes:**

## Listening

- [ ] **Listening** shows the retro deck, the playback queue, and **+ Show equalizer**. The equalizer starts hidden (**DSP BYPASSED**) unless this profile already stored it open.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Shuffle, previous, play/pause, next, and repeat cycle (off, all, one). Seek, volume, and Stop respond. Stop returns to the start and stops.
  - **Result:** pass / fail
  - **Notes:**
- [ ] The visualization control cycles spectrum, waveform, and off.
  - **Result:** pass / fail
  - **Notes:**
- [ ] The queue can add files, open **Import link**, search, and use the row menu (move, rescan when not playable, remove). Dragging files shows the drop overlay.
  - **Result:** pass / fail
  - **Notes:**
- [ ] **Import link** offers one public YouTube video or SoundCloud track and states that playlists, live streams, and sign-in are out of scope.
  - **Result:** pass / fail
  - **Notes:**

## Walkman

- [ ] Disconnected: the tab shows **NO DEVICE**, the ledger empty state, and transfer stays unavailable.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Connected: the tab shows **CONNECTED** or the reported model, plus a storage meter, track count, and **Back up device**. The device heading uses the reported model, or the literal fallback `NW-S705F` when the service omits one. Do not treat that fallback as a detected model.
  - **Result:** pass / fail
  - **Notes:**
- [ ] On a profile that has not acknowledged a backup, Walkman and Transfer show the backup warning with **Back up now** and **I already have a backup**.
  - **Result:** pass / fail
  - **Notes:**
- [ ] The ledger searches and sorts by title, artist, and album. Each row shows title, artist, album, and duration.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Delete opens **Remove from your Walkman?** **Keep track** leaves the song in place.
  - **Result:** pass / fail
  - **Notes:**

## Transfer

- [ ] Staging searches, sorts, selects cleared files, and labels transfer **MP3 / 192 kbps / 44.1 kHz**.
  - **Result:** pass / fail
  - **Notes:**
- [ ] A file that is not cleared can be rescanned and cannot be selected for transfer.
  - **Result:** pass / fail
  - **Notes:**
- [ ] The operation panel shows phase and progress. **Operation details** expands per file. An uncertain write says device state needs verification.
  - **Result:** pass / fail
  - **Notes:**

## Manage music

- [ ] **Manage music** opens **Files**, **Playlists**, and **Sony playlists**.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Files search and sort, edit title, artist, album, genre, year, and track number, and require confirmation before removing managed copies. The confirm text says originals stay untouched.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Local playlists can be created, renamed, reordered, and deleted. Deleting a playlist keeps the audio. **Play playlist** and **Stage for transfer** work. Playback shows the playlist name in the footer; **All music** clears it.
  - **Result:** pass / fail
  - **Notes:**
- [ ] **Sony playlists** needs a connected device. The help text says these lists appear in the Sony playlist menu and that tracks must already be on the device.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Deleting a Sony playlist does not remove its songs from the device ledger.
  - **Result:** pass / fail
  - **Notes:**
- [ ] A local playlist does not show up as a Sony playlist until its tracks are transferred and added on the Sony playlists tab.
  - **Result:** pass / fail
  - **Notes:**

## On-device menus after sync

The app ledger is one list. Artist and album browsing is on the Walkman, using the tags written at transfer.

- [ ] The device artist menu lists the transferred artist, or the unknown fallback saved by the app.
  - **Result:** pass / fail
  - **Notes:**
- [ ] The device album menu lists the transferred album, or the unknown fallback.
  - **Result:** pass / fail
  - **Notes:**
- [ ] The track is reachable from the device’s song or title list, with the saved title.
  - **Result:** pass / fail
  - **Notes:**
- [ ] A Sony playlist created in the app appears on the device playlist menu, in the saved order, without skipped or substituted members.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Removing that playlist on the device or in the app leaves the songs in the artist and album lists.
  - **Result:** pass / fail
  - **Notes:**
- [ ] After **Refresh lists** and a ledger refresh, the app and the device menus name the same tracks.
  - **Result:** pass / fail
  - **Notes:**

## Red Lotus Player

- [ ] Player has no Listening, Walkman, or Transfer tabs, no device ledger, and no Sony playlists tab. It has **Playback queue** and **Equalizer** wing toggles, plus Files and local Playlists in Manage music.
  - **Result:** pass / fail
  - **Notes:**

## HeadlessCli playlist fatal codes (pending)

**Status: pending.** Do not mark these pass on the current build.

Checked in this revision:

- `backend/jsymphonic.py` raises from a fatal event’s `message`. It does not read a fatal `code`.
- Sony playlist edits are jobs. On failure the API returns `detail.code` of `playlist_failed` or `verify_device_state` (`backend/playlists.py`). Those are not the three HeadlessCli codes below.
- `frontend/src/api.js` copies `detail.code` onto the error object. Manage music renders `e.message` only, so that code is not shown. A failed list is prefixed `Sony playlists are unavailable:`.
- The workspace operation panel tracks jobs it already holds (import, transfer, rescan, prepare). It does not add the playlist `job_id`. The alert in Manage music is the visible failure text.
- This repository does not reference `playlist-repair` or `playlist-recover`.
- The app also refuses a create when the snapshot already has 200 playlists (`The device can contain at most 200 playlists`) and refuses more than 200 tracks in one playlist. Those checks are not `PLAYLIST_SLOTS_EXHAUSTED`. Whether the 2048-slot fatal replaces either check is unverified.

When passthrough and UI land, show the HeadlessCli `code` and message in the Manage music alert. Until then, copy the raw alert into **Notes** and leave the boxes unchecked.

- [ ] `PLAYLIST_REF_MISSING` is visible with the message. The UI says the playlist has a dangling track reference and names `playlist-repair`. It does not delete songs.
  - **Result:** pass / fail
  - **Notes:**
- [ ] `PLAYLIST_JOURNAL_PENDING` is visible with the message. The UI says a playlist transaction journal remains and names `playlist-recover --inspect` before another playlist write. It does not retry the write.
  - **Result:** pass / fail
  - **Notes:**
- [ ] `PLAYLIST_SLOTS_EXHAUSTED` is visible with the message. The UI says the 2048-slot playlist limit is reached and to delete a playlist before creating another.
  - **Result:** pass / fail
  - **Notes:**
- [ ] `DEVICE_FILE_LOCKED` is visible with the message. For a normal change the UI says another program has a file on the Walkman open, Walkman Bridge stopped and nothing was changed, and to close that program and try the change again. If this happened while recovering, the UI says the save was already committed and only needs finishing, and to close the program and run Recover again. A file location from the device is plain text when present and is omitted when absent.
  - **Result:** pass / fail
  - **Notes:**
- [ ] `DEVICE_ROLLBACK_FAILED` is visible with the message. The UI says the save failed and could not be fully undone, and to stop making changes, keep the Walkman connected, and run Inspect first. The unfinished record remains, so inspect and recover still apply.
  - **Result:** pass / fail
  - **Notes:**
- [ ] `DEVICE_FILE_READ_ONLY` is visible with the message. For a normal change the UI says a song file on the Walkman is marked read-only, Walkman Bridge stopped before changing anything, and to clear Read-only in the file's Properties, then try again. If this happened while recovering, the UI says the save was already committed and only needs finishing, and to clear Read-only and run Recover again. A file location from the device is plain text when present and is omitted when absent.
  - **Result:** pass / fail
  - **Notes:**
- [ ] A fatal with no `code` is a generic failure: message only, with none of the recovery instructions for the codes above attached.
  - **Result:** pass / fail
  - **Notes:**
