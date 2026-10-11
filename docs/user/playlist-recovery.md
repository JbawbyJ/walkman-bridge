# Recovering playlists on your Walkman

This page is for Walkman Bridge owners. It explains what to do when a change to
the playlists stored on the Walkman itself does not finish.

**Playlists** under **Manage music** are lists kept on your PC. **Sony
playlists** are the lists the Walkman shows in its own menu. This page is about
Sony playlists.

Walkman Bridge does not yet show buttons for inspect, recover, or repair.
Those controls are coming soon. Until they appear, follow this page. If you
are comfortable with a command prompt, the commands at the end do the same
work.

## How to read a failure

A failed playlist change can include a short code. Choose the fix from that
code. If there is no code, or the code is not one of the nine failure names or the warning below,
treat it as a general failure. The longer message is not a code. Do not decide
the fix by reading it.

| Code | In plain words | What to do |
| --- | --- | --- |
| `PLAYLIST_JOURNAL_PENDING` | A previous playlist save was cut off. An unfinished record is still on the Walkman. | Inspect, read the result, then recover. |
| `PLAYLIST_REF_MISSING` | A playlist still names songs that are no longer on the Walkman. | Run playlist repair. |
| `PLAYLIST_SLOTS_EXHAUSTED` | The Walkman's playlist table is full (2048 slots). | Delete or combine playlists. |
| `PLAYLIST_LIBRARY_NOT_LOADED` | The Walkman's music list did not load, or a song file on the device could not be read, so repair stopped without changing anything. | Reconnect the Walkman, wait for the library to load, then try repair again. If it keeps happening, keep your backup and ask for help. |
| `DEVICE_FILE_LOCKED` | Another program has a file on the Walkman open. Walkman Bridge stopped and nothing was changed. | Close that program and try the change again. If this happened while recovering an interrupted save, the save was already committed and only needs finishing, so close the program and run Recover again. |
| `DEVICE_ROLLBACK_FAILED` | The save failed and could not be fully undone. The unfinished record is still on the Walkman. | Stop making changes, keep the Walkman connected, and run Inspect first. |
| `DEVICE_FILE_READ_ONLY` | A song file on the Walkman is marked read-only, so Walkman Bridge stopped before changing anything. | Clear the read-only setting on that file, then try again. If this happened while recovering, the save was already committed and only needs finishing, so clear read-only and run Recover again. |
| `DEVICE_PROBE_RESTORE_FAILED` | A song file was renamed during a safety check and could not be renamed back. | Do not rename or delete files by hand. Keep the Walkman connected, close other programs, then run Inspect and Recover. |
| `DEVICE_PROBE_CONFLICT` | Two different copies of the same song file are on the Walkman. Walkman Bridge will not change anything. | Do not delete either file. Keep your backup and ask for help. Neither Recover nor the next save can resolve this. |
| `DEVICE_PROBE_PENDING` (warning) | Nothing is broken. A safety check left a renamed copy. What happens next depends on the files. | The next save or Recover tidies it up when it can. If the copies differ, that save stops with `DEVICE_PROBE_CONFLICT`. |
| No code, or any other code | The change failed, and there is no specific recovery step. | Back up, wait until the app is idle, and try once more. |

## The unfinished record

When Walkman Bridge saves a Sony playlist, it first writes a small record on
the Walkman named `.jsymphonic-playlist-transaction`. Think of it as a note of
a change that is still in progress. The playlists already on the device stay
as they are until that note contains a commit mark, and that mark has been
saved all the way onto the Walkman.

If the save is interrupted — the cable comes out, the PC sleeps, or the app
closes in the middle — the note is left on the Walkman. Later playlist saves
stop until that note is resolved. That stop is `PLAYLIST_JOURNAL_PENDING`. The
note does not delete your songs.

## What not to do

- Do not unplug the Walkman while a sync or playlist save is running.
  Unplugging in the middle leaves the unfinished record on the device.
