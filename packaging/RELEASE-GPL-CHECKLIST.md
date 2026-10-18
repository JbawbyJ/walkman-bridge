# Public-release corresponding-source checklist

This is a gate for a future public release. It does not say that any license
obligation is already met. Local unsigned installers are not a public release.
Nothing in this file authorizes publication. `ship=false`.

A URL recorded here is a pointer for the person preparing that release. It is
not a written offer and it does not ship the source with a binary.

## FFmpeg

The Gyan 9.0.1 essentials binary stays pinned under `ffmpeg` in
`runtime-lock.json`. Staging still copies `ffmpeg.exe`, `LICENSE`, and
`README.txt` only. The official source tarball is recorded as `ffmpeg_source`
and is not vendored in git.

On 2026-10-10 the lock URL
`https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.1-essentials_build.zip`
returned HTTP 404. The same filename from the Gyan GitHub release
`https://github.com/GyanD/codexffmpeg/releases/download/9.0.1/ffmpeg-9.0.1-essentials_build.zip`
matched the locked binary sha256
`fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9`
(SHA-256 of the zip bytes, `hashlib.sha256` in 1 MiB reads, confirmed with
`sha256sum`). That match identifies the README inside the zip. It is not a
reason to commit the binary.

`README.txt` in that zip says:

`Source Code: https://github.com/FFmpeg/FFmpeg/commit/bf1b838f2a`

That abbreviated commit is
`bf1b838f2ab88b4f8fd83443325c782ea0e0f7fa`.
The annotated tag `n9.0.1` peels to the same commit. The official release
tarball `https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz` has `VERSION` equal
to `9.0.1` and sha256
`cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635`
(same hashing method). `ffmpeg_source` records that tarball.

Before any public redistribution of this binary, ship that tarball with the
binary or make a written offer that the applicable license accepts.

`packaging/verify-release.py` refuses a release check while
`ffmpeg_source.sha256` is missing or contains `TODO`. A 64-character hex pin
only means the digest was recorded. It does not mean the tarball was shipped.

## Libraries named by the Gyan README

The verified `README.txt` "External libraries" section names:

`avisynth`, `bzlib`, `cairo`, `gmp`, `gnutls`, `iconv`, `libaom`, `libass`,
`libfontconfig`, `libfreetype`, `libfribidi`, `libgme`, `libgsm`,
`libharfbuzz`, `libmp3lame`, `libopencore_amrnb`, `libopencore_amrwb`,
`libopenjpeg`, `libopenmpt`, `libopus`, `librubberband`, `libspeex`, `libsrt`,
`libssh`, `libtheora`, `libvidstab`, `libvmaf`, `libvo_amrwbenc`, `libvorbis`,
`libvpx`, `libwebp`, `libx264`, `libx265`, `libxml2`, `libxvid`, `libzimg`,
`libzmq`, `lzma`, `mediafoundation`, `openal`, `sdl2`, `zlib`.

The hardware-acceleration section names:

`amf`, `cuda`, `cuda_llvm`, `cuvid`, `d3d11va`, `d3d12va`, `dxva2`,
`ffnvcodec`, `libmfx`, `libvpl`, `nvdec`, `nvenc`, `vaapi`.

For each of those components that is GPL or LGPL, the corresponding source for
the version actually linked into this essentials build has to ship or be
offered. This checklist does not classify those licenses and does not pin
per-library versions. The README build-configuration section is the input for
that work.

## Gyan build scripts and configuration

The `README.txt` shipped beside `ffmpeg.exe` is the configuration summary for
this binary. The scripts and dependency versions Gyan used to produce
`ffmpeg-9.0.1-essentials_build` also have to be offered with a public binary.
This repository does not contain those scripts.

## JSymphonic

A public Bridge build has to include the pinned archive
`sources/jsymphonic-nightops-0.4.0-src.zip`. Its sha256 lives at
`jsymphonic.sha256` in `runtime-lock.json`. Staging copies that archive only
after the hash matches, and fails closed on a mismatch. Player builds must not
include it.

## Temurin

Bridge can bundle Eclipse Temurin 21.0.12+8, which is GPLv2 with the Classpath
Exception. Source pointer, not the source itself:

https://github.com/adoptium/jdk21u/releases/tag/jdk-21.0.12+8

Build-script pointer already cited in `NOTICES.md`:

https://github.com/adoptium/temurin-build

A public Bridge build still has to offer the corresponding Temurin source.
This pointer is not that offer.

## Written offer

A written offer to provide corresponding source, for the period the applicable
GPL version requires, is an alternative to shipping the source beside the
binary. This document is neither the source nor that offer.

## Deno

`NOTICES.md` already says full Rust and native dependency notice
reconciliation for Deno 2.9.5 is a publishing prerequisite and points at
`notices/deno/README.md`. That reconciliation is still open. The
`notices/deno/` tree is not in this checkout.

## Not a compliance claim

Do not describe a build, tag, or installer as license-compliant because this
checklist exists.
