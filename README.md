# Red Lotus Player and Walkman Bridge

Two Windows desktop applications share local playback, a persistent queue and mandatory Microsoft Defender scanning.

| Application | Interface | Additional capabilities |
| --- | --- | --- |
| Red Lotus Player | Sculpted retro music player | Ten-band equalizer, real audio visualization, source-quality playback |
| Walkman Bridge | Retro playback with device views | Walkman library, verified backup, staging, transfer and deletion |

Install the matching versioned NSIS executable from dist_electron/player or dist_electron/bridge. Both contain Python, ffmpeg, a restricted Deno runtime for link importing, the self-contained .NET scanner helper, Electron and local fonts. Player contains no Java, JSymphonic or device module. Build instructions and installer smoke checks are in [packaging/README-WINDOWS.md](packaging/README-WINDOWS.md). These are local unsigned builds; no release has been published.

## Playback and scanning

Import copies audio into the application's private managed cache; originals remain untouched. Defender must explicitly clear unchanged bytes before decoding, analysis, playback or conversion. When ordinary scanning requires elevation, Windows UAC launches a narrow helper for that batch. The UI and backend stay unprivileged. Missing/disabled Defender, cancelled permission, errors, timeouts and ambiguous results block consumption.

Supported source audio plays without a lossy re-encode. Codec compatibility can use a lossless FLAC derivative. Walkman transfer generates a separate 192 kbps, 44.1 kHz stereo MP3. Derivatives require their own clearance. Enhancement defaults off and changes playback only. Loudness matching uses measured EBU R128 levels; it does not reconstruct lost audio information.

Queue order, track, position, volume, shuffle and repeat persist in SQLite; startup restores paused. Queue removal waits for playback/processing leases before deleting managed bytes. Limits are 500 MiB per file, 2 GiB/200 files per import, one active import batch, and a 10 GiB cache.

## Music management and layout (0.4.1)

Current deployment checks and the remaining clean-VM/UAC acceptance are tracked in [docs/BRANDING-SANDBOX-0.4.1.md](docs/BRANDING-SANDBOX-0.4.1.md). Installer instructions are in [packaging/DEPLOYMENT-0.4.1.md](packaging/DEPLOYMENT-0.4.1.md).

**Manage music** opens searchable managed files, editable music details, and saved playlists. Create, rename, reorder and delete playlists; play them in their saved order or stage their music for Walkman transfer. Removing playlist membership keeps the audio. Removing managed files requires confirmation, prunes playlist membership and leaves the original files untouched. Metadata edits regenerate the transfer MP3 on the next transfer without rewriting the source audio.

Bridge also has a **Sony playlists** tab for creating and editing the Walkman's native playlist tables, including ordered membership in tracks already on the device. Transfer local music first, then add those device tracks to a Sony playlist. Native edits require a fresh device snapshot and run through the same volume-bound coordinator and Java engine as transfers. Playlist deletion never deletes the songs. The format and preservation limits are documented in the bundled JSymphonic source's `NATIVE-PLAYLISTS.md`.

Bridge separates **Listening**, **Walkman** and **Transfer** views. Both products keep essential transport controls visible and move optional equalizer, queue actions and job details behind expandable controls. Layouts scroll inside the window, dialogs fit small viewports, and the initial Electron window fits the active display work area. Both products use the retro playback deck and locally bundled fonts. Version 0.4.1 removes the former edition branding while preserving installation IDs, stored music and playlists.

## Music details, artwork and links (0.3.0)

Embedded title, artist, album, genre, date/year and track number are read after source clearance and written explicitly into the Walkman MP3. Missing titles use the original filename; absent artist/album values remain visibly unknown. Existing cached transfer files are regenerated when they lack the corrected metadata policy. This version does not look up missing local tags online. The legacy device engine limits text length and track numbers; hardware display still needs operator verification.

Embedded cover art appears in the desktop player after bounded JPEG extraction and its own Defender clearance. Failed or unavailable artwork falls back to the official lotus. Writing Sony jacket-picture databases is not implemented, so desktop cover display does not imply cover display on the Walkman.

**Import link** accepts one public YouTube video or SoundCloud track. Acquisition runs in a bounded child process with pinned HTTPS destinations and a ten-minute deadline; scanning must finish before any audio decoding, playback or conversion. Available provider music fields supplement embedded tags, without treating an uploader/channel name as a verified artist. Downloads remain in the managed queue after transfer. Use material you own or have permission to download.

Importing a provider's entire playlist, authenticated/private/paywalled streams, DRM, live streams and other providers are outside this version. A provider must offer a supported direct audio format; HLS-only SoundCloud tracks are reported unavailable. Online services can change their delivery formats, so some public links may fail with an actionable import error.

## Device reliability

JSymphonic HeadlessCli is the only OMGAUDIO writer. Backup, database reads, addition and deletion share a device coordinator and revalidate Windows volume identity. Deletion requires the current track-list ETag. Backups publish only after a complete verified copy. Interrupted or ambiguous writes display **Verify device state** and are never automatically retried.

**Before a physical NW-S705F write, make a full device backup and verify it.** Mock-device integration proves the software round trip; firmware playback still requires an operator-controlled hardware test. Do not infer physical playback from API or Java success.

Each product keeps its database, managed cache, backups and logs below its own %LOCALAPPDATA% directory: Red Lotus Player or Walkman Bridge. Existing terminal job history is migrated additively. Closing stops playback, saves state and drains admitted work. Failed busy probes mean unknown status; no timer kills an active device operation.

## Development and release

Use Windows x64, Node 24+, build Python 3.11+ and .NET SDK 10.0.400. Bridge also needs the pinned JDK/JSymphonic source described in the packaging guide. After dependency setup, run npm run electron for Bridge or npx electron . --product=player for Player.

Electron starts the Python factory using an authenticated, signed dynamic-port announcement. It serves the built React application through that backend. Production uses no Vite server, external fonts or export loaders. Direct legacy browser launchers do not establish this authentication boundary and are not supported product entry points.

The backend API requires a per-launch capability and exact permitted origin; renderer requests use an HttpOnly/SameSite cookie. Electron restricts outgoing requests to the exact backend origin including its port. Native scan and shutdown routes require the main-process capability.

See [STRUCTURE.md](STRUCTURE.md), [docs/DESIGN.md](docs/DESIGN.md), [docs/IMPLEMENTATION-CONTRACT.md](docs/IMPLEMENTATION-CONTRACT.md) and [docs/IMPLEMENTATION-PROGRESS.md](docs/IMPLEMENTATION-PROGRESS.md) for modules, decisions, interfaces and verification evidence.

Application code is MIT. Bundled dependencies retain their own notices, including GPL components. Bridge includes the companion JSymphonic source snapshot. Review [packaging/NOTICES.md](packaging/NOTICES.md) before distribution; the complete ffmpeg corresponding-source package remains a publishing prerequisite. Local builds are not a public release or a claim that hardware, listening and clean-machine acceptance have all passed.
