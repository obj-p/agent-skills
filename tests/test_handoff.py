#!/usr/bin/env python3
"""Handoff regressions use only temporary repositories and storage.

Run: python3 -B -m unittest discover -s tests -p 'test_handoff.py'
"""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / ".agents/skills/handoff/scripts"
SPEC = importlib.util.spec_from_file_location("handoff", SCRIPTS / "handoff.py")
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)

PAUSED_WRITE = r'''
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location("handoff", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def write(output):
    output.write(b"partial template")
    output.flush()
    pathlib.Path(sys.argv[3]).touch()
    time.sleep(30)
m.publish(pathlib.Path(sys.argv[2]), "unfinished.md", write)
'''


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="agent-skills-handoff-test-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.root = self.work / "handoffs 'quoted' $literal"
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith("GIT_") or key in ("BASH_ENV", "ENV", "PYTHONPATH", "PYTHONHOME"):
                self.env.pop(key)
        self.env.update(AGENT_HANDOFF_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE="1",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.repo = self.make_repo("a/widget")

    def git(self, cwd, *args):
        result = subprocess.run(["git", *args], cwd=cwd, env=self.env, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def make_repo(self, name):
        repo = self.work / name
        repo.mkdir(parents=True)
        self.git(repo, "init", "-q", "-b", "main")
        (repo / "tracked.txt").write_text("original\n")
        self.git(repo, "add", "tracked.txt")
        self.git(repo, "-c", "core.hooksPath=" + os.devnull, "commit", "-qm", "fixture")
        return repo

    def run_handoff(self, *args, cwd=None, check=True, env=None):
        result = subprocess.run(["bash", str(SCRIPTS / "handoff.sh"), *map(str, args)],
                                cwd=cwd or self.repo, env=env or self.env, text=True,
                                capture_output=True, timeout=10)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def new(self, slug="same-task", goal="fixture goal", cwd=None):
        return Path(self.run_handoff("new", slug, goal, cwd=cwd).stdout.strip())

    def state(self, file):
        return json.loads(file.read_text().split("```json\n", 1)[1].split("\n```", 1)[0])

    def test_unrelated_same_basename_repositories_are_isolated(self):
        other = self.make_repo("b/widget")
        first = self.new(goal="only A")
        self.assertEqual(self.run_handoff("latest", cwd=other).stdout, "")
        second = self.new(goal="only B", cwd=other)
        self.assertNotEqual(first.parent, second.parent)
        self.assertRegex(first.parent.name, r"^widget-[0-9a-f]{20}$")
        self.assertEqual(self.run_handoff("latest").stdout.strip(), str(first))
        self.assertEqual(self.run_handoff("list", cwd=other).stdout.splitlines(), [str(second)])

    def test_nested_and_symlinked_paths_select_same_repository(self):
        nested = self.repo / "src/deep"
        nested.mkdir(parents=True)
        alias = self.work / "alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        first = self.new()
        for cwd in (nested, alias, alias / "src/deep"):
            self.assertEqual(self.run_handoff("dir", cwd=cwd).stdout.strip(), str(first.parent))
            self.assertEqual(self.run_handoff("latest", cwd=cwd).stdout.strip(), str(first))

    def test_worktrees_share_namespace_and_capture_source_worktree(self):
        other = self.work / "linked-worktree"
        self.git(self.repo, "worktree", "add", "-q", "-b", "feature", str(other))
        first = self.new()
        (other / "tracked.txt").write_text("changed\n")
        (other / "untracked.txt").write_text("new\n")
        second = self.new(goal="from feature", cwd=other)
        self.assertEqual(first.parent, second.parent)
        state = self.state(second)
        self.assertEqual(state["worktree"], str(other))
        self.assertEqual(state["git_common_dir"], str(self.repo / ".git"))
        self.assertEqual(state["branch"], "feature")
        self.assertEqual(state["commit"], self.git(other, "rev-parse", "HEAD"))
        self.assertTrue(state["dirty"])
        self.assertIn(" M tracked.txt", state["status_porcelain"])
        self.assertIn("?? untracked.txt", state["status_porcelain"])
        self.assertEqual(self.run_handoff("latest").stdout.strip(), str(second))

    def test_clean_detached_and_unborn_source_states(self):
        clean = self.state(self.new())
        self.assertFalse(clean["dirty"])
        self.assertEqual(clean["status_porcelain"], [])
        self.git(self.repo, "checkout", "--detach", "-q")
        detached = self.state(self.new())
        self.assertIsNone(detached["branch"])
        self.assertEqual(detached["commit"], clean["commit"])
        empty = self.work / "unborn"
        empty.mkdir()
        self.git(empty, "init", "-q", "-b", "main")
        unborn = self.state(self.new(cwd=empty))
        self.assertEqual(unborn["branch"], "main")
        self.assertIsNone(unborn["commit"])
        self.assertFalse(unborn["dirty"])

    def test_separate_git_directory_and_bare_repository(self):
        external = self.work / "metadata/custom.git"
        external.parent.mkdir()
        separate = self.work / "separate"
        self.git(self.work, "init", "-q", "--separate-git-dir", str(external), str(separate))
        first = self.new(cwd=separate)
        self.assertEqual(self.state(first)["git_common_dir"], str(external))
        self.assertTrue(first.parent.name.startswith("custom.git-"))
        bare = self.work / "bare.git"
        self.git(self.work, "init", "--bare", "-q", str(bare))
        self.assertEqual(self.state(self.new(cwd=bare))["git_common_dir"], str(bare))

    def test_non_git_directories_are_explicitly_directory_scoped(self):
        one, two = self.work / "plain/widget", self.work / "other/widget"
        one.mkdir(parents=True)
        two.mkdir(parents=True)
        first = self.new(cwd=one)
        self.assertEqual(self.run_handoff("latest", cwd=two).stdout, "")
        state = self.state(first)
        self.assertIsNone(state["git_common_dir"])
        self.assertIsNone(state["dirty"])
        self.assertEqual(state["worktree"], str(one))
        alias = self.work / "plain-alias"
        alias.symlink_to(one, target_is_directory=True)
        self.assertEqual(self.run_handoff("dir", cwd=alias).stdout.strip(), str(first.parent))

    def test_queries_are_successfully_empty_and_do_not_create_storage(self):
        for command in ("latest", "list"):
            self.assertEqual(self.run_handoff(command).stdout, "")
        for command in ("repo", "dir", "legacy-dir"):
            self.assertTrue(self.run_handoff(command).stdout)
        self.assertFalse(self.root.exists())
        active = self.new()
        self.run_handoff("archive", active)
        before = sorted(self.root.rglob("*"))
        self.assertEqual(self.run_handoff("latest").stdout, "")
        self.assertEqual(self.run_handoff("list").stdout, "")
        self.assertEqual(before, sorted(self.root.rglob("*")))

    def test_latest_orders_by_mtime_and_ignores_non_active_entries(self):
        first, second = self.new(), self.new()
        os.utime(first, ns=(2000000000, 2000000000))
        os.utime(second, ns=(1000000000, 1000000000))
        (first.parent / "directory.md").mkdir()
        (first.parent / "link.md").symlink_to(second)
        (first.parent / ".handoff-incomplete.tmp").write_text("unfinished")
        self.assertEqual(self.run_handoff("latest").stdout.strip(), str(first))
        self.assertEqual(set(self.run_handoff("list").stdout.splitlines()), {str(first), str(second)})

    def test_repeated_and_concurrent_creation_preserves_each_goal(self):
        original = self.new(goal="original")
        content = original.read_bytes()
        with ThreadPoolExecutor(max_workers=8) as pool:
            files = list(pool.map(lambda n: self.new(goal=f"goal-{n}"), range(16)))
        self.assertEqual(len(set(files)), 16)
        self.assertEqual(original.read_bytes(), content)
        for index, file in enumerate(files):
            self.assertIn(f"- **Goal**: goal-{index}\n", file.read_text())
            self.assertEqual(self.state(file)["repository_id"], file.parent.name)
        self.assertEqual(len(self.run_handoff("list").stdout.splitlines()), 17)

    def test_repeated_archives_preserve_same_day_same_slug_history(self):
        archived = []
        for index in range(4):
            active = self.new(goal=f"version-{index}")
            content = active.read_bytes()
            destination = Path(self.run_handoff("archive", active).stdout.strip())
            self.assertFalse(active.exists())
            self.assertEqual(destination.read_bytes(), content)
            archived.append(destination)
        self.assertEqual(len(set(archived)), 4)
        for index, file in enumerate(archived):
            self.assertIn(f"version-{index}", file.read_text())
        self.assertEqual(self.run_handoff("latest").stdout, "")

    def test_concurrent_archives_do_not_replace_colliding_destinations(self):
        files = [self.new(goal=f"value-{n}") for n in range(8)]
        archive = files[0].parent / "archive"
        archive.mkdir()
        for file in files:
            (archive / file.name).write_text("existing " + file.name)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda f: self.run_handoff("archive", f), files))
        destinations = [Path(result.stdout.strip()) for result in results]
        self.assertEqual(len(set(destinations)), 8)
        for index, file in enumerate(files):
            self.assertEqual((archive / file.name).read_text(), "existing " + file.name)
            self.assertIn(f"value-{index}", destinations[index].read_text())
            self.assertFalse(file.exists())

    def test_concurrent_archivers_of_one_file_have_one_winner(self):
        file = self.new()
        content = file.read_bytes()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.run_handoff("archive", file, check=False), range(8)))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)
        archived = list((file.parent / "archive").glob("*.md"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), content)

    def test_legacy_records_require_explicit_import_and_remain_recoverable(self):
        legacy = Path(self.run_handoff("legacy-dir").stdout.strip())
        legacy.mkdir(parents=True)
        file = legacy / "2026-09-01-old-task.md"
        file.write_bytes(b"legacy source, identity must be verified\n")
        other = self.make_repo("b/widget")
        for cwd in (self.repo, other):
            self.assertEqual(self.run_handoff("latest", cwd=cwd).stdout, "")
            self.assertEqual(self.run_handoff("list", cwd=cwd).stdout, "")
        imported = Path(self.run_handoff("import", file).stdout.strip())
        again = Path(self.run_handoff("import", file).stdout.strip())
        self.assertNotEqual(imported, again)
        self.assertEqual(imported.read_bytes(), file.read_bytes())
        self.assertEqual(again.read_bytes(), file.read_bytes())
        self.assertEqual(self.run_handoff("latest", cwd=other).stdout, "")
        imported.write_text("edited copy")
        self.assertEqual(file.read_bytes(), b"legacy source, identity must be verified\n")

    def test_import_archived_history_does_not_activate_it(self):
        source = self.work / "old.md"
        source.write_text("old history")
        imported = Path(self.run_handoff("import", source, "--archived").stdout.strip())
        self.assertEqual(imported.parent.name, "archive")
        self.assertEqual(imported.read_text(), source.read_text())
        self.assertEqual(self.run_handoff("latest").stdout, "")

    def test_archive_of_explicit_legacy_path_preserves_existing_history(self):
        legacy = self.work / "legacy"
        (legacy / "archive").mkdir(parents=True)
        source = legacy / "old.md"
        source.write_text("new old")
        (legacy / "archive/old.md").write_text("original old")
        result = Path(self.run_handoff("archive", source).stdout.strip())
        self.assertEqual(result.read_text(), "new old")
        self.assertEqual((legacy / "archive/old.md").read_text(), "original old")

    def test_failed_publication_retains_prior_files_and_hides_partial_template(self):
        directory = self.work / "storage"
        directory.mkdir()
        prior = directory / "task.md"
        prior.write_bytes(b"previous")
        def fail(output):
            output.write(b"partial")
            raise OSError("fixture write failure")
        with self.assertRaises(OSError):
            handoff.publish(directory, "task.md", fail)
        self.assertEqual(prior.read_bytes(), b"previous")
        self.assertEqual([entry[0] for entry in handoff.active_files(directory)], [prior])
        self.assertEqual(list(directory.glob("*.tmp")), [])

    def test_archive_publication_or_unlink_failure_preserves_evidence(self):
        file = self.new()
        content = file.read_bytes()
        with patch.object(handoff.os, "link", side_effect=OSError("fixture link failure")):
            with self.assertRaises(OSError):
                handoff.archive(file)
        self.assertEqual(file.read_bytes(), content)
        unlink = Path.unlink
        def fail_source(path, *args, **kwargs):
            if path == file:
                raise OSError("fixture unlink failure")
            return unlink(path, *args, **kwargs)
        with patch.object(handoff.Path, "unlink", fail_source):
            with self.assertRaisesRegex(OSError, "archive saved at"):
                handoff.archive(file)
        self.assertEqual(file.read_bytes(), content)
        archive = next((file.parent / "archive").glob("*.md"))
        self.assertEqual(archive.read_bytes(), content)
        file.write_bytes(b"later edit")
        self.assertEqual(archive.read_bytes(), content)
        recovered = handoff.archive(file)
        self.assertEqual(recovered.read_bytes(), b"later edit")
        self.assertEqual(archive.read_bytes(), content)

    def test_killed_writer_never_exposes_partial_template_and_releases_lock(self):
        directory = Path(self.run_handoff("dir").stdout.strip())
        ready = self.work / "writer-ready"
        process = subprocess.Popen([sys.executable, "-B", "-c", PAUSED_WRITE,
                                    str(SCRIPTS / "handoff.py"), str(directory), str(ready)],
                                   cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "writer did not reach staging barrier")
            self.assertEqual(self.run_handoff("latest").stdout, "")
            process.send_signal(signal.SIGKILL)
            process.communicate(timeout=5)
            self.assertEqual(self.run_handoff("latest").stdout, "")
            completed = self.new()
            self.assertTrue(completed.is_file())
            self.assertEqual(self.run_handoff("list").stdout.splitlines(), [str(completed)])
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_invalid_inputs_do_not_create_handoffs_or_follow_symlinks(self):
        for slug in ("../escape", "", "--help", "a/b", "a" * 101):
            self.assertNotEqual(self.run_handoff("new", slug, check=False).returncode, 0)
        self.assertFalse(self.root.exists())
        target = self.work / "target.md"
        target.write_text("preserve")
        link = self.work / "link.md"
        link.symlink_to(target)
        for command in ("import", "archive"):
            self.assertNotEqual(self.run_handoff(command, link, check=False).returncode, 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(), "preserve")

    def test_goal_is_literal_and_relative_root_override_is_supported(self):
        env = dict(self.env, AGENT_HANDOFF_ROOT="local-store")
        result = self.run_handoff("new", "literal", "--help", "$(touch unexpected)", env=env)
        file = Path(result.stdout.strip())
        self.assertEqual(file.parent.parent, self.repo / "local-store")
        self.assertIn("--help $(touch unexpected)", file.read_text())
        self.assertFalse((self.repo / "unexpected").exists())


if __name__ == "__main__":
    unittest.main()
