# Project Corkboard — idea sheet

Parked for a later pass. Not in v1 UI unless noted.

## Tags on cards

Expanded pins used to show a tag row at the bottom (`project.tags`). That display is still wired if tags exist, but **samples no longer ship with tags** — the wall was too busy.

Bring tags back as a real feature:

- Edit on the open card (add / remove chips), persist in `data/config.json`
- Filter or search by tag
- Optional scan-time guesses (language, `local-first`, etc.) — never invent what a real repo is about
- A `sample` tag is not needed; `"sample": true` already marks demo cards

### Original sample tag vocabulary (restore if we re-seed)

| Sample | Tags that were on it |
|---|---|
| Starlight Notes | `sample`, `notes`, `local-first` |
| Harbor Radio | `sample`, `audio` |
| Lantern Maps | `sample`, `maps` |
| Postcard Press | `sample`, `web` |
| Attic Ledger | `sample`, `python` |
| Project Corkboard | `sample`, `meta` |

## Sample types (the six demo cards)

Keep these as the palette of *kinds of project* the board should still feel like, even without tags. They exist to show status + color + git-or-not at a glance.

| id | Name | Status | Color | What it is for | Git flavor |
|---|---|---|---|---|---|
| `sample-starlight` | Starlight Notes | active | `#f5e6a3` yellow | Quiet local-first notebook | clean `main`, recent commit |
| `sample-harbor` | Harbor Radio | paused | `#c9e4c5` green | Weekend thing parked mid-work | dirty `wip/eq` branch |
| `sample-lantern` | Lantern Maps | idea | `#f8d3b0` orange | Half-drawn, no repo yet | none |
| `sample-postcard` | Postcard Press | shipped | `#e8c4d8` pink | Finished, someone else's show | clean `main` + remote |
| `sample-attic` | Attic Ledger | archive | `#d9c8b4` kraft | Old script, keep just in case | old freeze commit |
| `sample-corkboard` | Project Corkboard | *(this app)* | pad color (not special blue) | Meta card for the board itself | none |

When replacing samples with real pins, try to keep one of each status on the wall if it still makes sense.

## Other later ideas (from v1)

- Persist pin positions on the cork
- Color picker on the card (colors are already in config)
- Open in Finder / VS Code / Cursor
- Live git status on pinned paths
- Search
