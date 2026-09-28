# Project Corkboard — v1 contract

Single owner document. All agents implement against this. Do not invent a second stack, port, or config shape.

## Stack

- Python 3 stdlib only (this Mac has 3.9.6). **No pip packages. No Node runtime required.**
- UI: `static/index.html` + `static/styles.css` + `static/app.js` (vanilla, no CDN, no cloud fonts).
- Server: `server.py` — `http.server` subclass, binds **127.0.0.1:4783**.
- Scanner: `scan.py` — importable module + CLI (`python3 scan.py /path`).
- Source of truth: `data/config.json` (hand-editable JSON).
- Tests: `tests/test_scan.py`, `tests/test_api.py` using stdlib `unittest`.

Run from inside the repo:

```bash
python3 server.py
```

Stop: Ctrl+C.

CLI scan (no server needed):

```bash
python3 scan.py /path/to/root --max-depth 4
```

## File layout

```
dev-project-corkboard/
  README.md
  CONTRACT.md
  server.py              # HTTP + JSON API; imports scan.py
  scan.py                # discovery + ranking; never writes into scanned trees
  data/config.json       # projects, scan roots, skip list, ignored_paths
  static/index.html
  static/styles.css
  static/app.js
  fixtures/sample-tree/  # fake projects for tests + demo scan
  tests/test_scan.py
  tests/test_api.py
```

Do not create files outside this folder.

## Port / host

- Host: `127.0.0.1`
- Port: `4783` (override with env `CORKBOARD_PORT`)
- Document as `http://127.0.0.1:4783`

## Config schema (`data/config.json`)

```json
{
  "app": {
    "name": "Project Corkboard",
    "port": 4783,
    "host": "127.0.0.1"
  },
  "scan": {
    "roots": ["~/dev", "~/dev-project*"],
    "max_depth": 4,
    "max_results": 150,
    "stat_timeout_seconds": 5,
    "skip_names": ["node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", "target", ".next", ".cache", "Library", ".Trash", ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".turbo", "vendor", "Pods", ".gradle", ".idea"]
  },
  "ignored_paths": [],
  "projects": []
}
```

### Project object

```json
{
  "id": "string-slug-or-uuid",
  "name": "string",
  "summary": "one line",
  "status": "active",
  "color": "#f5e6a3",
  "tags": ["sample"],
  "path": null,
  "sample": true,
  "last_activity": "2026-08-20T12:00:00",
  "git": {
    "branch": "main",
    "last_commit_at": "2026-08-20T12:00:00",
    "last_commit_subject": "one line",
    "dirty": false,
    "remote": null
  }
}
```

- `status`: `active` | `paused` | `idea` | `shipped` | `archive`
- `path`: absolute path string or `null` (samples may be null)
- `sample`: `true` for shipped demo cards; real pins should be `false` or omitted
- `git` may be `null`
- `id` is stable; when adding from scan, derive from a slug of the absolute path (see server)

`ignored_paths` is a list of absolute path strings the user marked Ignore. Re-scan lists them as ignored, never auto-adds.

## HTTP API

JSON, UTF-8, `Content-Type: application/json`. CORS not required (same origin).

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `/` | — | `static/index.html` |
| GET | `/static/*` | — | static files |
| GET | `/api/state` | — | `{ ok, config }` full config.json |
| POST | `/api/scan` | `{ "path": "/abs/or/~/p", "max_depth": 4 }` | `{ ok, root, candidates, truncated, errors, elapsed_ms }` |
| POST | `/api/projects` | project or candidate fields | `{ ok, project, config }` — persist to config.json |
| POST | `/api/ignore` | `{ "path": "..." }` | `{ ok, config }` |
| PATCH | `/api/projects/{id}` | partial project | `{ ok, project, config }` |
| DELETE | `/api/projects/{id}` | — | `{ ok, config }` |

Soft failures (do **not** 500 the process):

- Missing/unmounted path: HTTP 200 `{ ok: false, error: "Path not found or unmounted: …" }`
- Glob with no directory matches: HTTP 200 `{ ok: false, error: "No directories matched: …" }`
- Scan path may be a glob (`*`, `?`, `[]`). Each matching directory is a scan root. Response may include `roots: [ ...expanded paths ]`.
- Permission / timeout: include in `errors[]`; still return other candidates
- Duplicate add (same resolved path already on board): `{ ok: true, already_tracked: true, project }`

Static 404 is 404. Unknown API route is JSON `{ ok: false, error }` with 404.

## Scanner rules (`scan.py`)

**Read-only.** Never create, write, delete, or `git` mutate anything inside scanned trees. Git via `git -C <path> …` read-only (`rev-parse`, `log`, `status --porcelain`, `branch --show-current`, `remote get-url origin`).

### Walk

- Start at the **user-provided path only** (resolved, expanded `~`). Do not default-walk `/` or `$HOME`.
- `max_depth` default 4 (0 = the root itself).
- Do not follow symlinks (`os.scandir` + skip if `entry.is_symlink()`; also skip if `os.path.realpath` would escape into an already-visited realpath).
- Skip names in `scan.skip_names` (do not descend). Always skip `.git` as a directory to descend, but **detect** `.git` as a **file or dir in the current folder** as a git root.
- Cap at `max_results` likely projects (not every folder). Set `truncated: true` if hit.
- If root does not exist or is not a directory: return `{ ok: false, error }` — no hang.
- Timeouts: if a git command or stat exceeds `stat_timeout_seconds`, skip enrichment, record a soft error, continue.

