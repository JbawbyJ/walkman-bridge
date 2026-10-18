# Listening checklist — Walkman Bridge

Human check by ear after a cleared transfer to a physical Walkman. Desktop playback is included only to confirm processing stays off the device file. A passing mock, Java, or API run is not a pass.

Read the version from the title bar. Source at this revision reports **0.4.1** (`package.json`, `backend/application.py`). If the title bar differs, record it; do not assume these steps match that build. The documented physical target is an NW-S705F. Other models are unverified here.

Before the first write of the session, make a full device backup and wait until the app confirms a saved backup. Originals outside the app stay untouched. Transfer is a separate 192 kbps, 44.1 kHz stereo MP3. This build does not implement gapless playback, crossfade, or Sony jacket pictures.

| Field | Entry |
| --- | --- |
| Tester | |
| Date | |
| Title-bar version | |
| Device model | |

## Setup

- [ ] The app confirmed a completed backup path before this session’s first device write.
  - **Result:** pass / fail
  - **Notes:**
- [ ] The transfer job finished done. The new track is on the device ledger. It is not interrupted, unknown, or **Verify device**.
  - **Result:** pass / fail
  - **Notes:**
- [ ] The original file outside the app is unchanged.
  - **Result:** pass / fail
  - **Notes:**

## On-device playback

- [ ] The transferred track starts, plays through, and ends. No dropouts, stuck loops, or silence where the source has audio.
  - **Result:** pass / fail
  - **Notes:**
- [ ] A stereo source is audible in both channels (headphones or a known stereo passage).
  - **Result:** pass / fail
  - **Notes:**
- [ ] Tracks transferred together play in the staged selection order.
  - **Result:** pass / fail
  - **Notes:**

## Track boundaries

Sequential playback and the separate MP3 encode can leave a short gap. Gapless playback is not a feature of this build.

- [ ] The next track is the following staged track. It does not skip or repeat. Desktop shuffle and repeat do not rewrite the device.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Audio from the previous track does not continue under the next track.
  - **Result:** pass / fail
  - **Notes:**
- [ ] A short gap at the boundary is a pass. A wrong track, a skip, or overlapping audio is a fail.
  - **Result:** pass / fail
  - **Notes:**

## Levels and desktop DSP

The equalizer, loudness match, and output limiter default off. They affect desktop playback only. Loudness match trims loud material toward -18 LUFS and does not boost quiet material. Positive band boosts reduce headroom. Presets are Flat, Warm, Presence, and Late night.

- [ ] With desktop processing left bypassed, Walkman level is even across the transferred tracks and is not obviously clipped or one-sided next to the source.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Turn desktop processing on, then play the same track on the Walkman. The device copy does not pick up that EQ, loudness match, or limiter.
  - **Result:** pass / fail
  - **Notes:**

## Metadata and artwork

Transfer writes title, artist, album, and, when present, genre, year, and track number. A missing title becomes the original filename. A missing artist or album is stored as `Unknown artist` or `Unknown album`. Desktop art is a cleared embedded JPEG, or the lotus if that fails. Firmware text length is unverified; record truncation instead of calling a shortened prefix a full-length pass.

- [ ] On the Walkman, title, artist, and album match Manage music, or a shorter prefix of those strings. A different work, or a blank where the app has text, fails.
  - **Result:** pass / fail
  - **Notes:**
- [ ] A track saved with no artist and album shows the unknown fallbacks, not another track’s tags.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Genre, year, or track number, when saved in the app, is either visible on the device or written down as absent. Absence is a note. Firmware field limits are unverified in this repo.
  - **Result:** pass / fail
  - **Notes:**
- [ ] Desktop now-playing shows the embedded cover or the lotus. No cover on the Walkman is expected. Do not fail the device for a missing jacket picture.
  - **Result:** pass / fail
  - **Notes:**
