#!/usr/bin/env python3
"""Repository-scoped handoff storage (macOS/Linux, Python 3.9+ stdlib)."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid


STATUS_ENTRY_LIMIT = 100
STATUS_JSON_BYTE_LIMIT = 8192
LOCK_WAIT_SECONDS = 5.0


def diagnostic(message):
    """Diagnostics remain printable even with strict ASCII stderr settings."""
    data = (message + "\n").encode("utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr.buffer.write(data)
        sys.stderr.buffer.flush()
    else:
        sys.stderr.write(data.decode("utf-8"))


def notice(message):
    # An unavailable diagnostics channel cannot undo a successful publication.
    try:
        diagnostic("note: " + message)
    except OSError:
        pass


def print_path(path):
    """Paths are filesystem bytes, independent of PYTHONIOENCODING."""
    data = os.fsencode(path) + b"\n"
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write(os.fsdecode(data))
        sys.stdout.flush()


def git_environment():
    return dict(os.environ, LC_ALL="C")


def git(cwd, *args, absent_codes=(), allow_non_repo=False):
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", *args], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=git_environment(),
            encoding="utf-8", errors="surrogateescape", check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError("Git is required to determine repository identity; install it or restore PATH") from exc
    if result.returncode == 0:
        return result.stdout.removesuffix("\n")
    if result.returncode in absent_codes:
        return None
    no_repo = result.stderr.startswith((
        "fatal: not a git repository (or any of the parent directories): .git",
        "fatal: not a git repository (or any parent up to mount point ",
    ))
    markers = any(os.path.lexists(parent / ".git") for parent in (cwd, *cwd.parents))
    if allow_non_repo and result.returncode == 128 and no_repo and not markers and not os.environ.get("GIT_DIR"):
        return None
    detail = result.stderr.strip()[:1024] or f"exit {result.returncode}"
    raise ValueError(f"Git {' '.join(args)} failed: {detail}")


def capture_status(cwd):
    """Count all status entries while retaining a bounded, JSON-sized prefix."""
    selected, total, used, full = [], 0, 8, False
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ["git", "--no-optional-locks", "-c", "core.quotepath=true", "status",
             "--porcelain=v1", "--untracked-files=normal"],
            cwd=cwd, env=git_environment(), stdout=subprocess.PIPE, stderr=errors,
            encoding="utf-8", errors="surrogateescape",
        )
        try:
            for line in process.stdout:
                entry = line.removesuffix("\n")
                total += 1
                # Include JSON escaping, indentation, commas, and newlines.
                size = len(json.dumps(entry, ensure_ascii=True)) + 6
                if not full and len(selected) < STATUS_ENTRY_LIMIT and used + size <= STATUS_JSON_BYTE_LIMIT:
                    selected.append(entry)
                    used += size
                else:
                    full = True
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
        if code:
            errors.seek(0)
            detail = errors.read(1024).decode("utf-8", errors="backslashreplace").strip()
            raise ValueError(f"Git status failed (exit {code}): {detail}")
    return {
        "dirty": bool(total), "status_porcelain": selected,
        "status_total_entries": total, "status_omitted_entries": total - len(selected),
        "status_truncated": len(selected) != total,
    }


class Workspace:
    def __init__(self, cwd=None):
        self.cwd = Path(cwd or Path.cwd()).resolve()
        # Older Git returns a relative common directory; resolve it ourselves.
        common = git(self.cwd, "rev-parse", "--git-common-dir", allow_non_repo=True)
        if common == "":
            raise ValueError("Git returned an empty common directory")
        self.common = (self.cwd / common).resolve() if common else None
        if self.common is not None and not self.common.is_dir():
            raise ValueError(f"Git returned an invalid common directory: {self.common}")
        identity = self.common or self.cwd
        label = (identity.parent.name if identity.name == ".git" else identity.name)
        readable = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip(".-")[:60] or "repo"
        kind = b"git\0" if self.common else b"directory\0"
        digest = hashlib.sha256(kind + os.fsencode(identity)).hexdigest()[:20]
        self.name = f"{readable}-{digest}"
        # Match the previous helper's basename, including separate Git dirs.
        self.legacy_name = self.common.parent.name if self.common else self.cwd.name

    def source_state(self):
        bare = git(self.cwd, "rev-parse", "--is-bare-repository") == "true" if self.common else False
        top = git(self.cwd, "rev-parse", "--show-toplevel") if self.common and not bare else None
        status = capture_status(self.cwd) if top else {
            "dirty": None, "status_porcelain": None, "status_total_entries": None,
            "status_omitted_entries": None, "status_truncated": None,
        }
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "repository_id": self.name,
            "git_common_dir": str(self.common) if self.common else None,
            "worktree": str(Path(top).resolve()) if top else str(self.cwd),
            "branch": git(self.cwd, "symbolic-ref", "--quiet", "--short", "HEAD", absent_codes=(1,)) if self.common else None,
            "commit": git(self.cwd, "rev-parse", "--verify", "--quiet", "HEAD", absent_codes=(1,)) if self.common else None,
            **status,
        }


@contextmanager
def locked(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Keep the inode: removing a lock file can let contenders lock different
    # inodes. The OS releases flock even if a writer is killed.
    with (directory / ".handoff.lock").open("ab") as lock:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"timed out waiting for handoff lock in {directory}; retry after the writer finishes")
                time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def link_unique(source, directory, name):
    """Publish a complete file without replacing any existing destination."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    candidate = directory / name
    for _ in range(100):
        try:
            os.link(source, candidate)
            if candidate.name != name:
                notice(f"existing path {str(directory / name)!r} preserved; saved separate handoff to {str(candidate)!r}")
            return candidate
        except FileExistsError:
            original = Path(name)
            candidate = directory / f"{original.stem}-{uuid.uuid4().hex}{original.suffix}"
    raise FileExistsError(f"could not allocate a unique handoff name in {directory}")


