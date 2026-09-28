#!/usr/bin/env python3
"""Project Corkboard filesystem scanner.

Read-only project discovery. Importable (`scan_path`, `is_corkboard_dir`)
and usable as CLI: `python3 scan.py /path [--max-depth N] [--json]`.
"""

from __future__ import print_function

import argparse
import glob
import json
import os
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Defaults (match CONTRACT.md / data/config.json)
# ---------------------------------------------------------------------------

DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_RESULTS = 150
DEFAULT_STAT_TIMEOUT = 5
DEFAULT_MAX_GLOB_ROOTS = 200
_GLOB_MAGIC = frozenset("*?[]")
DEFAULT_SKIP_NAMES = [
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".next",
    ".cache",
    "Library",
    ".Trash",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
    ".turbo",
    "vendor",
    "Pods",
    ".gradle",
    ".idea",
    ".output",
    ".DS_Store",
]

MANIFEST_FILES = (
    "package.json",
    "pnpm-workspace.yaml",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "CMakeLists.txt",
    "Makefile",
    "Package.swift",
    "pubspec.yaml",
)
MANIFEST_LOWER = dict((n.lower(), n) for n in MANIFEST_FILES)
MANIFEST_SUFFIXES = (".xcodeproj", ".xcworkspace")
SOURCE_DIR_NAMES = ("src", "app", "lib", "cmd")
README_LOWER = ("readme", "readme.md", "readme.txt", "readme.rst", "readme.markdown")
BUNDLE_SUFFIXES = (".xcodeproj", ".xcworkspace")

CORKBOARD_NAME = "Project Corkboard"
CORKBOARD_BASENAME = "dev-project-corkboard"

GIT_ENV = os.environ.copy()
GIT_ENV["GIT_TERMINAL_PROMPT"] = "0"
GIT_ENV["GIT_OPTIONAL_LOCKS"] = "0"
GIT_ENV["GCM_INTERACTIVE"] = "never"


def _as_int(value, default):
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _now_ms():
    return time.time() * 1000.0


