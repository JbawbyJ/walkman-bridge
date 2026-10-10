# Management 0.4 independent review

Review date: 2026-09-05. Reviewer: the backend playlist worker, reviewing production surfaces built by other workers. The reviewer did **not** implement the Java engine, MusicManager, usePlayer, or the media_service revision changes. Review of the reviewer's own Python playlist/store/adapter work is excluded from the independent verdict and belongs to the root reviewer.

## Current verdict

**No remaining material finding in the reviewed final candidate.** The independent Java specification and engineering checks pass for the scoped native playlist behavior. All reported product-path findings were corrected and reviewed; the external Windows Unicode failure was reproduced before the fix and verified through the corrected JAR afterward. Final evidence: **28 Java tests**, **36 frontend tests**, one focused metadata-revision test, and an independent external Windows CLI/junction probe. UI lifecycle behavior has direct deterministic tests; the three small MusicManager corrections were checked in source, with actual Electron workflow acceptance left to root's integration gate. No physical Sony access was performed by this reviewer.

## Findings and disposition

1. **P1, confirmed and closed: Windows launcher corrupted non-ANSI native playlist names.** Java `HeadlessCli` originally received the playlist name through a literal command-line argument. An external invocation through Python `subprocess.run([...])`, using the bundled JDK 21, returned exit 0 and `done` for a name containing U+65E5 and U+672C but persisted two U+003F characters. Reading the GPFB UTF-16 bytes independently confirmed actual on-disk corruption in a disposable backup copy. `-Dfile.encoding=UTF-8` and `-Dsun.jnu.encoding=UTF-8` did not repair the boundary. The engine owner and root were notified before hardware use. The final product adapter uses `--name-base64` with bounded ASCII transport of UTF-8 bytes. Java strictly decodes Base64/UTF-8 and applies the native-name validation before mutation. The independent external-process probe passed for Japanese, quotes, backslashes, 30 supplementary characters and 60 CJK characters; raw GPFB bytes matched the intended names. Invalid encoding and excessive lengths failed without writes. The legacy manual `--name` option remains subject to Windows argument encoding; CLI help directs Unicode callers to `--name-base64`.

2. **P2, confirmed and closed: stale restored track left saved playlist scope.** In `frontend/src/usePlayer.js`, restoring `{media_id:'a', playlist:{id:'night',media_ids:['b']}}` while both tracks remain in the library originally assigned `/media/a` while displaying the saved playlist. Pressing Play started `a` and cleared playlist scope. This state can persist when membership changes before the last playback-state flush. The deterministic existing hook harness reproduced both steps. The final initializer selects the first remaining member, or null for an empty playlist, at position zero while remaining paused. The new stale-restoration regression passed in the independent final frontend run.

3. **P2, confirmed and closed by source review: device-read failure hid local playlists.** MusicManager originally committed the successful local playlist fetch only after the Sony fetch completed. An unreadable Sony table or incomplete journal therefore hid local playlists too. The final code publishes local results before the separately caught Sony request, clears stale Sony rows/ETag on failure and renders a Sony-specific error.

4. **P2, confirmed and closed by source review: native name length mismatch.** MusicManager initially allowed 120 UTF-16 units while the Sony record writer allows 60. The final inputs use 60 for Sony create/rename; root also added API validation before write admission. Local names retain their separate 120-character contract. External engine tests independently verified the 60/61-unit boundary and 30/31 supplementary-character boundary.

5. **P2, confirmed and closed by source review: adding a new member deduplicated existing Sony repetitions.** MusicManager's original `unique([...ids,...adding])` removed existing repeated songs when adding another song. The final native branch appends `[...ids,...adding]`, preserving the existing ordered prefix; only local playlists use the unique-member branch. Row keys and single-occurrence removal use the member index. Existing native repetitions also survived the independent external engine create/rename probe.

The Java parser intentionally preserves repeated song IDs on read and rename. The old Python response adapter rejected them; this self-implemented adapter issue was reported to root separately and is not counted as independent review of Python. Root changed native read/write adapters to retain repetitions while local playlists remain unique.

## Java risk assessment