def publish(directory, name, write):
    """Stage bytes outside the active *.md set, then publish atomically."""
    with locked(directory):
        fd, temporary = tempfile.mkstemp(prefix=".handoff-", suffix=".tmp", dir=directory)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as output:
                write(output)
                output.flush()
                os.fsync(output.fileno())
            return link_unique(temporary, directory, name)
        finally:
            temporary.unlink(missing_ok=True)


def regular_file(path):
    # Resolve the parent, but reject a final symlink instead of following it.
    path = Path(path).expanduser().absolute()
    path = path.parent.resolve() / path.name
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"not a regular handoff file: {path}")
    return path


def archive_directory(directory):
    directory = directory.resolve()
    target = (directory / "archive").resolve()
    if target == directory or target in directory.parents:
        raise ValueError("archive directory must not resolve to the source directory or an ancestor")
    return target


def archive(file):
    file = regular_file(file)
    target = archive_directory(file.parent)
    # Keep the original inode alive while waiting, including across replacement.
    with file.open("rb") as input_file, locked(file.parent):
        original = os.fstat(input_file.fileno())
        # Another archiver may have moved it while this process waited.
        regular_file(file)
        current = file.stat()
        if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
            raise ValueError(f"handoff changed while waiting to archive: {file}")
        # Copy before removing the source. If interrupted between publication
        # and removal, later edits to the active file cannot change history.
        destination = publish(target, file.name,
                              lambda output: shutil.copyfileobj(input_file, output))
        try:
            file.unlink()
        except OSError as exc:
            raise OSError(f"archive saved at {destination}, but source remains at {file}: {exc}") from exc
        return destination


def active_files(directory):
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for file in entries:
        if file.suffix != ".md":
            continue
        try:
            info = file.lstat()
        except FileNotFoundError:
            continue  # A concurrent archive may have removed it.
        if stat.S_ISREG(info.st_mode):
            result.append((file, info.st_mtime_ns))
    return result