def _iso_from_timestamp(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    except (OSError, OverflowError, ValueError, TypeError):
        return None


def _parse_iso(value):
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _days_ago(iso_str):
    dt = _parse_iso(iso_str)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    try:
        delta = now - dt.astimezone(timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    return delta.days


def _norm_key(path):
    if not path:
        return None
    try:
        expanded = os.path.abspath(os.path.expanduser(path))
        return os.path.normcase(os.path.realpath(expanded))
    except (OSError, ValueError):
        try:
            return os.path.normcase(os.path.abspath(os.path.expanduser(path)))
        except (OSError, ValueError):
            return path


def _path_set(paths):
    out = set()
    for item in paths or []:
        key = _norm_key(item)
        if key:
            out.add(key)
    return out


def _same_dir(a, b):
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return _norm_key(a) == _norm_key(b)


def _is_readme_name(name):
    if not name:
        return False
    return name.lower() in README_LOWER or name.lower().startswith("readme")


def _empty_result(ok, error, root, errors, t0, truncated=False, roots=None):
    return {
        "ok": ok,
        "error": error,
        "root": root,
        "roots": list(roots or []),
        "candidates": [],
        "truncated": truncated,
        "errors": list(errors or []),
        "elapsed_ms": int(round(_now_ms() - t0)),
    }


def _is_glob_pattern(path):
    if not path:
        return False
    return any(ch in path for ch in _GLOB_MAGIC)


def _glob_literal_prefix(path):
    """Longest existing-checkable prefix with no glob magic in a segment."""
    path = os.path.normpath(path)
    segs = []
    head = path
    while True:
        head, tail = os.path.split(head)
        if tail:
            segs.append(tail)
            continue
        segs.append(head)
        break
    segs.reverse()
    built = segs[0]
    for seg in segs[1:]:
        if _is_glob_pattern(seg):
            break
        built = os.path.join(built, seg)
    return built


def _expand_glob_roots(pattern, timeout, errors, max_matches):
    """Expand a glob to matching directories. Never follows symlink matches."""
    prefix = _glob_literal_prefix(pattern)
    if prefix:
        check = _check_root(prefix, timeout)
        kind = check[0]
        if kind in ("missing", "timeout", "error"):
            return {
                "ok": False,
                "error": "Path not found or unmounted: %s" % pattern,
                "roots": [],
                "truncated": False,
            }
        if kind == "notdir":
            return {
                "ok": False,
                "error": "Not a directory: %s" % prefix,
                "roots": [],
                "truncated": False,
            }
    try:
        recursive = "**" in pattern.replace("\\", "/")
        raw = glob.glob(pattern, recursive=recursive)
    except (OSError, ValueError) as exc:
        errors.append("Glob failed for %s: %s" % (pattern, exc))
        return {
            "ok": False,
            "error": "Path not found or unmounted: %s" % pattern,
            "roots": [],
            "truncated": False,
        }

    dirs = []
    seen = set()
    for item in sorted(raw):
        try:
            if os.path.islink(item):
                continue
            if not os.path.isdir(item):
                continue
            resolved = os.path.abspath(item)
            key = _norm_key(resolved)
            if key in seen:
                continue
            seen.add(key)
            dirs.append(resolved)
        except OSError as exc:
            errors.append("Cannot inspect match %s: %s" % (item, exc))

    truncated = False
    if max_matches and len(dirs) > max_matches:
        truncated = True
        dirs = dirs[:max_matches]
    if not dirs:
        return {
            "ok": False,
            "error": "No directories matched: %s" % pattern,
            "roots": [],
            "truncated": False,
        }
    return {
        "ok": True,
        "error": None,
        "roots": dirs,
        "truncated": truncated,
    }


def _run_test_bin(flag, path, timeout):
    """Run /bin/test with a timeout so unmounted volumes cannot hang us."""
    try:
        proc = subprocess.run(
            ["/bin/test", flag, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=GIT_ENV,
        )
        return proc.returncode == 0, None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as exc:
        return None, str(exc)


def _check_root(path, timeout):
    """Return ('ok',), ('missing',), ('notdir',), ('timeout',), or ('error', msg)."""
    exists, err = _run_test_bin("-e", path, timeout)
    if err == "timeout":
        return ("timeout",)
    if err:
        # Fall back to os.path if /bin/test is unavailable.
        try:
            if not os.path.exists(path):
                return ("missing",)
            if not os.path.isdir(path):
                return ("notdir",)
            return ("ok",)
        except OSError as exc:
            return ("error", str(exc))
    if not exists:
        return ("missing",)
    is_dir, err = _run_test_bin("-d", path, timeout)
    if err == "timeout":
        return ("timeout",)
    if err:
        try:
            if os.path.isdir(path):
                return ("ok",)
            return ("notdir",)
        except OSError as exc:
            return ("error", str(exc))
    if not is_dir:
        return ("notdir",)
    return ("ok",)


def _git(repo, args, timeout):
    cmd = ["git", "-C", repo] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=GIT_ENV,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout running git %s at %s" % (" ".join(args), repo)
    except OSError as exc:
        return None, "git unavailable at %s: %s" % (repo, exc)
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        return None, stderr or ("git %s failed at %s" % (" ".join(args), repo))
    if stdout.endswith("\n"):
        stdout = stdout[:-1]
    return stdout, None


def _has_dot_git(path):
    git_path = os.path.join(path, ".git")
    try:
        st = os.lstat(git_path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return False
    return stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)


def _git_enrich(path, timeout, errors):
    """If `path` is a git working-tree ROOT, return a git info dict, else None.

    Confirmation: `.git` is already known to exist. We still require
    `rev-parse --show-toplevel` to equal this directory.
    """
    inside, err = _git(path, ["rev-parse", "--is-inside-work-tree"], timeout)
    if err:
        if "timeout" in err or "unavailable" in err:
            errors.append(err)
        return None
    if (inside or "").strip().lower() != "true":
        return None

    toplevel, err = _git(path, ["rev-parse", "--show-toplevel"], timeout)
    if err:
        if "timeout" in err or "unavailable" in err:
            errors.append(err)
        return None
    toplevel = (toplevel or "").strip()
    if not toplevel or not _same_dir(toplevel, path):
        return None

    info = {
        "branch": None,
        "last_commit_at": None,
        "last_commit_subject": None,
        "dirty": False,
        "remote": None,
    }

    log, err = _git(path, ["log", "-1", "--format=%cI%x09%s"], timeout)
    if err:
        if "timeout" in err:
            errors.append(err)
    elif log:
        if "\t" in log:
            when, subject = log.split("\t", 1)
        else:
            when, subject = log, ""
        info["last_commit_at"] = when.strip() or None
        info["last_commit_subject"] = subject.strip() or None

    branch, err = _git(path, ["branch", "--show-current"], timeout)
    if err:
        if "timeout" in err:
            errors.append(err)
    else:
        info["branch"] = (branch or "").strip() or None

    status, err = _git(path, ["status", "--porcelain"], timeout)
    if err:
        if "timeout" in err:
            errors.append(err)
    else:
        info["dirty"] = bool((status or "").strip())

    remote, err = _git(path, ["remote", "get-url", "origin"], timeout)
    if not err:
        info["remote"] = (remote or "").strip() or None

    return info


def _readme_summary(readme_paths):
    preferred = []
    for path in readme_paths:
        base = os.path.basename(path).lower()
        rank = 1 if base == "readme.md" else 2 if base == "readme" else 3
        preferred.append((rank, path))
    preferred.sort()
    for _rank, path in preferred:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                chunk = fh.read(65536)
        except OSError:
            continue
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                stripped = stripped.lstrip("#").strip()
            if stripped:
                return stripped
    return None


def is_corkboard_dir(path):
    """True if basename is dev-project-corkboard OR (server.py exists AND
    a README* mentions 'Project Corkboard')."""
    if not path:
        return False
    try:
        base = os.path.basename(os.path.normpath(path))
    except (OSError, TypeError):
        return False
    if base == CORKBOARD_BASENAME:
        return True

    server = os.path.join(path, "server.py")
    try:
        if not os.path.isfile(server):
            return False
    except OSError:
        return False

    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if not entry.name.lower().startswith("readme"):
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    with open(entry.path, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read(65536)
                    if "Project Corkboard" in text:
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _inspect_dir(path, skip_names, errors):
    """List a directory once. Never follows symlinks."""
    info = {
        "mtime": None,
        "has_git": False,
        "readmes": [],
        "manifests": [],
        "source_dirs": [],
        "children": [],
    }
    try:
        st = os.lstat(path)
    except OSError as exc:
        errors.append("Cannot stat %s: %s" % (path, exc))
        return None
    if stat.S_ISLNK(st.st_mode):
        return None
    if not stat.S_ISDIR(st.st_mode):
        return None
    info["mtime"] = _iso_from_timestamp(st.st_mtime)

    try:
        entries = list(os.scandir(path))
    except PermissionError:
        errors.append("Permission denied: %s" % path)
        return info
    except OSError as exc:
        errors.append("Cannot read %s: %s" % (path, exc))
        return info

    for entry in entries:
        try:
            name = entry.name
            if entry.is_symlink():
                continue
            if name == ".git":
                try:
                    st_e = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(st_e.st_mode) or stat.S_ISREG(st_e.st_mode):
                        info["has_git"] = True
                except OSError:
                    info["has_git"] = True
                continue
            if name in skip_names:
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
            lower = name.lower()
            if is_dir and name in SOURCE_DIR_NAMES:
                info["source_dirs"].append(name)
            if is_dir and lower.endswith(MANIFEST_SUFFIXES):
                info["manifests"].append(name)
            elif is_file and lower in MANIFEST_LOWER:
                info["manifests"].append(MANIFEST_LOWER[lower])
            if is_file and _is_readme_name(name):
                info["readmes"].append(entry.path)
            if is_dir:
                if lower.endswith(BUNDLE_SUFFIXES):
                    continue
                info["children"].append(entry.path)
        except OSError as exc:
            errors.append("Cannot inspect %s: %s" % (getattr(entry, "path", path), exc))
    info["manifests"] = sorted(set(info["manifests"]))
    info["source_dirs"] = sorted(set(info["source_dirs"]))
    return info


def _build_candidate(path, info, git_info, config, corkboard):
    signals = []
    score = 0
    is_git_root = git_info is not None
    if is_git_root:
        signals.append("git")
        score += 5

    has_readme = bool(info.get("readmes"))
    if has_readme:
        signals.append("readme")
        score += 2

    manifests = list(info.get("manifests") or [])
    for name in manifests:
        signals.append(name)
        score += 2

    source_dirs = list(info.get("source_dirs") or [])
    if source_dirs:
        # One point for source-ish layout, regardless of how many of src/app/lib/cmd.
        signals.append(source_dirs[0] if len(source_dirs) == 1 else "src")
        score += 1

    last_commit_at = None
    last_commit_subject = None
    if git_info:
        last_commit_at = git_info.get("last_commit_at")
        last_commit_subject = git_info.get("last_commit_subject")
        days = _days_ago(last_commit_at)
        if days is not None:
            if days <= 90:
                score += 3
            elif days <= 365:
                score += 1

    supporting = 0
    if has_readme:
        supporting += 1
    supporting += len(manifests)
    if source_dirs:
        supporting += 1

    if not is_git_root and supporting == 0 and not corkboard:
        return None

    recent = False
    if last_commit_at:
        days = _days_ago(last_commit_at)
        if days is not None and days <= 365:
            recent = True

    if corkboard:
        # This app should never look like an unidentified stray folder.
        confidence = "high"
        score = max(score, 10)
    elif is_git_root and (has_readme or recent or bool(manifests)):
        confidence = "high"
    elif is_git_root or supporting >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    resolved = os.path.realpath(path)
    base = os.path.basename(resolved.rstrip(os.sep)) or os.path.basename(path)
    name = CORKBOARD_NAME if corkboard else base

    summary = _readme_summary(info.get("readmes") or [])
    if not summary and last_commit_subject:
        summary = last_commit_subject
    if not summary:
        summary = "%s — scanned project" % name

    mtime = info.get("mtime")
    last_activity = last_commit_at or mtime

    projects = (config or {}).get("projects") or []
    ignored_paths = (config or {}).get("ignored_paths") or []
    tracked_keys = _path_set([p.get("path") for p in projects if isinstance(p, dict)])
    ignored_keys = _path_set(ignored_paths)
    key = _norm_key(resolved)

    return {
        "path": resolved,
        "name": name,
        "summary": summary,
        "confidence": confidence,
        "score": score,
        "signals": signals,
        "tracked": key in tracked_keys,
        "ignored": key in ignored_keys,
        "is_corkboard": bool(corkboard),
        "sample": False,
        "status": "active",
        "tags": [],
        "last_activity": last_activity,
        "mtime": mtime,
        "git": git_info,
    }


def scan_path(root, config, max_depth=None):
    """Read-only project discovery.

    root: str path (may include ~). Globs with *, ?, or [] expand to
    matching directories; each match is scanned as its own root.
    config: full config dict from data/config.json (uses config["scan"] and
            config.get("ignored_paths"), config.get("projects"))
    max_depth: optional int override (relative to each matched root)

    ALWAYS returns a dict, never raises to the caller for missing paths /
    permission / timeouts.
    """
    t0 = _now_ms()
    errors = []
    config = config if isinstance(config, dict) else {}
    scan_cfg = config.get("scan") if isinstance(config.get("scan"), dict) else {}

    timeout = _as_int(scan_cfg.get("stat_timeout_seconds"), DEFAULT_STAT_TIMEOUT)
    if timeout <= 0:
        timeout = DEFAULT_STAT_TIMEOUT
    if max_depth is None:
        max_depth = _as_int(scan_cfg.get("max_depth"), DEFAULT_MAX_DEPTH)
    else:
        max_depth = _as_int(max_depth, DEFAULT_MAX_DEPTH)
    if max_depth < 0:
        max_depth = 0
    max_results = _as_int(scan_cfg.get("max_results"), DEFAULT_MAX_RESULTS)
    if max_results < 0:
        max_results = 0

    skip_names = set(scan_cfg.get("skip_names") or DEFAULT_SKIP_NAMES)
    skip_names.add(".git")
    max_glob_roots = _as_int(
        scan_cfg.get("max_glob_roots"), DEFAULT_MAX_GLOB_ROOTS
    )
    if max_glob_roots <= 0:
        max_glob_roots = DEFAULT_MAX_GLOB_ROOTS

    if root is None or str(root).strip() == "":
        return _empty_result(
            False, "Path not found or unmounted: %s" % root, root, errors, t0
        )

    expanded = os.path.abspath(os.path.expanduser(str(root).strip()))
    globbed = _is_glob_pattern(expanded)
    glob_truncated = False

    if globbed:
        expansion = _expand_glob_roots(
            expanded, timeout, errors, max_glob_roots
        )
        if not expansion.get("ok"):
            return _empty_result(
                False,
                expansion.get("error"),
                expanded,
                errors,
                t0,
                roots=expansion.get("roots"),
            )
        start_roots = expansion.get("roots") or []
        glob_truncated = bool(expansion.get("truncated"))
        root_resolved = expanded
    else:
        check = _check_root(expanded, timeout)
        kind = check[0]
        if kind == "timeout":
            return _empty_result(
                False,
                "Path not found or unmounted: %s" % expanded,
                expanded,
                errors,
                t0,
            )
        if kind == "missing":
            return _empty_result(
                False,
                "Path not found or unmounted: %s" % expanded,
                expanded,
                errors,
                t0,
            )
        if kind == "notdir":
            return _empty_result(
                False,
                "Not a directory: %s" % expanded,
                expanded,
                errors,
                t0,
            )
        if kind == "error":
            return _empty_result(
                False,
                "Path not found or unmounted: %s" % expanded,
                expanded,
                errors,
                t0,
            )
        try:
            root_resolved = os.path.realpath(expanded)
        except OSError:
            root_resolved = expanded
        start_roots = [expanded]

    visited = set()
    found = []
    truncated = glob_truncated

    for start in start_roots:
        if _walk_from_root(
            start,
            max_depth,
            skip_names,
            timeout,
            config,
            visited,
            found,
            errors,
            max_results,
        ):
            truncated = True
            break

    found.sort(
        key=lambda c: (int(c.get("score") or 0), c.get("last_activity") or ""),
        reverse=True,
    )

    return {
        "ok": True,
        "error": None,
        "root": root_resolved,
        "roots": list(start_roots),
        "candidates": found,
        "truncated": truncated,
        "errors": errors,
        "elapsed_ms": int(round(_now_ms() - t0)),
    }


def scan_path_list(paths, config, max_depth=None):
    """Scan one pattern, or merge several (shell-expanded globs become many args)."""
    cleaned = []
    for item in paths or []:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            cleaned.append(text)
    if not cleaned:
        t0 = _now_ms()
        return _empty_result(
            False, "Path not found or unmounted: %s" % paths, paths, [], t0
        )
    if len(cleaned) == 1:
        return scan_path(cleaned[0], config, max_depth=max_depth)

    t0 = _now_ms()
    found = []
    errors = []
    roots = []
    truncated = False
    seen_paths = set()
    last_error = None
    ok_any = False
    display_root = " ".join(cleaned)

    for item in cleaned:
        one = scan_path(item, config, max_depth=max_depth)
        if one.get("truncated"):
            truncated = True
        errors.extend(one.get("errors") or [])
        if one.get("ok"):
            ok_any = True
            for root in one.get("roots") or []:
                if root not in roots:
                    roots.append(root)
            if one.get("root") and one.get("root") not in roots:
                # literal single-root scans put the path in root
                pass
            for cand in one.get("candidates") or []:
                key = _norm_key(cand.get("path"))
                if key and key in seen_paths:
                    continue
                if key:
                    seen_paths.add(key)
                found.append(cand)
        else:
            last_error = one.get("error") or last_error
            if one.get("error"):
                errors.append(one.get("error"))

    if not ok_any:
        return _empty_result(
            False,
            last_error or "Path not found or unmounted: %s" % display_root,
            display_root,
            errors,
            t0,
            roots=roots,
        )

    scan_cfg = (config or {}).get("scan") if isinstance(config, dict) else {}
    max_results = _as_int(
        (scan_cfg or {}).get("max_results"), DEFAULT_MAX_RESULTS
    )
    found.sort(
        key=lambda c: (int(c.get("score") or 0), c.get("last_activity") or ""),
        reverse=True,
    )
    if max_results and len(found) > max_results:
        found = found[:max_results]
        truncated = True

    return {
        "ok": True,
        "error": None,
        "root": display_root,
        "roots": roots,
        "candidates": found,
        "truncated": truncated,
        "errors": errors,
        "elapsed_ms": int(round(_now_ms() - t0)),
    }


def _walk_from_root(
    start,
    max_depth,
    skip_names,
    timeout,
    config,
    visited,
    found,
    errors,
    max_results,
):
    """Walk one root. Mutates visited/found/errors. True if result cap hit."""
    stack = [(start, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            real = os.path.realpath(current)
        except OSError as exc:
            errors.append("Cannot resolve %s: %s" % (current, exc))
            continue
        if real in visited:
            continue
        visited.add(real)

        info = _inspect_dir(current, skip_names, errors)
        if info is None:
            continue

        git_info = None
        if info.get("has_git") or _has_dot_git(current):
            git_info = _git_enrich(current, timeout, errors)

        corkboard = is_corkboard_dir(current)
        candidate = _build_candidate(current, info, git_info, config, corkboard)
        if candidate is not None:
            found.append(candidate)
            if max_results and len(found) >= max_results:
                return True

        if depth >= max_depth:
            continue
        children = list(info.get("children") or [])
        children.sort(reverse=True)
        for child in children:
            try:
                if os.path.islink(child):
                    continue
                child_real = os.path.realpath(child)
            except OSError:
                continue
            if child_real in visited:
                continue
            stack.append((child, depth + 1))
    return False


def _load_config(path):
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if path:
        candidates.append(path)
        if not os.path.isabs(path):
            candidates.append(os.path.join(here, path))
    candidates.append(os.path.join(here, "data", "config.json"))
    seen = set()
    for candidate in candidates:
        real = os.path.abspath(candidate)
        if real in seen:
            continue
        seen.add(real)
        if os.path.isfile(real):
            with open(real, "r", encoding="utf-8") as fh:
                return json.load(fh)
    return {
        "scan": {
            "roots": [],
            "max_depth": DEFAULT_MAX_DEPTH,
            "max_results": DEFAULT_MAX_RESULTS,
            "stat_timeout_seconds": DEFAULT_STAT_TIMEOUT,
            "skip_names": list(DEFAULT_SKIP_NAMES),
        },
        "ignored_paths": [],
        "projects": [],
    }


def _print_table(result):
    if not result.get("ok"):
        sys.stderr.write("Error: %s\n" % (result.get("error") or "scan failed"))
        return
    rows = result.get("candidates") or []
    table = []
    for cand in rows:
        git = cand.get("git") or {}
        table.append(
            (
                cand.get("path") or "",
                cand.get("name") or "",
                cand.get("confidence") or "",
                git.get("last_commit_subject") or "",
            )
        )
    headers = ("PATH", "NAME", "CONFIDENCE", "LAST COMMIT")
    if not table:
        print("No project candidates found.")
    else:
        widths = [len(h) for h in headers]
        for row in table:
            for i, col in enumerate(row):
                widths[i] = max(widths[i], len(col))
        fmt = "  ".join("{:<%d}" % w for w in widths)
        print(fmt.format(*headers))
        print("  ".join("-" * w for w in widths))
        for row in table:
            print(fmt.format(*row))
    extra = []
    if result.get("truncated"):
        extra.append("truncated: max_results reached")
    extra.append(
        "%d candidate(s), %s ms"
        % (len(rows), result.get("elapsed_ms"))
    )
    print()
    print("; ".join(extra))
    issues = result.get("errors") or []
    if issues:
        print("Issues:")
        for item in issues:
            print("  - %s" % item)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Project Corkboard — read-only project scanner"
    )
    parser.add_argument(
        "path",
        nargs="+",
        help="Root directory or glob (quote it: '~/dev-project*'). "
        "Unquoted globs are fine too — the shell passes each match.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Walk depth (0 = root only). Default: config scan.max_depth",
    )
    parser.add_argument(
        "--config",
        default="data/config.json",
        help="Path to config.json (default: data/config.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Dump the full scan_path result as JSON",
    )
    args = parser.parse_args(argv)
    config = _load_config(args.config)
    result = scan_path_list(args.path, config, max_depth=args.max_depth)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_table(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
