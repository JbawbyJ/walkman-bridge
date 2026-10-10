# Walkman Bridge — Night Ops Electron + audio scanner

**Date:** 2026-08-27 17:58 EDT  
**Conductor:** Hermes chat Grok (default)  
**Builder:** Hermes MoA Dev (profile `dev`)  
**Board:** `walkman-bridge` (`--board walkman-bridge`; current CLI board stays `osint-v7` — never `boards switch`)

## Goal

Ship Walkman Bridge as a **laptop-installed Electron app** whose GUI is the existing **Night Ops** console (Red Lotus), plus a **local audio-file integrity scanner** wired to INTEGRITY SCAN / SCAN PROCESSES / “Scan before transfer”. Not a website.

## Non-goals / Later

- Hosted/web deploy, 0.0.0.0 bind, auth, accounts
- ATRAC encoding
- Full-device backup API (`POST /api/backup` stays 404; first-run strip already explains hand copy)
- Replacing Windows Defender as a product; cloud VirusTotal; full-disk AV
- Real NW-S705F Milestone 4 hardware proof
- Git push / making private GitHub public
- Tauri; keeping pywebview as the primary GUI
- Purple/Inter/SaaS restyle; TypeScript rewrite; WebSockets; SQLite; parallel JSymphonic

## DoD (one line)

`npm run electron` (or equivalent) opens Night Ops in an Electron window on 127.0.0.1; scan API is online; a polyglot/MZ-as-mp3 is THREAT and never hits `add_tracks`; clean mock-device transfer still works; pytest includes new scan tests and prior suite stays green.

## Waterfall TODO

- [x] W1 Requirements locked
- [x] W2 Plan file + Kanban cards + assignments
- [ ] W2 User approval (not requested — user said build with Dev)
- [ ] W3 Implementation cards complete
- [ ] W4 Review Gate (app + scan = bug-hunter + appsec after Dev)
- [ ] W5 farm-verifier
- [ ] W6 Status
- [ ] W7 Cards closed; Later parked

## Assignment

| Epic | Card | Owner | Why | Depends | Status |
|------|------|-------|-----|---------|--------|
| Night Ops Electron | `t_6d049caa` Epic | dev / MoA Dev | rollup | — | blocked (capability) |
| | `t_56b88de8` Audio scanner + API | dev / MoA Dev | backend first; Night Ops already expects the contract | — | blocked |
| | `t_bbc16d0a` Electron shell | dev / MoA Dev | laptop GUI host | scanner can parallel; window needs API later | blocked |
| | `t_833ccdaa` Wire Night Ops | dev / MoA Dev | existing uncommitted React | scanner API + Electron preload | blocked |
| | `t_82ba4ddc` electron-builder | dev / MoA Dev | install on laptop | shell + wire | blocked |

Cards are **blocked on purpose** so the osint/kanban gateway does not steal them. Dev executes from the handoff, not `hermes kanban dispatch`.

## Isolation

Single workdir: `C:\Users\rober\Desktop\Projects\walkman\walkman-bridge`. No worktrees. Do not edit `../jsymphonic` unless a proven blocker (should not). Do not touch `Walkman Bridge/` install tree except to document it is the old PyInstaller build.

## Verification (Dev must run)

- `python -m pytest` in `backend/` (old 39 + new scan tests)
- `npm run build` in `frontend/`
- Electron smoke: window loads, `/api/health` 200, `/api/scan/processes` 200
- Threat fixture never calls `add_tracks` (unit/TestClient)
- Disk proof listed in reportback file