- Native playlist parsing is capped at 4 MiB per file, 2,048 metadata slots and 65,534 total members. Section descriptors, record widths, member offsets and referenced slot IDs are checked before writes.
- Existing GPFB slots are retained byte-for-byte except an explicitly renamed title field. Deletion does not compact or reuse slot IDs. The actual saved Sony fixture has inactive metadata slots; the fixture test verifies those bytes survive adding an active playlist.
- A native mutation writes only the fixed `01TREE22.DAT` / `03GINF22.DAT` pair. Names and track IDs cannot select arbitrary output filenames. Option-looking name `--device` was independently observed to remain name data.
- The pair writer stages synced original and next files, rechecks its original snapshot, publishes both members, rereads bytes, and removes the journal only after verification. A deterministic second-publish failure test verifies exact rollback of both originals. An incomplete journal blocks subsequent playlist access; there is no automatic recovery or retry.
- Ordinary track add/delete still use the legacy nontransactional database writer. The new preflight prevents mutation when playlist data is malformed. The new allowlist preserves unrelated Sony tables, catalog data and opaque playlist records; deletion prunes only removed track memberships. This does not turn legacy add/delete into a crash-atomic operation.
- Path checks reject symlinks and other reparse paths before reads, journal writes, replacement and cleanup. An actual Windows directory junction to the disposable fixture was rejected with a fatal error, and the target database pair remained unchanged. The final candidate passed this check again.
- The engine owner identified that unfamiliar nonzero TREE reserved fields could be normalized by the encoder. The final encoder preserves opaque file/descriptor bytes, while unknown class complement semantics are rejected before mutation. Source review and the independently rerun preservation/rejection test support this correction.

## Fresh executed evidence

All commands ran against source or temporary fixtures, never a physical mount. The source backup was `hardware-proof/pre-playlist-backups/2026-09-05-194948`; tests copy it before mutating. Review commands requiring escalation used it only to access the existing JDK, Maven cache, Python base interpreter or Node runtime.

1. From `C:/Users/rober/Desktop/Projects/walkman/jsymphonic`, with `JAVA_HOME=C:/Users/rober/Desktop/Projects/walkman/tools/jdk-21.0.12+8`:

   `../tools/apache-maven-3.9.9/bin/mvn.cmd -q -o -Dtest=NativePlaylistDatabaseTest,HeadlessCliTest -Dsony.playlist.fixture=C:/Users/rober/Desktop/Projects/walkman/walkman-bridge/hardware-proof/pre-playlist-backups/2026-09-05-194948 test`

   Exit **0**; **20 tests**, zero failures/errors/skips: seven native table tests and thirteen CLI tests. Covered restart/order, malformed offsets and unknown track rejection without writes, empty membership after pruning, incomplete journal refusal, two-file rollback, stale snapshot refusal, and opaque records from the real Sony fixture.

2. Same environment, `-Dtest=HeadlessNativePlaylistTest` with the same fixture property:

   Exit **0**; **5 tests**, zero failures/errors/skips. Covered real copied Sony CLI CRUD, ordinary add to 15 tracks then deletion, surviving membership order, unrelated file preservation, malformed-table refusal before deletion, and read-only enumeration of tiny malformed OMA files.

3. From `C:/Users/rober/Desktop/Projects/walkman/walkman-bridge`:

   `C:/Program Files/nodejs/node.exe --test frontend/src/*.test.js frontend/tests/*.test.mjs`

   Exit **0**; **35 tests**. Includes saved playlist order/end behavior, paused restore, scope-limited repeat, Stop during delayed playlist start, membership removal, deletion returning to all music, and existing codec/lease races. These are deterministic fake Audio/Web Audio tests, not browser or firmware tests.

4. Independent Python-to-Java external subprocess probe on `C:/Users/rober/AppData/Local/Temp/playlist-independent-review-a3xgnj7r`:

   Literal `--device` name and repeated members succeeded. Japanese rename assertion failed; raw GPFB bytes contained `??`. Two encoding-flag attempts reproduced the same corruption. This failure is a product boundary finding, not an environment-only test failure.

5. In-memory execution of the existing hook harness, without modifying source/tests:

   Restored stale-scope case printed `{phase:'restored',id:'a',scope:{id:'night',media_ids:['b']},src:'/media/a'}` then `{phase:'play',id:'a',scope:null,playing:true}`. This proves finding 2.

