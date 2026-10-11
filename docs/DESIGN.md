# Red Lotus design decisions — 0.4.1

The approved September 2026 plan supersedes the earlier single-console design.

| Product | Composition | Main purpose |
| --- | --- | --- |
| Red Lotus Player | Concept A: sculpted retro player | Local listening and a persistent queue |
| Walkman Bridge | Retro playback with device views | Listening, device ledger, staging and device operations |

Both use the official outlined lotus from the supplied Red Lotus archive, maroon/ember colors, and bundled Orbitron, IBM Plex Sans and IBM Plex Mono. The supplied HTML export is a layout reference only. Its unpacking wrapper, external loaders and fixture data are never executed or imported.

## Shared controls

The maintained React application selects the product through the isolated Electron preload. Player has an inset display, tactile playback/volume controls and collapsible queue/equalizer panels; the equalizer starts collapsed. Bridge reuses the same tactile retro playback deck and separates Listening, Walkman and Transfer into contextual views. Both products keep essential transport and Manage music in the footer. Queue secondary actions and job details expand on demand. Playback and transfer actions have separate labels and locations.

Both windows normally resize to a minimum 640 × 560, bounded by the active display's available work area. The shell fits the viewport, its workspace scrolls, and dialogs have bounded scrolling bodies. Scaling through 200%, including the 320 CSS-pixel effective width, is the automated Electron zoom matrix: `npm run test:zoom-matrix` (`frontend/tests/zoom-matrix.cjs`, launched by `node --test scripts/zoom-matrix.test.cjs`). The run applies `webContents.setZoomFactor` at 100%, 125%, 150% and 200% for Red Lotus Player and Walkman Bridge at 640×560, 720×560, 900×700, 1140×860, 1380×860, 1920×640 and 800×1200. A cell fails when the shell overflows horizontally, a visible control cannot be reached, the shell is taller than the viewport, or the Manage music dialog leaves the viewport. 640×560 at 200% is the 320 CSS-pixel cell. Linux runs that command under xvfb; Windows launches Electron directly. The same command is what a CI job should run. Host OS display DPI stays at the session value already in effect; the matrix records `screen.getPrimaryDisplay().scaleFactor` and applies only `webContents.setZoomFactor`. Native titlebar buttons minimize, maximize/restore and close. Inputs have keyboard operation, visible focus and accessible names. Reduced-motion preferences disable decorative motion. Spectrum and waveform modes use actual Web Audio analyser samples.

Bridge Listening, Walkman and Transfer viewport fit, dialog bounds, and a renderer boot with no console errors are checked headlessly by `frontend/tests/walkman-smoke.test.mjs`. Operator hardware, clean-guest, listening, and the Electron zoom/audio harnesses stay separate; see [packaging/README-WINDOWS.md](../packaging/README-WINDOWS.md).

Manage music groups Files, Playlists and Bridge-only Sony playlists in a dedicated dialog. Search and sort find managed copies; details edit without changing originals. Local playlists define ordered playback scope and can stage their members for transfer. Sony playlists edit the native device database and use membership from the current device ledger. Removing a playlist or member keeps its audio; removing managed files requires confirmation. Device mutations remain unavailable while incompatible work is active.

The playback hook owns transport intent, seek, volume, queue advance and persistent session state. Restoration is paused. The optional ten-band equalizer defaults to bypass. Conservative headroom subtracts positive band boosts; loudness matching trims measured loud sources toward -18 LUFS and never boosts quiet sources. The optional compressor and final ceiling limit peaks. These controls affect playback only, never the transfer MP3.

## Data and security states

Queue and job rows use stable media IDs and structured backend states. Filenames and metadata render as React text. Scanning, awaiting permission, cleared, blocked, failed, interrupted and uncertain device outcomes are explicit. Missing or disabled Defender never means cleared. There is no scan-off switch.

The player leases managed media before loading it and releases it on Stop/removal. Failed scans cannot reach decoders or consumers. Source playback preserves its existing quality; unsupported codecs can request a cleared FLAC compatibility derivative. Bridge transfers a separate cleared 192 kbps, 44.1 kHz stereo MP3.

## Follow-up

Concepts B and D, AI restoration, automatic upsampling, streaming-service playback, folder watching and a full indexed music library remain outside this version. Public single-track link import is supported as documented in README.md. Current 0.4.1 acceptance is in BRANDING-SANDBOX-0.4.1.md. Management and layout evidence for 0.4.0 is in MANAGEMENT-0.4.0.md. Metadata, artwork, and link-import evidence is in MEDIA-UPGRADE-0.3.0.md. The 0.2.0 verification log is archived at archive/IMPLEMENTATION-PROGRESS.md.
