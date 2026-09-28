# Project Corkboard

A local corkboard for too many projects.

Pin the repos on this machine — active, paused, half-drawn ideas, shipped, archived — onto a wall of post-its. Scan a folder, add what belongs, ignore the rest. Nothing leaves the machine.

Python 3 stdlib only. No Docker, no pip packages, no Node runtime.

## Run

```bash
cd dev-project-corkboard && python3 server.py
```

Then open [http://127.0.0.1:4783](http://127.0.0.1:4783).

Stop with Ctrl+C.

Host and port default to `127.0.0.1:4783`. Override with `CORKBOARD_HOST` and `CORKBOARD_PORT` if you need to.

## Config

Hand-editable JSON at `data/config.json`. The server reads and writes this file (atomic replace in `data/`). It never writes into the project trees you scan.

```json
{
  "scan": {
    "roots": ["~/dev", "~/dev-project*", "/Volumes/Agent-Naive-External-Drive/*"],
    "max_depth": 4,
    "skip_names": ["node_modules", ".git", ".venv", "dist", "build"]
  },
  "ignored_paths": [],
  "projects": []
}
```

- `scan.roots` — default places shown as chips in the UI. Click one to fill the scan path. Globs such as `~/dev-project*` are allowed.
- `scan.max_depth` — how far down from the scan root to walk (`0` = the root itself). Default `4`.
- `scan.skip_names` — directory names the walker will not enter (`node_modules`, `.git`, build artifacts, …).
- `scan.max_results` / `scan.stat_timeout_seconds` — cap how many candidates come back, and how long a git/stat probe may take.
- `ignored_paths` — absolute paths you marked Ignore. Re-scans list them as ignored and never auto-add them.
- `projects` — the cards on the board.

## Scan

**From the UI:** type or paste a path (or click a root chip) and press Scan. Review the candidates — path, name, confidence, last commit — then **Pin to board** or **Ignore**.

**From the CLI** (no server needed):

```bash
python3 scan.py /path --max-depth 4
python3 scan.py "~/dev-project*" --max-depth 4
```

Globs (`*`, `?`, `[abc]`) expand to matching **directories**. Each match is a scan root; `max_depth` is counted from that folder, not from home. A pattern that matches nothing fails softly with `No directories matched: …` (it does not crash the board).

The scanner is **read-only** toward other repos. It never creates, writes, deletes, or mutates git state inside scanned trees.

It does not walk `/` or `$HOME` unless you ask it to. There is no background crawl.

### External drives

If a root such as `/Volumes/Whatever` is unmounted, the scan fails softly with `Path not found or unmounted: …` and does not hang. The server stays up; scan something else or plug the disk in.

## Add / ignore / edit

- **Pin to board** from scan review (or POST `/api/projects`) pins a project. Duplicate paths are no-ops (`already_tracked`). Scanned adds are real pins (`sample: false`).
- **Ignore** appends the resolved path to `ignored_paths`.
- Click a card on the wall to expand it (path, git snippet, tags). Pick a **status** on the open card: `active`, `paused`, `idea`, `shipped`, `archive`. That writes `data/config.json` immediately. You can also set `"status"` by hand in the file and refresh.
- New pins start **Canary Yellow**. Open the card and click a Post-it circle to change it (Pink, Orange, Lime, Green, Blue Paradise, Iris, Kraft).
- **Hot!** is a red header filter (far right) and a toggle on an open card. On writes `"hot": true` in config and stamps a **Hot!** ribbon the same way SAMPLE is stamped. Off removes it. The Hot! filter shows only those pins.
- **Unpin:** the × on an open card removes that project from `projects`. It is not added to `ignored_paths`. A later scan of the same folder shows it as available to pin again, not “on board”.
- An open card stays open when you click it (status, Hot!, path). Click the path to copy `cd "/that/folder"` for Terminal. Collapse by clicking empty cork, another card, or Esc.
- You can also edit `data/config.json` directly and reload.

## Samples

The board ships with **6 SAMPLE cards** (`"sample": true`, `path: null`) so the wall is not empty the first time you open it. They are marked SAMPLE in the UI.

Unpinning a sample only removes it from `data/config.json`. The factory set is frozen in `data/samples.json` and is never changed by Pin / Unpin / Ignore.

Restore the six demos (real pins are kept; matching sample ids are reset to factory):

```bash
python3 server.py --restore-samples
```

Then refresh the board. If you clear the board entirely, the empty state tells you to scan, restore samples, or hand-edit the config.

## Layout

```
dev-project-corkboard/
  server.py              # HTTP + JSON API
  scan.py                # discovery + ranking (importable + CLI)
  data/config.json       # source of truth (your board)
  data/samples.json      # frozen factory SAMPLE cards (reset with --restore-samples)
  static/                # index.html, styles.css, app.js
  fixtures/sample-tree/  # harmless demo scan path
  tests/
  IDEAS.md               # parked later work (tags, sample types, …)
  runlines.md            # copy-paste Terminal commands
```

A safe demo scan (does not touch your other repos):

```bash
python3 scan.py fixtures/sample-tree --max-depth 4
```

Or paste `fixtures/sample-tree` into the path field on the board and press Scan. You should see layout-based candidates such as `ongoing-app` and `python-lib`, not `notes-only` and not anything under `node_modules`.