6. **Final Java rerun after both engine corrections**, same Maven environment and immutable source fixture, with `-Dtest=NativePlaylistDatabaseTest,HeadlessCliTest,HeadlessNativePlaylistTest`:

   Exit **0**; **28 tests**, zero failures/errors/skips: native database **9**, legacy CLI **13**, native CLI **6**. Added coverage includes opaque TREE reserved-byte preservation, unsupported complement refusal, repeated members, Unicode bounds and actual external Java invocation with Base64 names.

7. **Final frontend rerun**, the Node command in item 3:

   Exit **0**; **36 tests**, including `stale restored track cannot escape the saved playlist on Play`. These results are from the corrected source hashes below.

8. **Independent final Windows subprocess probe**, executed as an in-memory Python script using the project interpreter and the candidate fat JAR (not just compiled classes):

   Exit **0**. Passed literal `--device` name-as-data; quoted/backslash/Japanese roundtrip with independent raw GPFB UTF-16 byte comparison; 30 supplementary-character and 60 CJK-character names; repeated membership retention; no-write rejection of 61 ASCII units, 31 supplementary characters, malformed Base64, invalid UTF-8, simultaneous literal/encoded names, and an option token in membership. An actual Windows junction to this temporary copy was rejected. **All 69 saved-backup file hashes matched before and after the probe.** Disposable fixture: `C:/Users/rober/AppData/Local/Temp/playlist-final-review-vhvjto7f`; junction fixture: `C:/Users/rober/AppData/Local/Temp/playlist-final-junction-review-qusbvg_e`.

9. Root's `media_service` revision change was independently rerun with:

   `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playlists.py -k metadata_edit_regenerates_transfer -q -p no:cacheprovider`

   Exit **0**, **1 passed**. Edited metadata regenerated the transfer derivative, the user override survived reanalysis, and the edit retained the previous artifact until a processing operation regenerated it. This is evidence for the service integration, not independent review of the reviewer's own store/routes.

## Final source and artifact identity

SHA-256 captured after final verification. Java source paths are relative to `C:/Users/rober/Desktop/Projects/walkman/jsymphonic`; app paths are relative to `C:/Users/rober/Desktop/Projects/walkman/walkman-bridge`.

| Surface | SHA-256 |
| --- | --- |
| Java `src/main/java/org/naurd/media/jsymphonic/device/sony/nw/NativePlaylistDatabase.java` | `d7d9cfac54fd21256eb3a985801ab18e64c14888442e668bed99accc2c6165cd` |
| Java `src/main/java/org/naurd/media/jsymphonic/device/sony/nw/DataBaseOmgaudio.java` | `e9a428895aeb7e8e926dce8ad9eda86492f64ef31095008725b47fb45a616818` |
| Java `src/main/java/org/naurd/media/jsymphonic/device/sony/nw/NWOmgaudio.java` | `0588975606f0f8ae522d3698cd4d8583361726ef918bf604e88d3e86a0c3986d` |
| Java `src/main/java/org/naurd/media/jsymphonic/headless/HeadlessCli.java` | `f9ad8cd8fdabc980313c2e033d7ebecfd89615d4a4bc769ef185803e14ab8734` |
| Java `target/jsymphonic-0.5.3-jar-with-dependencies.jar`, 3,930,350 bytes | `cbab7deae5baed02b799f43a454095e5b8b8d00a26151cc0c8649c2cccbcfc72` |
| App `frontend/src/usePlayer.js` | `f64e58f20174c865e252d6264c56667c4d9c498975942f03168248ad56b01b51` |
| App `frontend/src/components/MusicManager.jsx` | `c251b50d56b3deb41d7b55803efd0c7a2ac7ab33a27640d56b60a9aeaa8967e9` |
| App `backend/media_service.py` | `cb714b2fefe0a9a9d0681eef14c3ba12f403d418627325c0baeb3939eae49950` |

## Limits and remaining evidence

This is a scoped engineering/security review, not a formal certification. The reviewer has not tested physical firmware menus, actual listening, removal during a physical write, forced process termination or power loss. Ordinary IOException rollback and incomplete-journal refusal have direct tests; hardware crash consistency is not established. Actual Electron management workflow testing and integrated Python API-to-JAR testing remain root's separate gates; another worker's results for those gates are not represented as independently executed here. No production source or test was edited during this review; the only authored repository artifact is this report.