### What is a project

A directory is a **candidate** only if it has evidence:

**Strong (high confidence):** this directory is a git working tree root — `.git` exists here as a dir or file (worktrees).

**Supporting (medium if 2+ without git; low if 1 without git):**

- README / README.md / README.txt / Readme.md
- Manifests: `package.json`, `pnpm-workspace.yaml`, `pyproject.toml`, `requirements.txt`, `Cargo.toml`, `go.mod`, `Gemfile`, `composer.json`, `mix.exs`, `CMakeLists.txt`, `Makefile`, `Package.swift`, `pubspec.yaml`, `*.xcodeproj`, `*.xcworkspace`
- Source-ish layout: a subdir named `src`, `app`, `lib`, or `cmd` that is a directory — but not if the folder looks like a dependency dump (only `node_modules` / `vendor` besides junk).

**Ignore / do not auto-pin:**

- Folders with only loose files and no git/manifest (or confidence `low` with score below threshold). Return low-confidence only if they have at least one supporting signal; otherwise skip entirely.
- Do not treat `node_modules/...` as projects.

### Ranking / confidence

Score (example): git root +5, README +2, each manifest +2, source layout +1, last commit within 90 days +3, within 365 days +1.

- `high`: git root AND (readme or recent commit or any manifest)
- `medium`: git root alone, OR 2+ supporting signals
- `low`: single supporting signal, no git — include in review but never auto-pin (v1 never auto-pins anyway)

Sort: score desc, then last_activity desc.

### Enrichment

- `name`: folder basename, except if this is Project Corkboard itself → `"Project Corkboard"`
- `summary`: README title or first paragraph (first non-empty line after optional `# `), else last commit subject, else `"{name} — scanned project"`
- **Never invent** what a real repo is about.
- Git: last commit date+subject (one line), current branch, dirty (optional, cheap `status --porcelain`), origin remote URL (optional)
- `last_activity`: git last commit time if available else directory mtime ISO-ish
- Self: if path basename is `dev-project-corkboard` OR the directory contains `server.py` and a README mentioning "Project Corkboard", set `is_corkboard: true` and name/summary accordingly.

### Candidate object

```json
{
  "path": "/abs/resolved",
  "name": "foo",
  "summary": "...",
  "confidence": "high",
  "score": 10,
  "signals": ["git", "readme", "package.json"],
  "tracked": false,
  "ignored": false,
  "is_corkboard": false,
  "sample": false,
  "status": "active",
  "color": "#f5e6a3",
  "tags": [],
  "last_activity": "2026-08-20T12:00:00",
  "mtime": "2026-08-20T12:00:00",
  "git": { "branch": "main", "last_commit_at": "...", "last_commit_subject": "...", "dirty": false, "remote": null }
}
```

`tracked` if resolved path matches any project's `path`. `ignored` if in `ignored_paths`.

New pins start Canary Yellow (`#f5e6a3`). Color is chosen later from the Post-it pad circles. Corkboard is not a special blue.

## UI contract

- Title: **Project Corkboard**
- Corkboard wall with pushpin + post-it cards (slight rotation, readable)
- Header: brand, project count, status filter (All + each status), scan controls (path input + Scan button)
- Default roots shown as clickable chips from `config.scan.roots`
- Sample cards clearly marked SAMPLE
- Empty state (if user deletes all samples and has no pins): explain scan + hand-edit `data/config.json`
- Click card to expand in place (path, git snippet, tags) — do not navigate away
- Scan results: review panel/list — path, name, confidence, last commit, Pin to board / Ignore
- Already-tracked: show as “on board”, no duplicate add
- Colors from `project.color`; statuses as badges
- Desktop-first, works offline once the server is running
- No CDNs

## Sample cards

Ship **6** sample projects in `data/config.json` with `"sample": true` and `path: null`. Distinct colors/statuses. Replace/delete anytime; document in README.

## Fixtures (`fixtures/sample-tree`)

Used by tests and as a safe demo scan path:

- `ongoing-app/` git root + README + package.json (high)
- `rust-tool/` git root + Cargo.toml (high)
- `notes-only/` loose txt files, no git/manifest (must NOT appear as candidate)
- `python-lib/` pyproject.toml + src/ + README, no git (medium)
- `nested/deep/hidden-git/` git at depth 3
- `junk/node_modules/fake-pkg/` with package.json — must NOT appear
- `dev-project-corkboard/` mini copy with README "Project Corkboard" + server.py stub — must be labeled this app

Fixture git repos: create real `.git` via `git init` in tests or a setup script; scanner tests should build temp trees themselves so fixtures in git of *this* repo don’t nest weirdly. Prefer **temp trees inside tests** as the source of truth. Keep `fixtures/sample-tree` as a non-git illustrative layout plus a `fixtures/README.md`.

## Tests (required, drive real code)

- `tests/test_scan.py`: build a temp tree, call `scan.scan_path(...)`, assert notes-only omitted, git+manifest included, node_modules skipped, missing path returns ok=False, corkboard self-id works.
- `tests/test_api.py`: start handler or call functions in `server.py` (prefer importing handlers / using `http.server` against a temp config). Assert POST scan missing path does not crash; POST project persists to the temp config file.

Do not hard-code expected names of the user's real disk projects.

## README must document

What it is; run; stop; URL; config path; how to scan (UI + CLI); add/ignore; change roots; skip list; external-drive unmounted behavior; samples vs real; that scans are read-only.
