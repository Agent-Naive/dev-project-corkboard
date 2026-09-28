#!/usr/bin/env python3
"""Unit tests for scan.py — build real temp trees, drive scan.scan_path."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import scan  # noqa: E402


SKIP_NAMES = [
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

GIT_ENV = os.environ.copy()
GIT_ENV["GIT_TERMINAL_PROMPT"] = "0"
GIT_ENV["GIT_AUTHOR_NAME"] = "Corkboard Test"
GIT_ENV["GIT_AUTHOR_EMAIL"] = "corkboard-test@example.invalid"
GIT_ENV["GIT_COMMITTER_NAME"] = "Corkboard Test"
GIT_ENV["GIT_COMMITTER_EMAIL"] = "corkboard-test@example.invalid"
GIT_ENV["GIT_CONFIG_NOSYSTEM"] = "1"


def make_config(**overrides):
    scan_cfg = {
        "roots": [],
        "max_depth": 4,
        "max_results": 150,
        "stat_timeout_seconds": 5,
        "skip_names": list(SKIP_NAMES),
    }
    extra_scan = overrides.pop("scan", None) or {}
    scan_cfg.update(extra_scan)
    cfg = {
        "scan": scan_cfg,
        "ignored_paths": overrides.pop("ignored_paths", []),
        "projects": overrides.pop("projects", []),
    }
    cfg.update(overrides)
    return cfg


def write_file(path, content=""):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def git_run(repo, args, check=True):
    cmd = [
        "git",
        "-c",
        "user.name=Corkboard Test",
        "-c",
        "user.email=corkboard-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-C",
        repo,
    ] + list(args)
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=GIT_ENV,
        check=check,
    )


def git_init_commit(repo, message="Initial commit"):
    os.makedirs(repo, exist_ok=True)
    init = subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=GIT_ENV,
    )
    if init.returncode != 0:
        subprocess.run(
            ["git", "init"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=GIT_ENV,
            check=True,
        )
    git_run(repo, ["add", "-A"])
    git_run(repo, ["commit", "-m", message, "--allow-empty"])


def candidate_by_basename(result, name):
    matches = [
        c
        for c in (result.get("candidates") or [])
        if os.path.basename(c.get("path") or "") == name
    ]
    return matches[0] if matches else None


def basenames(result):
    return [
        os.path.basename(c.get("path") or "")
        for c in (result.get("candidates") or [])
    ]


def paths_of(result):
    return [c.get("path") or "" for c in (result.get("candidates") or [])]


class ScanPathTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="corkboard-scan-")
        self.config = make_config()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _root(self, *parts):
        return os.path.join(self._tmpdir, *parts)

    def test_git_root_with_readme_is_high_confidence(self):
        proj = self._root("shiny-app")
        write_file(
            os.path.join(proj, "README.md"),
            "# Shiny App\n\nA real project.\n",
        )
        write_file(
            os.path.join(proj, "package.json"),
            '{"name":"shiny-app","version":"1.0.0"}\n',
        )
        git_init_commit(proj, "Initial commit")

        result = scan.scan_path(self._tmpdir, self.config)
        self.assertTrue(result["ok"], result.get("error"))
        cand = candidate_by_basename(result, "shiny-app")
        self.assertIsNotNone(cand, "git root + README/manifest must be a candidate")
        self.assertEqual(cand["confidence"], "high")
        self.assertIn("git", cand["signals"])
        self.assertIn("readme", cand["signals"])
        self.assertTrue(cand.get("git"))
        self.assertEqual(cand["git"].get("last_commit_subject"), "Initial commit")
        self.assertGreaterEqual(cand["score"], 5)

    def test_notes_only_not_a_candidate(self):
        notes = self._root("notes-only")
        write_file(os.path.join(notes, "todo.txt"), "buy more pushpins\n")
        write_file(os.path.join(notes, "ideas.txt"), "maybe later\n")

        result = scan.scan_path(self._tmpdir, self.config)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIsNone(candidate_by_basename(result, "notes-only"))
        self.assertNotIn("notes-only", basenames(result))

    def test_node_modules_package_not_a_candidate(self):
        app = self._root("ongoing-app")
        write_file(os.path.join(app, "README.md"), "# Ongoing App\n")
        write_file(
            os.path.join(app, "package.json"),
            '{"name":"ongoing-app","version":"0.1.0"}\n',
        )
        fake = self._root("ongoing-app", "node_modules", "fake-pkg")
        write_file(
            os.path.join(fake, "package.json"),
            '{"name":"fake-pkg","version":"1.0.0"}\n',
        )
        write_file(os.path.join(fake, "README.md"), "# Fake Pkg\n")
        write_file(os.path.join(fake, "src", "index.js"), "module.exports = 1;\n")

        result = scan.scan_path(self._tmpdir, self.config)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIsNotNone(candidate_by_basename(result, "ongoing-app"))
        self.assertIsNone(candidate_by_basename(result, "fake-pkg"))
        for path in paths_of(result):
            self.assertNotIn(
                os.path.join("node_modules", "fake-pkg"),
                path,
            )
            parts = path.split(os.sep)
            self.assertNotIn("node_modules", parts)

    def test_pyproject_src_readme_no_git_is_medium(self):
        lib = self._root("python-lib")
        write_file(
            os.path.join(lib, "pyproject.toml"),
            '[project]\nname = "python-lib"\nversion = "0.0.1"\n',
        )
        write_file(
            os.path.join(lib, "README.md"),
            "# Python Lib\n\nTiny fixture library.\n",
        )
        write_file(os.path.join(lib, "src", "python_lib", "__init__.py"), "")

        result = scan.scan_path(self._tmpdir, self.config)
        self.assertTrue(result["ok"], result.get("error"))
        cand = candidate_by_basename(result, "python-lib")
        self.assertIsNotNone(
            cand, "pyproject.toml + src/ + README with no git must be a candidate"
        )
        self.assertEqual(cand["confidence"], "medium")
        self.assertNotIn("git", cand["signals"])
        self.assertIsNone(cand.get("git"))
        self.assertIn("readme", cand["signals"])
        self.assertIn("pyproject.toml", cand["signals"])

    def test_missing_path_ok_false_does_not_raise(self):
        missing = os.path.join(self._tmpdir, "no-such-dir", "really-missing")
        result = scan.scan_path(missing, self.config)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("error"))
        self.assertIsInstance(result["error"], str)
        self.assertEqual(result.get("candidates"), [])
        # Completely absent volume-style path
        result2 = scan.scan_path(
            "/this/path/does/not/exist-corkboard-xyz-404",
            self.config,
        )
        self.assertFalse(result2["ok"])
        self.assertTrue(result2.get("error"))
        self.assertEqual(result2.get("candidates"), [])

    def test_corkboard_self_id(self):
        cb = self._root("dev-project-corkboard")
        write_file(
            os.path.join(cb, "README.md"),
            "# Project Corkboard\n\nThis app — a local corkboard.\n",
        )
        write_file(
            os.path.join(cb, "server.py"),
            'print("Project Corkboard fixture")\n',
        )

        self.assertTrue(scan.is_corkboard_dir(cb))

        result = scan.scan_path(self._tmpdir, self.config)
        self.assertTrue(result["ok"], result.get("error"))
        cand = candidate_by_basename(result, "dev-project-corkboard")
        self.assertIsNotNone(cand, "corkboard fixture must be a candidate")
        self.assertTrue(cand["is_corkboard"])
        self.assertEqual(cand["name"], "Project Corkboard")
        self.assertNotEqual(cand.get("color"), "#cfe8ff")
        self.assertEqual(cand["confidence"], "high")

        # server.py + README mention also identifies a differently-named copy
        copy = self._root("some-other-folder")
        write_file(
            os.path.join(copy, "README.md"),
            "Welcome to Project Corkboard the app.\n",
        )
        write_file(os.path.join(copy, "server.py"), "# stub\n")
        self.assertTrue(scan.is_corkboard_dir(copy))

    def test_skip_list_does_not_descend_venv(self):
        app = self._root("real-app")
        write_file(os.path.join(app, "README.md"), "# Real App\n")
        write_file(
            os.path.join(app, "pyproject.toml"),
            '[project]\nname = "real-app"\n',
        )
        hidden = self._root("real-app", ".venv", "lib", "sneaky-pkg")
        write_file(
            os.path.join(hidden, "package.json"),
            '{"name":"sneaky-pkg"}\n',
        )
        write_file(os.path.join(hidden, "README.md"), "# Sneaky\n")
        write_file(os.path.join(hidden, "src", "mod.py"), "x = 1\n")

        result = scan.scan_path(self._tmpdir, self.config)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIsNotNone(candidate_by_basename(result, "real-app"))
        self.assertIsNone(candidate_by_basename(result, "sneaky-pkg"))
        for path in paths_of(result):
            parts = path.split(os.sep)
            self.assertNotIn(".venv", parts)

    def test_max_results_sets_truncated(self):
        for i in range(5):
            d = self._root("proj-%d" % i)
            write_file(os.path.join(d, "README.md"), "# Proj %d\n" % i)
            write_file(
                os.path.join(d, "package.json"),
                '{"name":"proj-%d"}\n' % i,
            )
        cfg = make_config(scan={"max_results": 2, "max_depth": 4})
        result = scan.scan_path(self._tmpdir, cfg)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["candidates"]), 2)

        cfg_big = make_config(scan={"max_results": 150})
        result_big = scan.scan_path(self._tmpdir, cfg_big)
        self.assertTrue(result_big["ok"])
        self.assertFalse(result_big["truncated"])
        self.assertGreaterEqual(len(result_big["candidates"]), 5)

    def test_sample_tree_fixture(self):
        sample = os.path.join(ROOT, "fixtures", "sample-tree")
        if not os.path.isdir(sample):
            self.skipTest("fixtures/sample-tree is not present")
        result = scan.scan_path(sample, self.config)
        self.assertTrue(result["ok"], result.get("error"))
        names = set(basenames(result))
        self.assertIn("ongoing-app", names)
        self.assertIn("python-lib", names)
        self.assertNotIn("notes-only", names)
        self.assertNotIn("fake-pkg", names)
        for path in paths_of(result):
            self.assertNotIn("%snode_modules%s" % (os.sep, os.sep), path)
            self.assertFalse(path.rstrip(os.sep).endswith("node_modules"))

        cb = candidate_by_basename(result, "dev-project-corkboard")
        self.assertIsNotNone(cb)
        self.assertTrue(cb["is_corkboard"])
        self.assertEqual(cb["name"], "Project Corkboard")

    def test_scan_path_never_raises_on_not_a_dir(self):
        f = self._root("a-file.txt")
        write_file(f, "hello\n")
        result = scan.scan_path(f, self.config)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("error"))
        self.assertEqual(result.get("candidates"), [])

    def test_glob_expands_matching_directories(self):
        alpha = self._root("dev-project-alpha")
        beta = self._root("dev-project-beta")
        other = self._root("other-app")
        write_file(os.path.join(alpha, "README.md"), "# Alpha\n")
        write_file(os.path.join(alpha, "package.json"), '{"name":"alpha"}\n')
        write_file(os.path.join(beta, "Cargo.toml"), '[package]\nname="beta"\n')
        write_file(os.path.join(other, "README.md"), "# Other\n")
        write_file(os.path.join(other, "package.json"), '{"name":"other"}\n')
        write_file(self._root("dev-project-notes.txt"), "not a directory\n")

        pattern = os.path.join(self._tmpdir, "dev-project-*")
        result = scan.scan_path(pattern, self.config, max_depth=0)
        self.assertTrue(result["ok"], result.get("error"))
        names = set(basenames(result))
        self.assertIn("dev-project-alpha", names)
        self.assertIn("dev-project-beta", names)
        self.assertNotIn("other-app", names)
        self.assertNotIn("dev-project-notes.txt", names)
        roots = set(os.path.basename(r.rstrip(os.sep)) for r in result.get("roots") or [])
        self.assertEqual(roots, {"dev-project-alpha", "dev-project-beta"})
        self.assertEqual(result.get("root"), os.path.abspath(pattern))

    def test_glob_no_match_is_soft_failure(self):
        pattern = os.path.join(self._tmpdir, "no-such-prefix-zzzz-*")
        result = scan.scan_path(pattern, self.config)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertIn("No directories matched", result.get("error") or "")
        self.assertEqual(result.get("candidates"), [])

    def test_glob_unmounted_prefix_is_soft_failure(self):
        pattern = "/Volumes/DoesNotExistCorkboardDrive/dev-project-*"
        result = scan.scan_path(pattern, self.config)
        self.assertFalse(result["ok"])
        self.assertIn("not found or unmounted", (result.get("error") or "").lower())
        self.assertEqual(result.get("candidates"), [])

    def test_scan_path_list_merges_shell_expanded_dirs(self):
        alpha = self._root("dev-project-alpha")
        beta = self._root("dev-project-beta")
        write_file(os.path.join(alpha, "README.md"), "# Alpha\n")
        write_file(os.path.join(alpha, "package.json"), '{"name":"alpha"}\n')
        write_file(os.path.join(beta, "Cargo.toml"), '[package]\nname="beta"\n')
        result = scan.scan_path_list(
            [alpha, beta], self.config, max_depth=0
        )
        self.assertTrue(result["ok"], result.get("error"))
        names = set(basenames(result))
        self.assertIn("dev-project-alpha", names)
        self.assertIn("dev-project-beta", names)


class IsCorkboardDirTests(unittest.TestCase):
    def test_basename_alone(self):
        self.assertTrue(
            scan.is_corkboard_dir("/tmp/does-not-need-to-exist/dev-project-corkboard")
        )
        self.assertFalse(scan.is_corkboard_dir("/tmp/some-other-app"))


if __name__ == "__main__":
    unittest.main()
