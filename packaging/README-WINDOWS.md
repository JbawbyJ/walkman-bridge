# Windows product builds

`npm run dist` builds local, unsigned NSIS installers for **Walkman Bridge**
and **Red Lotus Player**, version 0.4.1. `npm run dist:bridge` and `npm run dist:player`
build one product. No command publishes or uploads a release.

Use Windows 10/11 x64, Node 24+, a build Python 3.11+, and .NET SDK 10.0.400.
The frontend build pins Vite 8.2.2 and its React plugin 6.1.1; React remains 18.3.1.
Bridge also needs the pinned JSymphonic source/JAR and Temurin JDK 21.0.12+8.
The workflow in `.github/workflows/windows-products.yml` provisions these tools
and runs the complete build and checks on a clean Windows runner.
The headless Java source is vendored in `packaging/sources` and checksum-locked;
CI extracts it using `packaging/provision-engine.py`, so private or unavailable
GitHub source URLs do not create a credential dependency.

For a prepared local checkout:

```powershell
npm ci
node node_modules/electron/install.js
npm --prefix frontend ci
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt -c packaging/python-runtime-constraints.txt -r backend/requirements-dev.txt
dotnet publish scanner-helper/ScannerHelper/ScannerHelper.csproj -c Release -r win-x64 --self-contained true -o scanner-helper/artifacts/win-x64
npm run dist
```

The default local Bridge source/JDK paths are the sibling `../jsymphonic` and
`../tools/jdk-21.0.12+8`. Pass `-JSymphonicSource`, `-Jar` and `-JdkHome` to
`packaging/build-products.ps1` to use other prepared paths. If no matching local
JDK is available, staging downloads the archive pinned in `runtime-lock.json`.
Build the headless fork using `mvn -B -f <source>/pom.xml package`, run its tests,
then explicitly provide its jar-with-dependencies artifact using `-Jar`.
For a reviewed engine update, create a new immutable source snapshot with
`python packaging/snapshot-engine.py ../jsymphonic --revision redlotus-<new-version>`.
Use a new revision label each time. The lock records the base commit separately
from the complete archive hash; a locally modified snapshot is not described as
the unchanged commit.

The build fetches exact CPython, FFmpeg, Deno and Python-wheel artifacts with SHA-256
verification. `-Offline` prevents runtime dependency downloads; Node dependencies,
Electron and electron-builder tool archives must already be provisioned/cached.
`-UnpackedOnly` produces a runnable installed-layout directory without NSIS.

Outputs are isolated under `dist_electron/bridge` and `dist_electron/player`.
Each contains an installer, its blockmap, `SHA256SUMS.txt`, and `win-unpacked`.
The two executables have separate app IDs, package names, shortcuts and product
manifests. The installed resources include their dependency notices and file-hash
inventory in `runtime-manifest.json`.

Both products bundle the isolated CPython 3.13.15 embeddable runtime, locked
site-packages (including yt-dlp and its bundled EJS solver), Deno 2.9.5, ffmpeg,
self-contained .NET scanner helper, backend source and built
frontend with local fonts. Player excludes the JRE, JAR and device modules.
Bridge carries its JRE legal notices and a JSymphonic source snapshot. Source
and redistribution status are explained in `NOTICES.md`; these unsigned local
artifacts have not been published or code-signed.

To update Python dependencies, change the explicit backend pins, download fresh
Windows cp313 wheels into a new cache directory, then run:

```powershell
backend/.venv/Scripts/python.exe -m pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313 -r backend/requirements.txt -d packaging/.cache/new-wheels
backend/.venv/Scripts/python.exe packaging/lock-wheels.py packaging/.cache/new-wheels
```

Review and commit the changed lock/constraints through the project's normal
review process. Staging refuses a stale top-level requirements lock.

Verification commands:

```powershell
backend/.venv/Scripts/python.exe packaging/test_stage.py
npm run test:frontend
npm run test:packaging
node packaging/verify-asar.cjs
packaging/smoke-install.ps1 -Product player -Installer dist_electron/player/Red-Lotus-Player-Setup-0.4.1-x64.exe
packaging/smoke-install.ps1 -Product bridge -Installer dist_electron/bridge/Walkman-Bridge-Setup-0.4.1-x64.exe
```

The install smoke uses a unique workspace installation/data directory, refuses
to replace an existing registered product, requires authenticated renderer
startup and a clean drain, and uninstalls only after the test process exits.
It retains logs and the installer hash in `packaging/build/install-smoke`.
Physical Walkman tests remain separately controlled by the operator.

`verify-asar.cjs` runs after both products are built. It rejects unexpected archive
entries, including test-only scanner harnesses, and compares packaged Electron
modules with the verified checkout. CI also executes the real Electron renderer,
lifecycle and OfflineAudioContext DSP harnesses and retains their evidence and
the generated audio comparisons.

The stable app IDs, database filename and internal session protocol remain compatible with 0.4.0. Current logs use `redlotus.log`; the packaged helper is `scanner-helper/RedLotus.ScanHelper.exe`. Build output staging is `packaging/build/redlotus`. Historical cache/archive names are intentionally retained.
