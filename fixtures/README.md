# Sample tree (illustrative)

`sample-tree/` is a **non-git** picture of what a scan root can look like.

Scanner tests in `tests/test_scan.py` build their own temporary trees (with real `git init`) so this repo does not nest extra `.git` directories.

To try a harmless demo scan from the UI or CLI, point at this folder — you should see layout-based candidates (`python-lib` via `pyproject.toml`) and skip `notes-only` plus anything under `node_modules`. Git-based fixtures are created at test time.

```
sample-tree/
  ongoing-app/          package.json + README (medium without git)
  rust-tool/            Cargo.toml
  notes-only/           loose files — must not be a candidate
  python-lib/           pyproject.toml + src/ + README
  nested/deep/hidden/   README only (low)
  junk/node_modules/fake-pkg/  must not appear
  dev-project-corkboard/  labeled as this app if detected
```
