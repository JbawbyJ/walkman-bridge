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
code. If there is no code, or the code is not one of the three names below,
treat it as a general failure. The longer message is not a code. Do not decide
the fix by reading it.

| Code | In plain words | What to do |
| --- | --- | --- |
| `PLAYLIST_JOURNAL_PENDING` | A previous playlist save was cut off. An unfinished record is still on the Walkman. | Inspect, then recover. |
| `PLAYLIST_REF_MISSING` | A playlist still names songs that are no longer on the Walkman. | Run playlist repair. |
| `PLAYLIST_SLOTS_EXHAUSTED` | The Walkman's playlist table is full (2048 slots). | Delete or combine playlists. |
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

- Leave the Walkman plugged in until a transfer or playlist save has finished.
  Unplugging in the middle is how the unfinished record gets left behind.
- Leave `.jsymphonic-playlist-transaction` where it is. Deleting that file
  yourself in File Explorer can hide a save that had already committed, or
  leave the playlist tables in a state the app can no longer explain.
- Wait for inspect and recover when the code is `PLAYLIST_JOURNAL_PENDING`.
  Saving again will keep stopping until the unfinished record is resolved.
- Use the code, or the general-failure steps when the code is missing or
  unfamiliar. Words inside the message are not a substitute for the code.

## Back up the Walkman first

Make a full backup and confirm it completed before you recover, repair, or
delete playlists to free slots. Walkman Bridge keeps a backup only after the
copy has been checked. If a backup is interrupted, the app shows **Verify
device state** and does not try that write again on its own.

A backup matters most when you are unsure whether the last playlist save
finished, and before the first write to a physical Walkman. Repair and recover
change playlist tables. They do not delete songs on the Walkman, and they do
not delete the original files on your PC. The backup is still the way back if
the result is not what you expected.

## Inspect, then recover

Use these steps when the code is `PLAYLIST_JOURNAL_PENDING`.

1. Leave the Walkman plugged into the same USB port. Wait until Walkman Bridge
   is not transferring, scanning, or saving.
2. Make a full backup and confirm it completed.
3. Inspect the unfinished record. Inspect only reads. It reports what is
   waiting. It does not change playlists or music.
4. Recover after you have read that report. If the record already contains a
   durable commit mark, recover finishes that save. If it does not, recover
   discards the record and leaves your playlists as they were before the
   interrupted save.
5. Try the playlist change again only after recover has finished.

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

Use these steps when there is no code, or the code is not one of the three
names in the table.

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

- `playlist-recover --inspect` reads the unfinished record and changes nothing. Run this first.
- `playlist-recover` finishes a save that already committed, or discards a record that never committed.
- `playlist-repair` removes playlist lines that name songs no longer on the Walkman.

Leave `.jsymphonic-playlist-transaction` on the device and let `playlist-recover` resolve it. When in-app controls arrive, they follow this same order.
