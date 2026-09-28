#!/usr/bin/env python3
"""Project Corkboard — local HTTP + JSON API (Python 3.9 stdlib only)."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlparse

import scan

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")
CONFIG_PATH = os.path.join(ROOT, "data", "config.json")
SAMPLES_PATH = os.path.join(ROOT, "data", "samples.json")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4783
DEFAULT_COLOR = "#f5e6a3"

# Classic Post-it pad colors (not a rainbow picker). Hexes are the only legal pin colors.
POSTIT_COLORS = (
    {"hex": "#f5e6a3", "name": "Canary Yellow"},
    {"hex": "#f4b6c8", "name": "Power Pink"},
    {"hex": "#f5a574", "name": "Vital Orange"},
    {"hex": "#d4e07a", "name": "Acid Lime"},
    {"hex": "#b8e0c8", "name": "Lucky Green"},
    {"hex": "#8ec6e6", "name": "Blue Paradise"},
    {"hex": "#c5b4e3", "name": "Iris"},
    {"hex": "#d9c8b4", "name": "Kraft"},
)
POSTIT_HEX = tuple(c["hex"].lower() for c in POSTIT_COLORS)

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}

PROJECT_FIELDS = (
    "id",
    "name",
    "summary",
    "status",
    "color",
    "tags",
    "path",
    "sample",
    "hot",
    "last_activity",
    "git",
)
ALLOWED_STATUSES = ("active", "paused", "idea", "shipped", "archive")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_PROJECT_ID_RE = re.compile(r"^/api/projects/([^/]+)/?$")
_CONFIG_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# Config persistence (atomic-ish: temp file in the same dir, then os.replace)
# ---------------------------------------------------------------------------


def default_config():
    return {
        "app": {
            "name": "Project Corkboard",
            "port": DEFAULT_PORT,
            "host": DEFAULT_HOST,
        },
        "scan": {
            "roots": [],
            "max_depth": 4,
            "max_results": 150,
            "stat_timeout_seconds": 5,
            "skip_names": [],
        },
        "ignored_paths": [],
        "projects": [],
    }


def load_config():
    path = CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object")
    data.setdefault(
        "app",
        {"name": "Project Corkboard", "port": DEFAULT_PORT, "host": DEFAULT_HOST},
    )
    data.setdefault("scan", {})
    if not isinstance(data.get("ignored_paths"), list):
        data["ignored_paths"] = []
    if not isinstance(data.get("projects"), list):
        data["projects"] = []
    return data


def _norm_hex(value):
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def is_postit_color(value):
    return _norm_hex(value) in POSTIT_HEX


def normalize_pin_colors(config):
    """Unknown/legacy colors (e.g. old corkboard blue) fall back to Canary."""
    changed = False
    for project in config.get("projects") or []:
        if not isinstance(project, dict):
            continue
        if is_postit_color(project.get("color")):
            hexv = _norm_hex(project.get("color"))
            if project.get("color") != hexv:
                project["color"] = hexv
                changed = True
            continue
        project["color"] = DEFAULT_COLOR
        changed = True
    return changed


def save_config(config):
    path = CONFIG_PATH
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="config.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_frozen_samples():
    """Factory sample cards. Never written by Pin / Unpin / Ignore."""
    with open(SAMPLES_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        projects = data
    elif isinstance(data, dict):
        projects = data.get("projects")
    else:
        raise ValueError("samples.json must be a list or an object with projects")
    if not isinstance(projects, list) or not projects:
        raise ValueError("samples.json has no projects")
    frozen = []
    for item in projects:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        card = copy.deepcopy(item)
        card["sample"] = True
        card["path"] = None
        frozen.append(card)
    if not frozen:
        raise ValueError("samples.json has no usable sample cards")
    return frozen


def restore_samples(config):
    """Put factory samples back. Real pins (other ids) are kept."""
    frozen = load_frozen_samples()
    frozen_ids = set(p["id"] for p in frozen)
    existing = [
        p
        for p in (config.get("projects") or [])
        if isinstance(p, dict) and p.get("id") not in frozen_ids
    ]
    config["projects"] = frozen + existing
    return config, frozen


def api_restore_samples(_body=None):
    with _CONFIG_LOCK:
        config = load_config()
        config, frozen = restore_samples(config)
        save_config(config)
        return {
            "ok": True,
            "restored": len(frozen),
            "config": config,
        }


# ---------------------------------------------------------------------------
# Path / project helpers
# ---------------------------------------------------------------------------


def resolve_path(path):
    if path is None:
        return None
    if not isinstance(path, str):
        path = str(path)
    path = path.strip()
    if not path:
        return None
    return os.path.abspath(os.path.expanduser(path))


def slugify(text):
    slug = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return slug


def assign_id(path, existing_ids, suggested=None):
    existing_ids = set(existing_ids or [])
    if suggested:
        candidate = str(suggested).strip()
        if candidate and candidate not in existing_ids:
            return candidate
    base = ""
    if path:
        base = slugify(os.path.basename(path.rstrip(os.sep)))
    if not base:
        base = uuid.uuid4().hex[:12]
    candidate = base
    n = 2
    while candidate in existing_ids:
        candidate = "%s-%d" % (base, n)
        n += 1
    return candidate


def find_project_by_path(config, resolved):
    if not resolved:
        return None
    for project in config.get("projects") or []:
        raw = project.get("path")
        if not raw:
            continue
        try:
            other = resolve_path(raw)
        except (OSError, TypeError, ValueError):
            other = raw
        if other == resolved:
            return project
    return None


def find_project_by_id(config, project_id):
    for project in config.get("projects") or []:
        if project.get("id") == project_id:
            return project
    return None


def _as_tags(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def project_from_body(body, config):
    """Turn a project object or scan candidate into a persistable project."""
    if not isinstance(body, dict):
        raise ValueError("Body must be a JSON object")

    raw_path = body.get("path")
    resolved = resolve_path(raw_path) if raw_path else None

    if resolved:
        existing = find_project_by_path(config, resolved)
        if existing:
            return existing, True

    existing_ids = [
        p.get("id") for p in (config.get("projects") or []) if p.get("id")
    ]
    project_id = assign_id(resolved, existing_ids, body.get("id"))

    name = body.get("name")
    if not name:
        if body.get("is_corkboard"):
            name = "Project Corkboard"
        elif resolved:
            name = os.path.basename(resolved.rstrip(os.sep)) or "Untitled"
        else:
            name = "Untitled"

    color = DEFAULT_COLOR

    # Real pins (path set) are never samples. Scanned adds are sample=false.
    sample = bool(body.get("sample")) and not resolved

    status = body.get("status") or "active"
    summary = body.get("summary")
    if summary is None:
        summary = ""

    project = {
        "id": project_id,
        "name": str(name),
        "summary": str(summary),
        "status": str(status),
        "color": str(color),
        "tags": _as_tags(body.get("tags")),
        "path": resolved,
        "sample": sample,
        "hot": bool(body.get("hot")),
        "last_activity": body.get("last_activity"),
        "git": body.get("git"),
    }
    return project, False


def apply_patch(project, patch):
    updated = dict(project)
    if not isinstance(patch, dict):
        raise ValueError("Body must be a JSON object")
    for key in PROJECT_FIELDS:
        if key == "id" or key not in patch:
            continue
        value = patch[key]
        if key == "path":
            updated["path"] = resolve_path(value) if value else None
        elif key == "tags":
            updated["tags"] = _as_tags(value)
        elif key == "sample":
            updated["sample"] = bool(value)
        elif key == "hot":
            updated["hot"] = bool(value)
        elif key == "status":
            status = str(value or "").strip().lower()
            if status not in ALLOWED_STATUSES:
                raise ValueError(
                    "status must be one of: %s" % ", ".join(ALLOWED_STATUSES)
                )
            updated["status"] = status
        elif key == "color":
            hexv = _norm_hex(value)
            if hexv not in POSTIT_HEX:
                raise ValueError("color must be one of the Post-it pad colors")
            updated["color"] = hexv
        else:
            updated[key] = value
    return updated


def missing_path_error(resolved):
    return {
        "ok": False,
        "error": "Path not found or unmounted: %s" % resolved,
        "root": resolved,
        "candidates": [],
        "truncated": False,
        "errors": [],
        "elapsed_ms": 0,
    }


# ---------------------------------------------------------------------------
# API operations (filesystem errors bubble to the handler as ok:false)
# ---------------------------------------------------------------------------


def api_state():
    with _CONFIG_LOCK:
        config = load_config()
        if normalize_pin_colors(config):
            save_config(config)
        return {
            "ok": True,
            "config": config,
            "colors": [dict(c) for c in POSTIT_COLORS],
        }


def api_scan(body):
    if not isinstance(body, dict):
        return {"ok": False, "error": "Body must be a JSON object"}
    raw_path = body.get("path")
    if not raw_path or not str(raw_path).strip():
        return {"ok": False, "error": "path is required"}

    resolved = resolve_path(raw_path)
    with _CONFIG_LOCK:
        config = load_config()

    max_depth = body.get("max_depth")
    if max_depth is None:
        max_depth = (config.get("scan") or {}).get("max_depth", 4)
    try:
        max_depth = int(max_depth)
    except (TypeError, ValueError):
        return {"ok": False, "error": "max_depth must be an integer"}

    try:
        result = scan.scan_path(resolved, config, max_depth=max_depth)
    except Exception as exc:
        payload = missing_path_error(resolved)
        payload["errors"] = [str(exc)]
        if not os.path.isdir(resolved):
            return payload
        payload["error"] = "Scan failed: %s" % exc
        return payload

    if not isinstance(result, dict):
        return {"ok": False, "error": "Invalid scan result"}

    if not result.get("ok"):
        # Trust the scanner's error (including glob "No directories matched").
        # Only fill in the unmounted wording when the scanner left error empty.
        if result.get("error"):
            return result
        if resolved and not os.path.isdir(resolved):
            return missing_path_error(resolved)
        return result

    return result


def api_add_project(body):
    with _CONFIG_LOCK:
        config = load_config()
        project, already = project_from_body(body, config)
        if already:
            return {
                "ok": True,
                "already_tracked": True,
                "project": project,
                "config": config,
            }
        config.setdefault("projects", []).append(project)
        save_config(config)
        return {
            "ok": True,
            "already_tracked": False,
            "project": project,
            "config": config,
        }


def api_ignore(body):
    if not isinstance(body, dict):
        return {"ok": False, "error": "Body must be a JSON object"}
    raw_path = body.get("path")
    if not raw_path or not str(raw_path).strip():
        return {"ok": False, "error": "path is required"}
    resolved = resolve_path(raw_path)
    with _CONFIG_LOCK:
        config = load_config()
        ignored = config.setdefault("ignored_paths", [])
        if resolved not in ignored:
            ignored.append(resolved)
            save_config(config)
        return {"ok": True, "config": config}


def api_patch_project(project_id, body):
    with _CONFIG_LOCK:
        config = load_config()
        projects = config.get("projects") or []
        for index, project in enumerate(projects):
            if project.get("id") == project_id:
                updated = apply_patch(project, body)
                projects[index] = updated
                save_config(config)
                return {"ok": True, "project": updated, "config": config}
        return {"ok": False, "error": "Project not found: %s" % project_id}


def api_delete_project(project_id):
    with _CONFIG_LOCK:
        config = load_config()
        projects = config.get("projects") or []
        kept = [p for p in projects if p.get("id") != project_id]
        if len(kept) == len(projects):
            return {"ok": False, "error": "Project not found: %s" % project_id}
        config["projects"] = kept
        save_config(config)
        return {"ok": True, "config": config}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class CorkboardHTTPServer(HTTPServer):
    allow_reuse_address = True


class CorkboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ProjectCorkboard/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _norm_path(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return path

    def _send_bytes(self, status, content_type, data, extra_headers=None):
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for key, value in extra_headers.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", raw)

    def _send_api_error(self, status, message):
        self._send_json(status, {"ok": False, "error": message})

    def _read_json(self):
        length_header = self.headers.get("Content-Length")
        if not length_header:
            return {}
        try:
            length = int(length_header)
        except ValueError:
            raise ValueError("Invalid Content-Length")
        if length < 0:
            raise ValueError("Invalid Content-Length")
        if length > 10 * 1024 * 1024:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _safe_static(self, relpath):
        relpath = (relpath or "").lstrip("/")
        if not relpath:
            return None
        abs_static = os.path.abspath(STATIC_DIR)
        candidate = os.path.abspath(os.path.join(STATIC_DIR, relpath))
        if candidate != abs_static and not candidate.startswith(abs_static + os.sep):
            return None
        ext = os.path.splitext(candidate)[1].lower()
        if ext not in STATIC_TYPES:
            return None
        if not os.path.isfile(candidate):
            return None
        return candidate

    def _serve_static(self, relpath):
        candidate = self._safe_static(relpath)
        if candidate is None:
            self.send_error(404, "File not found")
            return
        ext = os.path.splitext(candidate)[1].lower()
        try:
            with open(candidate, "rb") as fh:
                data = fh.read()
        except OSError:
            self.send_error(404, "File not found")
            return
        self._send_bytes(200, STATIC_TYPES[ext], data)

    def _run_api(self, fn, *args):
        try:
            payload = fn(*args)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(200, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:
            traceback.print_exc()
            self._send_json(200, {"ok": False, "error": str(exc)})
            return
        if not isinstance(payload, dict):
            self._send_json(200, {"ok": False, "error": "Invalid handler result"})
            return
        self._send_json(200, payload)

    def do_GET(self):
        path = self._norm_path()
        if path == "/api/state":
            self._run_api(api_state)
            return
        if path.startswith("/api/"):
            self._send_api_error(404, "Unknown API route: %s" % path)
            return
        if path in ("/", "/index.html"):
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
            return
        # Let the UI load /styles.css and /app.js if it uses root-relative paths.
        self._serve_static(path.lstrip("/"))

    def do_POST(self):
        path = self._norm_path()
        if not path.startswith("/api/"):
            self._send_api_error(404, "Not found")
            return
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_api_error(400, "Invalid JSON: %s" % exc)
            return
        if path == "/api/scan":
            self._run_api(api_scan, body)
            return
        if path == "/api/projects":
            self._run_api(api_add_project, body)
            return
        if path == "/api/ignore":
            self._run_api(api_ignore, body)
            return
        if path == "/api/samples/restore":
            self._run_api(api_restore_samples, body)
            return
        self._send_api_error(404, "Unknown API route: %s" % path)

    def do_PATCH(self):
        path = self._norm_path()
        match = _PROJECT_ID_RE.match(path)
        if match:
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._send_api_error(400, "Invalid JSON: %s" % exc)
                return
            self._run_api(api_patch_project, unquote(match.group(1)), body)
            return
        if path.startswith("/api/"):
            self._send_api_error(404, "Unknown API route: %s" % path)
            return
        self._send_api_error(404, "Not found")

    def do_DELETE(self):
        path = self._norm_path()
        match = _PROJECT_ID_RE.match(path)
        if match:
            self._run_api(api_delete_project, unquote(match.group(1)))
            return
        if path.startswith("/api/"):
            self._send_api_error(404, "Unknown API route: %s" % path)
            return
        self._send_api_error(404, "Not found")


def bind_address():
    host = os.environ.get("CORKBOARD_HOST") or DEFAULT_HOST
    port_s = os.environ.get("CORKBOARD_PORT")
    if port_s:
        port = int(port_s)
    else:
        port = DEFAULT_PORT
    return host, port


def make_server(host=None, port=None):
    if host is None or port is None:
        env_host, env_port = bind_address()
        if host is None:
            host = env_host
        if port is None:
            port = env_port
    return CorkboardHTTPServer((host, int(port)), CorkboardHandler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Project Corkboard")
    parser.add_argument(
        "--restore-samples",
        action="store_true",
        help="Restore the frozen SAMPLE cards into data/config.json and exit. Real pins are kept.",
    )
    args = parser.parse_args(argv)
    if args.restore_samples:
        with _CONFIG_LOCK:
            config = load_config()
            config, frozen = restore_samples(config)
            save_config(config)
        print(
            "Restored %d SAMPLE card(s) into %s (real pins kept)."
            % (len(frozen), CONFIG_PATH)
        )
        return 0
    httpd = make_server()
    host, port = httpd.server_address
    print("Project Corkboard  http://%s:%s" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    main()
