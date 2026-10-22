# Link and MP4 metadata / artwork

Local update 0.4.2 preserves supplied title, artist, album and genre through
managed import, playback conversion and MP3 preparation for Sony transfer.
Manual edits take precedence over embedded audio tags, then explicit provider
fields. The editor shows each field's source. Missing artist/album labels are
display fallbacks only; missing or explicitly cleared fields are not invented
in transfer tags. A filename title remains labeled as a filename fallback.
Uploader/channel names are not substituted for artists.

MP4 imports retain their container extension and extract audio. Only an embedded
attached cover is used as cover art; ordinary video frames are not treated as
album artwork. YouTube thumbnails and SoundCloud track artwork retain their
provider label. SoundCloud avatars are excluded. Artwork is optional: unavailable
or undecodable artwork does not claim to have been retained.

Provider image downloads share the HTTPS/domain/public-address, redirect, size
and time restrictions of link imports. Opaque JPEG/PNG/WebP images are retained
inside managed storage, scanned before decoding, normalized to a bounded JPEG,
then scanned again before display or conversion. Original files are unchanged.
The normalized cover is embedded in prepared FLAC/MP3 files. Changes to tags or
artwork invalidate cached derivatives. Missing cached files recover from retained
sources. A threat verdict on artwork refuses derivatives that contain it.

## Sony behavior and limits

The [Sony NW-S705F family operation guide](https://www.sony.com/electronics/support/res/manuals/2887/28877431M.pdf)
documents title/artist/album display (p.11), song/artist/album/genre search
(pp.21–24), album jacket search (p.26), and album jacket display with Jacket Mode
(p.44). Playlist jacket pictures do not appear on the player. Character support
has firmware/language limits (pp.88,106).

The engine preserves text in OMA and native database serialization, including
genre readback. Native text fields retain the existing 60 UTF-16-unit limit
without splitting surrogate pairs or modifying the source metadata. Exported
MP3 text tags are Unicode ID3v2.3; untitled exports use distinct device-ID
filenames without creating fake title tags.

**Native jacket writing is not implemented or verified by this update.** Keeping
APIC artwork in the prepared MP3 is not evidence that the Sony firmware displays
it. The existing engine skips MP3 artwork during its OMA payload copy. Existing
nonzero or unsupported PICP/PIC0 associations cause add/delete/rebuild to refuse
with `DEVICE_ARTWORK_REBUILD_UNSUPPORTED`, rather than silently clearing jackets.
The legacy Java GUI export-only lifecycle also rebuilds the device database, so
it encounters this guard on devices with jackets. Do not remove artwork to work
around that refusal.

## Verification and release provenance

Fixture tests use real FFmpeg, the rebuilt Java engine, authenticated API routes,
independent ID3/EA3 parsing and temporary Sony directories. The scanner is
injected for these fixtures; they do not establish live Defender, audibility,
device glyph rendering or native jacket display. Link transport and scan gates
have separate negative tests. Frontend interactions and Electron fixtures cover
metadata editing and source labels.

The runtime lock identifies the complete local engine source snapshot by its
archive SHA-256 and revision label. Its recorded Git commit is the base, not a
claim that local edits were published. The final reviewed JAR hash is pinned
separately. No GitHub publication or merge is part of this local update.
