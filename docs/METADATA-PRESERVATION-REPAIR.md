# Metadata preservation repair

Synthetic tagged files reproduced two independent loss boundaries:

1. The analyzer exported only global FFmetadata. Vorbis/Opus stored their tags on the first audio stream, so the managed record fell back to its filename and unknown artist/album. Explicit transfer tags then carried those fallbacks into the MP3.
2. JSymphonic trimmed zero bytes before decoding UTF-16BE OMA fields. An artist ending in `\u4e00` (Japanese "one") was correct in MP3 and raw OMA bytes but became a replacement character when Java reread it. The companion engine repair trims complete UTF-16 NUL pairs, including in the original-path database reader.

The analyzer now explicitly exports the first audio stream along with global tags. Nonblank globals take precedence; cover-image streams, other audio streams and chapter tags cannot replace recording metadata. The bounded escaped FFmetadata parser remains separate from diagnostics. Album artist and disc fields, including common embedded aliases and available provider values, survive normalization and MP3 generation.

Metadata policy 3 reanalyzes retained sources and regenerates stale transfer derivatives during normal processing. Provider hints supplement embedded tags; saved manual edits take precedence. It cannot reconstruct tags absent from both the retained source and saved provider hints. Existing originals are never rewritten, and this change does not initiate a transfer or scan at startup.

| Information | Managed record / transfer MP3 | Java OMA / Sony database |
| --- | --- | --- |
| Title, artist, album, genre | Preserved, bounded to 512 characters | Existing engine fields; legacy text limits apply |
| Japanese / Unicode | UTF-8 metadata and ID3 text | UTF-16BE; companion fix preserves characters whose final byte is zero |
| Date / year | Valid date plus year | Year |
| Track / total | Preserved as number or number/total | Track only; existing writer uses modulo 100 |
| Album artist, disc / total | Preserved | Not represented by the existing engine |
| Embedded artwork | Original retained; separately cleared desktop JPEG | No automatic jacket-picture write; firmware display unverified |

## Verification scope

The regressions generate their own audio; no provider download or actual library/device is used. Real FFmpeg tests cover MP3, M4A, FLAC, Ogg and Opus with Japanese text, album artist, full date, track/total and disc/total. Real authenticated API/Java tests inspect staged ID3 frames, OMA frames and Sony database UTF-16 bytes, including policy-2 cache recovery and manual overrides. Existing artwork tests check bounded extraction and authenticated, hash-bound serving.

The scanner is injected only at the existing test boundary. These checks do not claim real Defender, physical Walkman playback or firmware display verification. New source and output consumption still require production clearance and the existing leases/coordinator. Installed software remains unchanged while these repair PRs are reviewed.