- Do not delete `.jsymphonic-playlist-transaction` yourself in File Explorer.
  Removing that file by hand can hide a save that had already committed, or
  leave the playlist tables in a state the app can no longer explain.
- Do not save again while the code is `PLAYLIST_JOURNAL_PENDING`. The next
  save will keep stopping until the unfinished record is resolved.
- Do not choose a fix from the words in the message. Use the code, or the
  general-failure steps when the code is missing or unfamiliar.

## Back up the Walkman first

Make a full backup and confirm it completed before you recover, repair, or
delete playlists to free slots. Walkman Bridge keeps a backup only after the
copy has been checked. If a backup is interrupted, the app shows **Verify
device state** and does not try that write again on its own.

A backup matters most when you are unsure whether the last playlist save
finished, and before the first write to a physical Walkman. Repair changes
playlist membership. Recover either finishes a committed save or drops an
interrupted one. Neither deletes songs on the Walkman, and neither deletes the
original files on your PC. The backup is still the way back if the result is
not what you expected.

## Inspect, then recover

Use these steps when the code is `PLAYLIST_JOURNAL_PENDING`.

1. Leave the Walkman plugged into the same USB port. Wait until Walkman Bridge
   is not transferring, scanning, or saving.
2. Make a full backup and confirm it completed.
3. Inspect first, and read the result. Inspect only reads. It reports which of
   the two recover outcomes applies: the journal has a commit marker, or it
   does not. It does not change playlists or music.
4. Recover only after that result. Plain `playlist-recover` rolls the change
   forward only when the journal has a commit marker. With no commit marker it
   discards the journal. The interrupted change is lost, and the Walkman keeps
   the playlists it had before that change. You may need to redo your last
   playlist edit after recovering.
5. Redo that playlist edit only when inspect said there was no commit marker.
   When inspect said the commit marker was already there, recover finishes
   that save.

In-app inspect and recover are coming soon. They are not in this version.

## Repair missing songs

Use these steps when the code is `PLAYLIST_REF_MISSING`.

A playlist can still name a song after that song was removed in Media Go, or
deleted on the Walkman. The playlist line is left behind, and further playlist
saves stop so the app does not write a list it cannot trust.

1. Leave the Walkman connected.
2. Make a full backup and confirm it completed.
3. Run playlist repair. Repair removes playlist lines that point at songs
   which are no longer on the device. Songs that are still on the Walkman stay
   in the playlist. Repair does not delete audio files.

In-app repair is coming soon. It is not in this version.

If the code is `PLAYLIST_LIBRARY_NOT_LOADED`, the Walkman's music list was empty or did not finish loading, or a song file on the device could not be read, so repair stopped. Nothing was written to the device. Repair refuses rather than emptying playlists when the track list did not load or a song file could not be read. Reconnect the Walkman, wait for the library to load, then try repair again. If it keeps happening, keep your backup and ask for help.

## A file is open in another program

Use these steps when the code is `DEVICE_FILE_LOCKED`. `DEVICE_FILE_LOCKED` and `DEVICE_FILE_READ_ONLY` can come from playlist writes as well as from adding, replacing, or removing songs. This can also happen while Recover is finishing a save.

Another program, such as File Explorer, a media player, or antivirus, has a file on the Walkman open. Walkman Bridge stopped and nothing was changed. Close that program and try the change again. If Walkman Bridge shows a file location on the Walkman, that is the open file.

If this happened while recovering an interrupted save, the save was already committed and only needs finishing, so close the program and run Recover again.

## A song file is read-only

Use these steps when the code is `DEVICE_FILE_READ_ONLY`.

A song file on the Walkman is marked read-only, so Walkman Bridge stopped before changing anything. Clear the read-only setting on that file. In Windows, right-click it, choose Properties, and untick Read-only, then try the change again. If Walkman Bridge shows a file location on the Walkman, that is the read-only file.

If this happened while recovering an interrupted save, the save was already committed and only needs finishing, so clear the read-only setting and run Recover again.

## The rename safety check