def storage_root():
    configured = os.environ.get("AGENT_HANDOFF_ROOT")
    try:
        return Path(configured or Path.home() / ".agents/handoffs").expanduser().resolve()
    except RuntimeError as exc:
        raise ValueError("cannot determine handoff storage; set AGENT_HANDOFF_ROOT to an absolute path") from exc


def legacy_notice(root, workspace):
    legacy = root / workspace.legacy_name
    try:
        if legacy.resolve() == (root / workspace.name).resolve():
            return
        found = active_files(legacy) or active_files(legacy / "archive")
    except (OSError, RuntimeError):
        notice(f"could not inspect legacy directory {str(legacy)!r}; use handoff.sh legacy-dir to check it manually")
        return
    if found:
        notice(f"legacy handoffs exist at {str(legacy)!r}; inspect with handoff.sh legacy-dir and import only after verifying the repository and task")


def template(workspace, slug, goal):
    return (f"# Handoff: {slug}\n\n"
            f"- **Goal**: {goal}\n"
            "- **Done**:\n  - [ ] TODO\n"
            "- **Outstanding**:\n  - [ ] TODO\n"
            "- **Next step**: TODO\n"
            "- **Key files**:\n  - TODO\n"
            "- **Gotchas**:\n  - TODO\n\n"
            "## Source state\n\n```json\n"
            + json.dumps(workspace.source_state(), indent=2, ensure_ascii=True)
            + "\n```\n")


def parse_args(argv):
    # Goal words are opaque text, including --help or other flag-like words.
    if argv[:1] == ["new"] and len(argv) >= 2:
        return argparse.Namespace(command="new", slug=argv[1], goal=argv[2:])
    parser = argparse.ArgumentParser(prog="handoff.sh")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("repo", "print the collision-resistant repository key"),
        ("dir", "print this repository's handoff directory"),
        ("legacy-dir", "print the old basename directory for manual inspection"),
        ("latest", "print newest active handoff; succeed with no output if absent"),
        ("list", "list active handoffs for this repository"),
    ):
        commands.add_parser(name, help=help_text)
    new = commands.add_parser("new", help="create a handoff template and print its path")
    new.add_argument("slug")
    new.add_argument("goal", nargs="*")
    commands.add_parser("archive", help="archive a file without overwriting history").add_argument("file")
    migrate = commands.add_parser("import", help="copy an explicitly verified legacy handoff; keep its source")
    migrate.add_argument("file")
    migrate.add_argument("--archived", action="store_true", help="import into history rather than active handoffs")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "new":
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug) or len(args.slug) > 100:
                raise ValueError("slug must be kebab-case (lowercase letters/digits, single hyphens; at most 100 characters)")
            goal = " ".join(args.goal) or "TODO"
            try:
                goal.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("goal must be valid UTF-8 text; decode or replace invalid input bytes before retrying") from exc
        if args.command == "archive":
            print_path(archive(args.file))
            return 0
        workspace = Workspace()
        if args.command == "repo":
            print(workspace.name)
            return 0
        root = storage_root()
        directory = root / workspace.name
        if args.command == "dir":
            print_path(directory)
        elif args.command == "legacy-dir":
            print_path(root / workspace.legacy_name)
        elif args.command == "new":
            content = template(workspace, args.slug, goal).encode("utf-8")
            name = f"{datetime.now().date().isoformat()}-{args.slug}.md"
            print_path(publish(directory, name, lambda output: output.write(content)))
        elif args.command == "import":
            source = regular_file(args.file)
            if source.suffix != ".md":
                raise ValueError("import requires a Markdown (.md) handoff")
            target = archive_directory(directory) if args.archived else directory
            with source.open("rb") as input_file:
                print_path(publish(target, source.name, lambda output: shutil.copyfileobj(input_file, output)))
        else:
            files = active_files(directory)
            if args.command == "latest":
                if files:
                    print_path(max(files, key=lambda entry: (entry[1], entry[0].name))[0])
            else:
                for file, _ in sorted(files):
                    print_path(file)
            if not files:
                legacy_notice(root, workspace)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        diagnostic(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
