# Red Lotus Audio 0.4.1 — Windows x64

This local deployment bundle contains two independently installable apps:

- **Red Lotus Player**: retro playback, enhancement, music details, managed
  files, saved playlists and public single-track link import.
- **Walkman Bridge**: the same playback features plus Sony library, verified
  backup, transfer/deletion and native Sony playlists.

## Install or upgrade

1. Close the running app and allow its work to finish.
2. Run the matching `Setup-0.4.1-x64.exe`. Either or both apps may be installed.
3. Use the normal per-user installation. Required runtimes, fonts and the
   Defender helper are bundled. Player needs neither Java nor a Walkman.
4. Open the app from its updated shortcut. Existing installation IDs and data
   locations are preserved. Keep `%LOCALAPPDATA%\Red Lotus Player` and
   `%LOCALAPPDATA%\Walkman Bridge` during an update.

Check the installer hashes against `SHA256SUMS.txt`:

```powershell
Get-FileHash -Algorithm SHA256 .\*Setup-0.4.1-x64.exe
```

The installers are unsigned. Windows Application Control may reject them.
The apps do not change Windows security policy. Defender must be enabled and
able to scan. Missing, failed or declined clearance blocks playback and transfer.

## Changes in 0.4.1

Current app headers, window titles, fallback artwork, executable, installer,
shortcut, log and helper names use Red Lotus Player or Walkman Bridge branding.
Both listening views now use the shared retro player. Bridge retains separate
Listening, Walkman and Transfer views, persistent transport, expandable details
and the resizing/clutter fixes from 0.4.0.

**Manage music** provides searchable managed files, editable music details and
saved playlists. Removing a playlist keeps its songs. Removing managed files
requires confirmation and leaves originals untouched. Music detail changes
apply to the next transfer copy.

In Bridge, **Sony playlists** edits native ordered playlists from tracks already
on the device. Transfer local audio first, then add device tracks to a playlist.
Playlist deletion keeps the audio. Make a complete backup before device changes
and leave the Walkman connected while writes are active.

Single public YouTube/SoundCloud tracks and desktop embedded artwork remain
supported within the documented acquisition limits. Whole provider playlists,
private/DRM content and Sony jacket-picture database writing are not included.

## Acceptance status

See `VERIFICATION.json` for the final installer identities and checks. The
current packages passed resource/source parity, backend and frontend tests,
actual Electron playback/management and 196 resize cases. The packaged media
services passed real Defender import, metadata and local playlist editing,
restart recovery and managed-file removal. Bridge passed transfer and native
playlist CRUD on a private copy of a verified Sony backup.

The physical Sony's existing test playlist was renamed to **Red Lotus Check**
after a fresh verified backup. Only its name record changed. Firmware-menu
display and listening still require the owner's check.

Clean-environment acceptance remains open. The exact 0.4.1 Sandbox run was
blocked by its default PowerShell execution policy before installers ran;
Defender's service was stopped. No policy override or scanner bypass was used.
A dedicated Hyper-V VM is prepared and powered off, pending registered Windows
evaluation media, normal setup and verification of working Defender. A separate
live UAC helper attempt returned `elevation_cancelled`; it granted no clearance.
Successful ordinary Defender scans do not substitute for elevated-helper
acceptance. These conditions are recorded as pending/blocked, never as passes.

This bundle is for local deployment review. Public distribution remains a
separate release action. Review `THIRD-PARTY-NOTICES.md` and complete its
outstanding corresponding-source/notice requirements before publishing.
