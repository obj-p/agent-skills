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
import uuid


def git(cwd, *args):
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", *args], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", errors="surrogateescape", check=False,
        )
    except FileNotFoundError:
        return None
    return result.stdout.removesuffix("\n") if result.returncode == 0 else None


class Workspace:
    def __init__(self, cwd=None):
        self.cwd = Path(cwd or Path.cwd()).resolve()
        common = git(self.cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        self.common = (self.cwd / common).resolve() if common else None
        identity = self.common or self.cwd
        label = (identity.parent.name if identity.name == ".git" else identity.name)
        readable = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip(".-")[:60] or "repo"
        kind = b"git\0" if self.common else b"directory\0"
        digest = hashlib.sha256(kind + os.fsencode(identity)).hexdigest()[:20]
        self.name = f"{readable}-{digest}"
        # Match the previous helper's basename, including separate Git dirs.
        self.legacy_name = self.common.parent.name if self.common else self.cwd.name

    def source_state(self):
        top = git(self.cwd, "rev-parse", "--show-toplevel") if self.common else None
        status = git(self.cwd, "status", "--porcelain=v1", "--untracked-files=all") if top else None
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "repository_id": self.name,
            "git_common_dir": str(self.common) if self.common else None,
            "worktree": str(Path(top).resolve()) if top else str(self.cwd),
            "branch": git(self.cwd, "symbolic-ref", "--quiet", "--short", "HEAD") if self.common else None,
            "commit": git(self.cwd, "rev-parse", "--verify", "HEAD") if self.common else None,
            "dirty": bool(status) if status is not None else None,
            "status_porcelain": status.splitlines() if status is not None else None,
        }


@contextmanager
def locked(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Keep the inode: removing a lock file can let contenders lock different
    # inodes. The OS releases flock even if a writer is killed.
    with (directory / ".handoff.lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
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


def archive(file):
    file = regular_file(file)
    original = file.stat()
    with locked(file.parent):
        # Another archiver may have moved it while this process waited.
        regular_file(file)
        current = file.stat()
        if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
            raise ValueError(f"handoff changed while waiting to archive: {file}")
        # Copy before removing the source. If interrupted between publication
        # and removal, later edits to the active file cannot change history.
        with file.open("rb") as input_file:
            destination = publish(file.parent / "archive", file.name,
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
        workspace = Workspace()
        root = Path(os.environ.get("AGENT_HANDOFF_ROOT") or Path.home() / ".agents/handoffs").expanduser().resolve()
        directory = root / workspace.name
        if args.command == "repo":
            print(workspace.name)
        elif args.command == "dir":
            print(directory)
        elif args.command == "legacy-dir":
            print(root / workspace.legacy_name)
        elif args.command == "new":
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug) or len(args.slug) > 100:
                raise ValueError("slug must be kebab-case (lowercase letters/digits, single hyphens; at most 100 characters)")
            content = template(workspace, args.slug, " ".join(args.goal) or "TODO").encode("utf-8")
            name = f"{datetime.now().date().isoformat()}-{args.slug}.md"
            print(publish(directory, name, lambda output: output.write(content)))
        elif args.command == "archive":
            print(archive(args.file))
        elif args.command == "import":
            source = regular_file(args.file)
            if source.suffix != ".md":
                raise ValueError("import requires a Markdown (.md) handoff")
            target = directory / "archive" if args.archived else directory
            with source.open("rb") as input_file:
                print(publish(target, source.name, lambda output: shutil.copyfileobj(input_file, output)))
        else:
            files = active_files(directory)
            if args.command == "latest":
                if files:
                    print(max(files, key=lambda entry: (entry[1], entry[0].name))[0])
            else:
                for file, _ in sorted(files):
                    print(file)
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
