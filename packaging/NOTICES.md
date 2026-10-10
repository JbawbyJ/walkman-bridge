# Red Lotus bundled components

The application source is MIT licensed; `LICENSE-APPLICATION.txt` contains that license.
Red Lotus branding is supplied by the project owner and is not relicensed here.
The following components retain their own licenses. Each installer carries a
machine-readable `runtime-manifest.json` listing the actual bundled file hashes.

| Component | License / bundled notice | Source |
| --- | --- | --- |
| Electron, Chromium, Node.js | Installed `LICENSE.electron.txt` and `LICENSES.chromium.html` | https://github.com/electron/electron/tree/v44.2.0 |
| CPython 3.13.15 | Python PSF license, `python/LICENSE.txt` | https://www.python.org/downloads/release/python-31315/ |
| Python packages | Individual `python/Lib/site-packages/*.dist-info` license and metadata files | Locked PyPI versions in `python-wheels.lock.json` |
| yt-dlp 2026.8.19 and yt-dlp-ejs 0.8.0 | Wheel license files and metadata under `python/Lib/site-packages/` | https://github.com/yt-dlp/yt-dlp and https://github.com/yt-dlp/ejs |
| Deno 2.9.5 | MIT, `notices/deno/LICENSE.md`; selected pinned component licenses and provenance in `notices/deno/`. Full Rust/native dependency notice reconciliation remains a publishing prerequisite; see `notices/deno/README.md`. | https://github.com/denoland/deno/tree/v2.9.5 |
| FFmpeg 9.0.1 Gyan essentials | GPLv3, `ffmpeg/LICENSE` and `ffmpeg/README.txt`; README lists linked library versions and build configuration | https://github.com/FFmpeg/FFmpeg/commit/bf1b838f2a and https://www.gyan.dev/ffmpeg/builds/ |
| .NET 10 scanner runtime | `notices/dotnet/LICENSE.txt` and `ThirdPartyNotices.txt` | https://github.com/dotnet/runtime |
| React and React DOM | MIT, `notices/frontend/` | https://github.com/facebook/react |
| IBM Plex Sans, IBM Plex Mono, Orbitron | SIL Open Font License 1.1, `notices/fonts/` | https://github.com/google/fonts |
| Bridge only: Eclipse Temurin 21.0.12+8 | GPLv2 with Classpath Exception; `jre/legal/` and `notices/java/NOTICE` | https://github.com/adoptium/jdk21u and https://github.com/adoptium/temurin-build |
| Bridge only: JSymphonic headless fork | GPLv3, `notices/jsymphonic/LICENSE`; matching project source snapshot in `sources/jsymphonic.zip` | https://github.com/JbawbyJ/jsymphonic |

These local unsigned installer artifacts are not published by the build. Before
redistributing GPL components, provide the complete corresponding source and
build information required by their licenses, including the linked FFmpeg
libraries. Source links and this notice alone are not a claim that every
redistribution obligation has been fulfilled.
