# fredcrumb — dev-project-corkboard

**SHQL (ShorthandQL)** — the command language and agent ruleset for this project.
- If the task involves SHQL: read the ACTIVE binder first — `shql-binder-v1.3b/SHQL-v1.3b.md`. The binder is the authority, not memory or these notes.
- Per-command how-tos live in `shql-binder-v1.3b/help-files/`.
- Binders are versioned directories; the ACTIVE one is named in the tree below. Upgrades add a new versioned dir — never edit a binder in place.

Project Corkboard: a local corkboard for too many projects. Pin the repos on
this machine — active, paused, half-drawn, shipped, archived — onto a wall of
post-its. Scan a folder, pin what belongs, ignore the rest. Python 3 stdlib
only; nothing leaves the machine.

```
dev-project-corkboard/
├── server.py         ← the board: HTTP + JSON API on 127.0.0.1:4783
├── scan.py           ← read-only repo discovery (CLI too; never mutates scans)
├── data/config.json  ← source of truth: your pins (data/samples.json = factory set)
├── static/           ← the wall UI (index.html, styles.css, app.js)
├── fixtures/         ← harmless demo scan tree
├── tests/            ← tests
├── CONTRACT.md       ← the contract
├── IDEAS.md          ← parked later work
├── runlines.md       ← copy-paste Terminal commands
├── README.md         ← full manual
├── shql-binder-v1.3b/ ← ACTIVE SHQL binder (v1.3b). Versioned dirs: upgrades add new, never edit in place.
└── fredcrumb.md      ← this file: what lives here and why
```

Fred was here.
