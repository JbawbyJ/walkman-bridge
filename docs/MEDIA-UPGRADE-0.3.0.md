# Media upgrade 0.3.0

This extends the existing two Windows applications with reliable music details,
desktop embedded artwork and single-track public YouTube/SoundCloud importing.
It preserves the initial device/Defender/authentication requirements. The later
request for link importing supersedes the original exclusion of remote imports.

## Behavior

- Source tags are read only after Defender clearance, normalized as bounded text,
  and explicitly written to the transfer MP3. Title, artist, album, genre,
  date/year and track number survive the tested Java mock-device round trip.
- Untagged recordings use their original filenames for titles, with honest unknown
  artist/album fields. Cached transfer derivatives from the older metadata policy
  regenerate and receive fresh clearance. No online identification of untagged
  local music is implemented.
- Embedded artwork is decoded from cleared audio into a JPEG at most 512 pixels
  per dimension and 1 MiB. The JPEG needs its own clearance and authenticated
  endpoint before rendering. The official lotus remains the fallback. Sony
  jacket-picture database writing is outside this version.
- Both interfaces have an accessible Import link dialog. Public single-track
  YouTube/SoundCloud downloads share the existing queue, processing, playback,
  and Bridge transfer services. Provider music fields supplement embedded tags;
  uploader/channel names are not silently labeled as artists.
- Acquisition is isolated in a temporary Python worker with a Windows Job Object,
  fixed restricted Deno, bounded output/bytes and a ten-minute deadline. It rejects
  private-network destinations, unapproved redirects, external downloaders and
  all yt-dlp postprocessors, including implicit FFmpeg fixups before scanning.
- Downloads count toward cache/admission limits, participate in shutdown drain,
  clean up partial files, and recover interrupted jobs without replaying writes.

Playlists, private/account-required/paywalled content, DRM, live streams, other
providers and HLS-only SoundCloud delivery are unsupported. Public service format
changes can cause a supported-looking link to fail. Missing local tags are not
automatically looked up online. Legacy device text length/track-number limits
remain; mock results do not establish firmware display or playback.

## Executed checks

| Check | Evidence |
| --- | --- |
| Full backend | 217 passed; one Windows symlink-creation skip; real ffmpeg/Java mock tests enabled |
| Frontend, broker and packaging logic | 58 Node tests passed; four Python staging safety tests passed |
| Actual Electron | Renderer playback/seek/queue, link form/artwork/escaping, lifecycle/drain, and native DSP harnesses all passed |
| Live public API and real Defender | Both providers imported, source and lossless FLAC separately cleared, authenticated ranges passed; generated embedded artwork also separately cleared and served |
| Independent review | All reproduced findings repaired; 74 focused tests, 16 real ffmpeg/API/Java tests, and real Windows worker/descendant cleanup probes passed independently |
| Runtime advisory lookup | 21 exact runtime package versions checked against PyPI vulnerability records; none reported at verification time |
| Packaged live pipeline | Independent Player run with System32-only PATH: bundled Python/yt-dlp/EJS/Deno, both providers, real Defender, metadata, separately scanned FLAC and authenticated ranges passed; no developer toolchain or Java required |
| Package integrity | All 1,868 Bridge and 1,711 Player manifest entries verified; backend, frontend and helper match current source; Electron archives contain verified production entry points |
| Installation on this Windows host | Both 0.3.0 NSIS installers installed in isolated directories, launched authenticated Electron, drained and exited, then uninstalled; no product registration remains |

Evidence paths: `packaging/build/media-upgrade/live-link-api-proof.json`,
`metadata-device-proof.json`, `runtime-audit.json` in the same directory;
`frontend/test-output/media-import/result.json`; the existing renderer/lifecycle/DSP
evidence paths; and [the independent review](security/MEDIA-UPGRADE-REVIEW-2026-09-05.md).
The review records exact source hashes and distinguishes its manual independent
pass from specialized review tools that were unavailable.

## Review artifacts and remaining acceptance

The two installers are versioned 0.3.0; the previous 0.2.0 installers are retained.
Both contain pinned Python, yt-dlp/EJS, Deno, ffmpeg, the .NET helper and fonts.
Player continues to exclude Java, JSymphonic and device modules.

These are unsigned local review artifacts. The earlier Windows Sandbox Application
Control block and unavailable guest Defender remain unresolved; no policy was
weakened. Physical NW-S705F acceptance requires a full verified backup and a
human-controlled test. No physical device operation or public release occurred.
Signing, complete FFmpeg corresponding source and full Deno/native dependency
notice reconciliation remain publishing prerequisites.

Final installer artifacts (the old 0.2.0 artifacts remain available):

| Product | Filename | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Player | `dist_electron/player/Red-Lotus-Player-Setup-0.3.0-x64.exe` | 208169850 | `50e6c5a4cd02999582d6ff4e916b25053607d68f2676bc2dc0ce069b4e683892` |
| Bridge | `dist_electron/bridge/Walkman-Bridge-Night-Ops-Setup-0.3.0-x64.exe` | 246889653 | `64af2f5bc5ee2c9047ceb2532ecdc3b41b845c114d16a69b65ec69663959e470` |

Package proof: `packaging/build/media-upgrade/artifact-verification.json` and
`packaged-media-proof.json`. Installation results:
`packaging/build/install-smoke/bridge-ff8530e0f8e94b389ec6a557cf44f975/result.json`
and `packaging/build/install-smoke/player-349f088a87ee4cb4b0c7ab4d6042d310/result.json`.
The standalone packaged probe is `packaging/probe-packaged-media.py`; it is an
explicit operator test and is not shipped as a runtime entry point.
