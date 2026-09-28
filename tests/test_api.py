#!/usr/bin/env python3
"""API tests for Project Corkboard — drive real server.py against a temp config."""

import http.client
import json
import os
import sys
import tempfile
import threading
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SCAN_PY = os.path.join(ROOT, "scan.py")
SAMPLE_TREE = os.path.join(ROOT, "fixtures", "sample-tree")


def _install_scan_stub():
    """Tests-only fallback if scan.py is not importable yet."""
    stub = types.ModuleType("scan")

    def scan_path(root, config, max_depth=4):
        exists = os.path.isdir(root)
        if not exists:
            return {
                "ok": False,
                "error": "Path not found or unmounted: %s" % root,
                "root": root,
                "candidates": [],
                "truncated": False,
                "errors": [],
                "elapsed_ms": 0,
            }
        return {
            "ok": True,
            "root": root,
            "candidates": [],
            "truncated": False,
            "errors": [],
            "elapsed_ms": 0,
        }

    scan_path._corkboard_stub = True
    stub.scan_path = scan_path
    sys.modules["scan"] = stub
    return stub


def _load_scan_module():
    try:
        import scan as scan_mod

        if callable(getattr(scan_mod, "scan_path", None)):
            return scan_mod, False
    except ImportError:
        pass
    return _install_scan_stub(), True


scan, _SCAN_STUBBED = _load_scan_module()

import server  # noqa: E402


def _is_stub_scan(mod):
    fn = getattr(mod, "scan_path", None)
    return bool(getattr(fn, "_corkboard_stub", False))


def scan_module_ready():
    """True only if the real scan.py is what server.py will call."""
    if not os.path.isfile(SCAN_PY):
        return False
    scan_mod = sys.modules.get("scan")
    if scan_mod is None or _is_stub_scan(scan_mod):
        try:
            sys.modules.pop("scan", None)
            import scan as real_scan

            server.scan = real_scan
            scan_mod = real_scan
        except Exception:
            return False
    return callable(getattr(scan_mod, "scan_path", None)) and not _is_stub_scan(
        scan_mod
    )


class ApiTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self._tmpdir.name, "config.json")
        self.known_sample = {
            "id": "test-sample-alpha",
            "name": "Alpha Cork Test",
            "summary": "A known sample written by the test, not production data.",
            "status": "active",
            "color": "#f5e6a3",
            "tags": ["sample", "test-only"],
            "path": None,
            "sample": True,
            "last_activity": "2026-08-01T00:00:00",
            "git": None,
        }
        self.initial_config = {
            "app": {
                "name": "Project Corkboard",
                "port": 4783,
                "host": "127.0.0.1",
            },
            "scan": {
                "roots": [self._tmpdir.name],
                "max_depth": 4,
                "max_results": 150,
                "stat_timeout_seconds": 5,
                "skip_names": ["node_modules", ".git"],
            },
            "ignored_paths": [],
            "projects": [self.known_sample],
        }
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(self.initial_config, fh, indent=2)
            fh.write("\n")

        self._orig_config_path = server.CONFIG_PATH
        server.CONFIG_PATH = self.config_path

        self.httpd = server.make_server("127.0.0.1", 0)
        self.host, self.port = self.httpd.server_address
        self._thread = threading.Thread(target=self.httpd.serve_forever)
        self._thread.daemon = True
        self._thread.start()

    def tearDown(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
            self._thread.join(timeout=5)
        finally:
            server.CONFIG_PATH = self._orig_config_path
            self._tmpdir.cleanup()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=15)
        try:
            headers = {}
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
                headers["Content-Length"] = str(len(payload))
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            text = raw.decode("utf-8")
            try:
                data = json.loads(text) if text else None
            except ValueError:
                data = text
            return resp.status, data
        finally:
            conn.close()

    def _read_disk_config(self):
        with open(self.config_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def test_get_state_returns_temp_config_projects(self):
        status, data = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertIn("config", data)
        projects = data["config"].get("projects") or []
        names = [p.get("name") for p in projects]
        ids = [p.get("id") for p in projects]
        self.assertIn("Alpha Cork Test", names)
        self.assertIn("test-sample-alpha", ids)
        self.assertEqual(len(projects), 1)

    def test_scan_missing_path_does_not_crash(self):
        missing = os.path.join(
            self._tmpdir.name, "definitely-not-mounted", "nope-xyzzy"
        )
        status, data = self.request(
            "POST", "/api/scan", {"path": missing, "max_depth": 2}
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertFalse(data.get("ok"))
        self.assertTrue(data.get("error"))

        status2, state = self.request("GET", "/api/state")
        self.assertEqual(status2, 200)
        self.assertTrue(state.get("ok"))
        names = [p.get("name") for p in state["config"].get("projects") or []]
        self.assertIn("Alpha Cork Test", names)

    def test_scan_glob_expands_and_no_match_keeps_message(self):
        alpha = os.path.join(self._tmpdir.name, "dev-project-alpha")
        other = os.path.join(self._tmpdir.name, "other-app")
        os.mkdir(alpha)
        os.mkdir(other)
        with open(os.path.join(alpha, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# Alpha\n")
        with open(os.path.join(alpha, "package.json"), "w", encoding="utf-8") as fh:
            fh.write('{"name":"alpha"}\n')
        with open(os.path.join(other, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# Other\n")
        with open(os.path.join(other, "package.json"), "w", encoding="utf-8") as fh:
            fh.write('{"name":"other"}\n')

        pattern = os.path.join(self._tmpdir.name, "dev-project-*")
        status, data = self.request(
            "POST", "/api/scan", {"path": pattern, "max_depth": 0}
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"), data)
        names = [
            os.path.basename((c.get("path") or "").rstrip(os.sep))
            for c in data.get("candidates") or []
        ]
        self.assertIn("dev-project-alpha", names)
        self.assertNotIn("other-app", names)
        self.assertGreaterEqual(len(data.get("roots") or []), 1)

        miss = os.path.join(self._tmpdir.name, "nope-glob-zzzz-*")
        status2, empty = self.request(
            "POST", "/api/scan", {"path": miss, "max_depth": 2}
        )
        self.assertEqual(status2, 200)
        self.assertFalse(empty.get("ok"))
        self.assertIn("No directories matched", empty.get("error") or "")

        status3, state = self.request("GET", "/api/state")
        self.assertEqual(status3, 200)
        self.assertTrue(state.get("ok"))

    def test_post_project_persists_to_disk(self):
        pin_dir = os.path.join(self._tmpdir.name, "pinned-app")
        os.mkdir(pin_dir)
        body = {
            "name": "Pinned App",
            "summary": "Added via API test.",
            "path": pin_dir,
            "status": "active",
            "tags": ["test"],
        }
        status, data = self.request("POST", "/api/projects", body)
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertFalse(data.get("already_tracked"))
        project = data.get("project") or {}
        self.assertEqual(project.get("name"), "Pinned App")
        self.assertEqual(project.get("path"), os.path.abspath(pin_dir))
        self.assertFalse(project.get("sample"))
        self.assertTrue(project.get("id"))
        self.assertEqual((project.get("color") or "").lower(), server.DEFAULT_COLOR)

        on_disk = self._read_disk_config()
        disk_names = [p.get("name") for p in on_disk.get("projects") or []]
        self.assertIn("Pinned App", disk_names)
        disk_match = [
            p for p in on_disk["projects"] if p.get("name") == "Pinned App"
        ][0]
        self.assertEqual(disk_match.get("path"), os.path.abspath(pin_dir))

        status2, state = self.request("GET", "/api/state")
        self.assertEqual(status2, 200)
        self.assertTrue(state.get("ok"))
        names = [p.get("name") for p in state["config"].get("projects") or []]
        self.assertIn("Pinned App", names)
        self.assertIn("Alpha Cork Test", names)

    def test_duplicate_path_sets_already_tracked(self):
        pin_dir = os.path.join(self._tmpdir.name, "dup-app")
        os.mkdir(pin_dir)
        first_body = {
            "name": "Dup App",
            "path": pin_dir,
            "summary": "first add",
        }
        status, first = self.request("POST", "/api/projects", first_body)
        self.assertEqual(status, 200)
        self.assertTrue(first.get("ok"))
        self.assertFalse(first.get("already_tracked"))
        first_id = first["project"]["id"]

        status2, second = self.request(
            "POST",
            "/api/projects",
            {
                "name": "Dup App Clone",
                "path": pin_dir + os.sep,
                "summary": "should not create a second card",
            },
        )
        self.assertEqual(status2, 200)
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("already_tracked"))
        self.assertEqual(second["project"]["id"], first_id)
        self.assertEqual(second["project"]["name"], "Dup App")

        on_disk = self._read_disk_config()
        paths = [
            p.get("path")
            for p in on_disk.get("projects") or []
            if p.get("path")
        ]
        self.assertEqual(paths.count(os.path.abspath(pin_dir)), 1)

    def test_ignore_appends_unique_resolved_path(self):
        target = os.path.join(self._tmpdir.name, "noise-folder")
        status, data = self.request("POST", "/api/ignore", {"path": target})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        resolved = os.path.abspath(os.path.expanduser(target))
        self.assertIn(resolved, data["config"].get("ignored_paths") or [])

        status2, again = self.request("POST", "/api/ignore", {"path": target})
        self.assertEqual(status2, 200)
        self.assertTrue(again.get("ok"))
        ignored = again["config"].get("ignored_paths") or []
        self.assertEqual(ignored.count(resolved), 1)

        on_disk = self._read_disk_config()
        self.assertEqual(on_disk.get("ignored_paths"), [resolved])

    def test_patch_and_delete_project(self):
        status, data = self.request(
            "PATCH",
            "/api/projects/test-sample-alpha",
            {"status": "paused", "summary": "patched by test"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["project"]["status"], "paused")
        self.assertEqual(data["project"]["summary"], "patched by test")
        self.assertEqual(data["project"]["name"], "Alpha Cork Test")

        on_disk = self._read_disk_config()
        self.assertEqual(on_disk["projects"][0]["status"], "paused")

        status_hot, hot = self.request(
            "PATCH",
            "/api/projects/test-sample-alpha",
            {"hot": True},
        )
        self.assertEqual(status_hot, 200)
        self.assertTrue(hot.get("ok"))
        self.assertTrue(hot["project"].get("hot"))
        on_disk = self._read_disk_config()
        self.assertTrue(on_disk["projects"][0].get("hot"))

        status_color, painted = self.request(
            "PATCH",
            "/api/projects/test-sample-alpha",
            {"color": "#f4b6c8"},
        )
        self.assertEqual(status_color, 200)
        self.assertTrue(painted.get("ok"))
        self.assertEqual(painted["project"]["color"], "#f4b6c8")

        status_bad_color, bad_color = self.request(
            "PATCH",
            "/api/projects/test-sample-alpha",
            {"color": "#00ff00"},
        )
        self.assertEqual(status_bad_color, 200)
        self.assertFalse(bad_color.get("ok"))
        on_disk = self._read_disk_config()
        self.assertEqual(on_disk["projects"][0]["color"], "#f4b6c8")

        status_hot_off, cold = self.request(
            "PATCH",
            "/api/projects/test-sample-alpha",
            {"hot": False},
        )
        self.assertEqual(status_hot_off, 200)
        self.assertTrue(cold.get("ok"))
        self.assertFalse(cold["project"].get("hot"))

        status_bad, bad = self.request(
            "PATCH",
            "/api/projects/test-sample-alpha",
            {"status": "on-fire"},
        )
        self.assertEqual(status_bad, 200)
        self.assertFalse(bad.get("ok"))
        self.assertIn("status", (bad.get("error") or "").lower())
        on_disk = self._read_disk_config()
        self.assertEqual(on_disk["projects"][0]["status"], "paused")

        status2, deleted = self.request(
            "DELETE", "/api/projects/test-sample-alpha"
        )
        self.assertEqual(status2, 200)
        self.assertTrue(deleted.get("ok"))
        self.assertEqual(deleted["config"].get("projects"), [])
        on_disk = self._read_disk_config()
        self.assertEqual(on_disk.get("projects"), [])

    def test_unpin_clears_tracked_on_rescan(self):
        pin_dir = os.path.join(self._tmpdir.name, "unpin-app")
        os.mkdir(pin_dir)
        with open(os.path.join(pin_dir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# Unpin App\n")
        with open(os.path.join(pin_dir, "package.json"), "w", encoding="utf-8") as fh:
            fh.write('{"name":"unpin-app"}\n')

        status, added = self.request(
            "POST",
            "/api/projects",
            {"name": "Unpin App", "path": pin_dir, "summary": "temp pin"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(added.get("ok"))
        pid = added["project"]["id"]
        saved_path = added["project"]["path"]

        def _named(scan_body, name):
            return [
                c
                for c in scan_body.get("candidates") or []
                if os.path.basename((c.get("path") or "").rstrip(os.sep)) == name
            ]

        status, scan1 = self.request(
            "POST", "/api/scan", {"path": pin_dir, "max_depth": 0}
        )
        self.assertEqual(status, 200)
        self.assertTrue(scan1.get("ok"), scan1)
        tracked = _named(scan1, "unpin-app")
        self.assertTrue(tracked, scan1)
        self.assertTrue(tracked[0].get("tracked"))

        status, deleted = self.request("DELETE", "/api/projects/" + pid)
        self.assertEqual(status, 200)
        self.assertTrue(deleted.get("ok"))
        leftover = [
            p.get("path")
            for p in deleted["config"].get("projects") or []
            if p.get("path")
        ]
        self.assertNotIn(saved_path, leftover)
        self.assertNotIn(
            saved_path, deleted["config"].get("ignored_paths") or []
        )

        status, scan2 = self.request(
            "POST", "/api/scan", {"path": pin_dir, "max_depth": 0}
        )
        self.assertEqual(status, 200)
        self.assertTrue(scan2.get("ok"), scan2)
        again = _named(scan2, "unpin-app")
        self.assertTrue(again, scan2)
        self.assertFalse(again[0].get("tracked"))

    def test_restore_samples_keeps_real_pins_and_does_not_duplicate(self):
        pin_dir = os.path.join(self._tmpdir.name, "real-pin")
        os.mkdir(pin_dir)
        status, added = self.request(
            "POST",
            "/api/projects",
            {"name": "Real Pin", "path": pin_dir, "summary": "keep me"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(added.get("ok"))

        status, first = self.request("POST", "/api/samples/restore", {})
        self.assertEqual(status, 200)
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(first.get("restored"), 6)
        names = [p.get("name") for p in first["config"].get("projects") or []]
        ids = [p.get("id") for p in first["config"].get("projects") or []]
        self.assertIn("Starlight Notes", names)
        self.assertIn("Real Pin", names)
        self.assertIn("test-sample-alpha", ids)
        self.assertEqual(ids.count("sample-starlight"), 1)
        starlight = [
            p for p in first["config"]["projects"] if p.get("id") == "sample-starlight"
        ][0]
        self.assertTrue(starlight.get("sample"))
        self.assertIsNone(starlight.get("path"))

        on_disk = self._read_disk_config()
        disk_ids = [p.get("id") for p in on_disk.get("projects") or []]
        self.assertIn("sample-harbor", disk_ids)
        self.assertIn(added["project"]["id"], disk_ids)

        status, second = self.request("POST", "/api/samples/restore", {})
        self.assertEqual(status, 200)
        self.assertTrue(second.get("ok"))
        ids2 = [p.get("id") for p in second["config"].get("projects") or []]
        self.assertEqual(ids2.count("sample-starlight"), 1)
        self.assertEqual(ids2.count("sample-attic"), 1)
        self.assertIn(added["project"]["id"], ids2)

    def test_unknown_api_is_json_404(self):
        status, data = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)
        self.assertIsInstance(data, dict)
        self.assertFalse(data.get("ok"))
        self.assertTrue(data.get("error"))

    def test_scan_sample_tree_when_scanner_ready(self):
        if not scan_module_ready():
            self.skipTest("scan.py is not ready")
        if not os.path.isdir(SAMPLE_TREE):
            self.skipTest("fixtures/sample-tree is missing")
        status, data = self.request(
            "POST", "/api/scan", {"path": SAMPLE_TREE, "max_depth": 4}
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        if not data.get("ok"):
            self.fail("scan of fixtures/sample-tree failed: %s" % data)
        candidates = data.get("candidates") or []
        joined = " ".join(
            (c.get("path") or "") + " " + (c.get("name") or "")
            for c in candidates
        )
        self.assertIn("python-lib", joined)
        for candidate in candidates:
            path = candidate.get("path") or ""
            self.assertNotIn("notes-only", path)
            self.assertNotIn("node_modules", path)


if __name__ == "__main__":
    unittest.main()