Before it changes a song, Walkman Bridge briefly renames that file to a name ending in `.jsymphonic-probe`, then renames it back. That checks the file is not in use. Do not rename or delete the song file or the `.jsymphonic-probe` copy yourself. If Walkman Bridge shows a file location, that is the song. If it shows a renamed-copy location, that is the temporary name.

## A safety check could not restore a song

Use these steps when the code is `DEVICE_PROBE_RESTORE_FAILED`.

A song file was renamed during that safety check and could not be renamed back.

1. Leave the Walkman connected.
2. Close any program that might be using the file, such as File Explorer, a media player, or antivirus.
3. Do not rename or delete files by hand.
4. Run Inspect, then Recover. Recover puts the renamed file back.

## Two copies of a song file differ

Use these steps when the code is `DEVICE_PROBE_CONFLICT`.

The song file and the renamed copy are both present, and they are not the same. Walkman Bridge will not change anything. Neither Recover nor the next save can resolve this. It clears only after the extra copy is removed with help.

1. Do not delete either file yourself.
2. Keep your backup.
3. Ask for help, and share both file locations if Walkman Bridge shows them.

There is no in-app button for this. `manual_help` is not a control in this version.

## A renamed copy is waiting

`DEVICE_PROBE_PENDING` is a warning, not a failure. Nothing is broken. An earlier safety check left a renamed copy of a song file. What happens next depends on the files.

On the next save or Recover, Walkman Bridge tidies it up automatically:

- If the original song file is missing, the copy is renamed back.
- If the original is there and identical, the extra copy is removed.
- Only if the two copies differ will that save stop with `DEVICE_PROBE_CONFLICT`. See **Two copies of a song file differ**.

## A save could not be fully undone

Use these steps when the code is `DEVICE_ROLLBACK_FAILED`.

The save failed, and Walkman Bridge could not undo it completely. The unfinished record is still on the Walkman, so Inspect and recover still apply. Stop making further playlist changes.

1. Leave the Walkman plugged into the same USB port.
2. Make a full backup and confirm it completed.
3. Run Inspect first, and read the result, then recover, using the steps under **Inspect, then recover**.

## Free a full playlist table

Use these steps when the code is `PLAYLIST_SLOTS_EXHAUSTED`.

The Walkman stores playlists in a table of 2048 slots. When every slot is in
use, another playlist cannot be saved. This is a full table. Free space on the
disk is a separate question.

1. Open **Manage music**, then the **Sony playlists** tab.
2. Delete playlists you no longer need, or move their songs into fewer
   playlists and then delete the ones you emptied. Deleting a playlist asks
   you to confirm. The songs stay on the Walkman. The confirmation tells you
   the music files will remain.
3. Save again after a slot is free.

The fix is freeing a slot with the playlist tools already in **Manage music**.
Inspect, recover, and repair do not add slots.

## A general failure

Use these steps when there is no code, or the code is not one of the nine
failure names or the warning in the table.

1. Leave the Walkman connected.
2. Make a full backup and confirm it completed.
3. Wait until Walkman Bridge is idle, then try the save once more.
4. If it fails again, keep the backup. When you ask for help, share the code
   if one was shown, or say that no code was shown.

## Commands for advanced users

JSymphonic HeadlessCli is the tool Walkman Bridge uses to write the Walkman.
Wait until the app is idle. Pass the drive the same way as the other HeadlessCli
commands (`--device` and the drive letter). Startup is described in
[PROTOCOL.md](../PROTOCOL.md).

- `playlist-recover --inspect` reads the unfinished record and changes nothing. It reports whether a commit marker is present. Run this first and read that result.
- `playlist-recover` rolls the change forward only when the journal has a commit marker. With no commit marker it discards the journal: the interrupted edit is lost, the Walkman keeps its earlier playlists, and you may need to redo that edit.
- `playlist-repair` removes playlist lines that name songs no longer on the Walkman.

Leave `.jsymphonic-playlist-transaction` on the device and let `playlist-recover` resolve it. When in-app controls arrive, they follow this same order.
