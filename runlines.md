# Project Corkboard — runlines

Paste in Terminal. Run from anywhere unless a line already `cd`s.

## Server

Start:

```bash
cd /Users/agent-naive/dev-project-corkboard && python3 server.py
```

Then open: http://127.0.0.1:4783

Stop: `Ctrl+C`

Optional port:

```bash
cd /Users/agent-naive/dev-project-corkboard && CORKBOARD_PORT=4783 python3 server.py
```

## Restore samples

Puts the six frozen SAMPLE cards back. Real pins stay. Matching sample ids reset to factory.

```bash
cd /Users/agent-naive/dev-project-corkboard && python3 server.py --restore-samples
```

Refresh the board after. Factory copy: `data/samples.json`. Live board: `data/config.json`.

## Scan (CLI, no server)

```bash
cd /Users/agent-naive/dev-project-corkboard && python3 scan.py "~/dev-project*" --max-depth 0
```

```bash
cd /Users/agent-naive/dev-project-corkboard && python3 scan.py "/Volumes/Agent-Naive-External-Drive/*" --max-depth 0
```

```bash
cd /Users/agent-naive/dev-project-corkboard && python3 scan.py ~/dev --max-depth 4
```

```bash
cd /Users/agent-naive/dev-project-corkboard && python3 scan.py fixtures/sample-tree --max-depth 4
```

Quote globs so the app expands them. Unquoted is fine too — the shell passes each match.

## Tests

```bash
cd /Users/agent-naive/dev-project-corkboard && python3 -m unittest discover -s tests -v
```
