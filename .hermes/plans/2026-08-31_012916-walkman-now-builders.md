# Walkman Bridge NOW — builders

Board: `walkman-bridge`. Never `boards switch`. Never T3 `proj-osint-v7`.

## Goal

SoT lock + in-app backup API. Hardware stays founder. Packaging stays Later `t_82ba4ddc`.

## Role-fit

| Card | Builder | Why | Verify |
|------|---------|-----|--------|
| W-SOT | conductor (this chat) | Docs only; no dual-writer on backend | grep SoT files; no pytest required |
| W-BACKUP | Codex (Claude failover) | Surgical FastAPI + pytest | `python -m pytest backend/tests` |
| W-HW | founder | Live device | human: one track plays; backup folder exists |
| Review | Cursor `@farm-verifier` / conductor | implementer ≠ verifier | disk proof |
| Packaging | — | already `t_82ba4ddc` | do not ready |

Cursor = planner/review, not writer. No Fable. No jsymphonic feature work this wave.

## Isolation

- W-SOT: `STRUCTURE.md`, `README.md`, `.cursorrules` if present
- W-BACKUP: `backend/main.py`, `backend/tests/test_backup.py` (new), maybe `frontend/src/api.js` backup strip only if already wired
- Do not both edit `main.py` — SOT does not touch Python

## Parallel

W-SOT ∥ W-BACKUP. W-HW never auto-promotes.

## Risks

- Dispatcher lock on `dev` can grab `ready` cards — reclaim + block W-HW immediately
- farm-dispatch Codex no-op if verify passes on old tests — require new `test_backup.py`
- Dirty tree: local Electron WIP — W-BACKUP must not revert uncommitted frontend
- Do not write the real Walkman in tests

## Dispatch

```
python %LOCALAPPDATA%\hermes\bin\farm-dispatch.py --workdir C:\Users\rober\Desktop\Projects\walkman\walkman-bridge --board walkman-bridge --card <W-BACKUP-id> --builder auto --max-turns 80 --verify-cmd "python -m pytest backend/tests"
```

W-SOT: conductor, no farm-dispatch.
