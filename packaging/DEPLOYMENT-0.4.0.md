# Red Lotus / Night Ops 0.4.0 — Windows x64

This bundle contains two independently installable products:

- **Red Lotus Player** — listening, audio enhancement, music details, managed
  files, saved playlists and public single-track link importing.
- **Walkman Bridge — Night Ops** — the same playback features plus Sony library,
  verified backup, transfer/deletion and native Sony playlists.

## Install or update

1. Close either running app and let its outstanding work finish.
2. Run the matching `Setup-0.4.0-x64.exe`. Install either product or both.
3. Choose the normal per-user installation. Python, ffmpeg, fonts and the scan
   helper are bundled. Bridge also bundles Java and JSymphonic; Player needs
   neither Java nor a Walkman.
4. Open the app from its shortcut. Each product retains its own managed music,
   database and settings under `%LOCALAPPDATA%\Red Lotus Player` or
   `%LOCALAPPDATA%\Walkman Bridge`. Do not remove those folders during an update.

Verify the installer hashes against `SHA256SUMS.txt` with PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 .\*Setup-0.4.0-x64.exe
```

These installers are unsigned. Managed Windows/App Control configurations may
block them. This build does not change Windows policy. Microsoft Defender must
be enabled and able to scan; unavailable, failed or declined scans block use.

## What's new

**Manage music** opens searchable/sortable files, editable music details and
saved playlists. Create, rename, reorder, add/remove tracks, play a playlist,
or stage its music for transfer. Metadata edits affect the next transfer copy;
your original audio files stay untouched. Removing a playlist keeps its songs.
Removing managed files asks for confirmation and removes their memberships.

In Bridge, **Sony playlists** edits the device's own playlist database. Transfer
local music first, refresh the Walkman, then choose its existing tracks for a
Sony playlist. Native playlist deletion removes only the playlist. Make a full
backup before device changes; don't unplug during a write.

The Bridge UI now separates Listening, Walkman and Transfer. Optional equalizer,
queue actions and job details expand on demand. Both apps keep playback controls
visible, fit their initial display work area and scroll correctly when resized.

Single public YouTube/SoundCloud track import and desktop artwork from 0.3.0
remain available. Whole provider playlists, private/DRM content and Sony jacket
picture database writing are not included.

## Verification and remaining acceptance

The final release passed 241 backend tests (one environment skip), 36 frontend
logic tests, 25 Electron logic tests, 28 Java tests, actual Electron management/
playback/lifecycle/DSP checks, and 196 window-size/scaling cases. Both installers
passed install/start/drain/uninstall on the build Windows host. The packaged
resource inventories and production source hashes were verified independently
of startup. Scoped independent Java/UI and backend reviews have no remaining
material findings.

Both packaged backends also passed real Defender imports, metadata and playlist
changes, restart restoration and file removal with only bundled runtimes on
System32-only PATH. Bridge passed native Unicode playlist and generated-track
transfer checks on a private copy of a verified Sony backup.

A backed-up physical Sony passed real Defender/ffmpeg/Java transfer, metadata
readback and native playlist CRUD; all original audio hashes were preserved.
Firmware-menu display and listening still require the owner's check. Earlier
clean Windows Sandbox attempts were blocked by Application Control; no clean
Sandbox installation success is claimed for this unsigned build.

This is a local deployment candidate, with no automatic publishing or updating.
Public distribution is a separate release action. Review `THIRD-PARTY-NOTICES.md`
and complete its outstanding corresponding-source/notice requirements first.
