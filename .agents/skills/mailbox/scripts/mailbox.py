#!/usr/bin/env python3
"""Shared local mailbox transport (macOS/Linux, Python standard library only)."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


POLL_SECONDS = 2
MAX_SECONDS = 2147483647


class MailboxError(Exception):
    pass


def validate_name(name, label="name"):
    if not name or name in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise MailboxError(
            f"invalid {label} {name!r}; use only letters, digits, dot, underscore, and hyphen"
        )
    return name


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def seconds(value):
    normalized = value.lstrip("0") or "0"
    if (not re.fullmatch(r"[0-9]+", value) or len(normalized) > 10
            or int(normalized) > MAX_SECONDS):
        raise argparse.ArgumentTypeError(f"seconds must be an integer from 0 to {MAX_SECONDS}")
    return int(normalized)


def parse_args(argv):
    argv = sys.argv[1:] if argv is None else argv
    # Message words are opaque payload, including --help and other flag-like
    # text. Do not let argument parsing turn a send into a false success.
    if argv[:1] == ["send"]:
        return argparse.Namespace(command="send", payload=argv[1:])
    parser = argparse.ArgumentParser(prog="mail.sh")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("iam", help="register a session identity").add_argument("name")
    commands.add_parser("send", help="publish a complete message").add_argument("payload", nargs="*")
    for command in ("read", "recover", "clean"):
        commands.add_parser(command).add_argument("name", nargs="?")
    for command in ("wait", "monitor"):
        timed = commands.add_parser(command)
        timed.add_argument("values", nargs="*", metavar="name-or-seconds")
        timed.add_argument("--name", dest="name")
        timed.add_argument("--seconds", type=seconds)
    if argv and argv[0] in ("iam", "read", "recover", "clean", "wait", "monitor"):
        # Address words are positional even when they start with '-'. Only
        # the two documented timed-command options are reserved; '--' quotes
        # literal option names. Global `mail.sh --help` remains available.
        command, options, values = argv[0], [], []
        words = iter(argv[1:])
        for word in words:
            if word == "--":
                values.extend(words)
                break
            if command in ("wait", "monitor") and word in ("--name", "--seconds"):
                try:
                    value = next(words)
                except StopIteration:
                    parser.error(f"{word} requires a value")
                options.append(f"{word}={value}")
            elif command in ("wait", "monitor") and word.startswith(("--name=", "--seconds=")):
                options.append(word)
            else:
                values.append(word)
        argv = [command, *options, "--", *values]
    args = parser.parse_args(argv)
    if args.command not in ("wait", "monitor"):
        return args
    values = args.values
    if len(values) > 2:
        parser.error("expected [name] [seconds]")
    try:
        if len(values) == 2:
            if args.name is not None or args.seconds is not None:
                parser.error("do not combine two positional arguments with --name or --seconds")
            args.name, duration = values
            args.seconds = seconds(duration)
        elif values:
            value = values[0]
            if args.name is not None:
                if args.seconds is not None:
                    parser.error("unexpected positional argument")
                args.seconds = seconds(value)
            elif args.seconds is None and re.fullmatch(r"[0-9]+", value):
                args.seconds = seconds(value)
            else:
                args.name = value
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    return args


class Mailbox:
    def __init__(self, root=None):
        configured = root if root is not None else os.environ.get("AGENT_MAILBOX_ROOT")
        self.root = Path(configured) if configured else Path.home() / ".agents/mailbox"
        if not self.root.is_absolute():
            raise MailboxError("AGENT_MAILBOX_ROOT must be an absolute path for an isolated test or sandbox")

    def resolve_name(self, explicit=None):
        name = explicit or os.environ.get("MAILBOX_FROM")
        if not name:
            sid = os.environ.get("MAILBOX_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
            if sid:
                validate_name(sid, "session id")
                identity = self.root / ".who" / sid
                if identity.is_file():
                    name = identity.read_text()
        if not name:
            raise MailboxError("no identity; run mail.sh iam <name> or set MAILBOX_FROM")
        return validate_name(name)

    def directory(self, name):
        return self.root / validate_name(name)

    def prepare(self, name):
        directory = self.directory(name)
        for state in ("inbox", "pending", "inflight", "read"):
            (directory / state).mkdir(mode=0o700, parents=True, exist_ok=True)
        return directory

    def register(self, name):
        validate_name(name)
        self.prepare(name)
        sid = os.environ.get("MAILBOX_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
        if not sid:
            print(f"mailbox initialized for {name!r} at '{self.directory(name)}'")
            print(f"no session id available; set MAILBOX_FROM={name} on each mail.sh call instead")
            return
        validate_name(sid, "session id")
        who = self.root / ".who"
        who.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".identity-", dir=who)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(name)
            os.replace(temporary, who / sid)
        finally:
            Path(temporary).unlink(missing_ok=True)
        print(f"registered as {name!r} for this session")

    def publish(self, sender, recipient, body):
        validate_name(sender, "sender")
        validate_name(recipient, "recipient")
        directory = self.directory(recipient)
        staging = directory / ".staging"
        inbox = directory / "inbox"
        staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        inbox.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Staging and inbox share a filesystem. Readers never scan staging.
        message_id = f"{stamp()}-{sender}-{uuid.uuid4().hex}.txt"
        temporary = staging / message_id
        created = False
        try:
            with temporary.open("xb") as stream:
                created = True
                stream.write(body)
            os.replace(temporary, inbox / message_id)
        finally:
            if created:
                temporary.unlink(missing_ok=True)
        return message_id

    def send(self, payload):
        parts = payload.split(None, 1)
        if len(parts) != 2 or not parts[1].strip():
            raise MailboxError("recipient and message required; pass '<to> <message...>' as arguments or stdin")
        try:
            recipient = parts[0].decode("ascii")
        except UnicodeDecodeError:
            raise MailboxError("recipient must contain only ASCII letters, digits, dot, underscore, and hyphen")
        self.publish(self.resolve_name(), recipient, parts[1])
        print(f"sent to {recipient!r}")

    @contextmanager
    def locked(self, name, blocking=True):
        directory = self.prepare(name)
        # Never unlink this file: contenders must lock the same inode. The OS
        # releases the lock on exit/SIGKILL; message recovery is a separate step.
        with (directory / ".consumer.lock").open("ab") as lock:
            try:
                flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                fcntl.flock(lock, flags)
            except BlockingIOError:
                yield None
                return
            try:
                yield directory
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def message_metadata(path):
        match = re.fullmatch(r"([0-9]{8}T[0-9]{6}Z)-([A-Za-z0-9._-]+)-([A-Za-z0-9]+)\.txt", path.name)
        if not match:
            raise MailboxError("expected a timestamp-sender-id.txt message filename")
        timestamp, sender, _identifier = match.groups()
        validate_name(sender, "message sender")
        if not path.is_file():
            raise MailboxError("not a regular message file")
        return timestamp, sender

    @staticmethod
    def render(path, timestamp, sender):
        header = f"--- from {sender} at {timestamp} ---\n".encode()
        return header + path.read_bytes() + b"\n"

    @staticmethod
    def output(record):
        sys.stdout.buffer.write(record)
        # Archive only after the complete output was accepted by this stream.
        sys.stdout.buffer.flush()

    @staticmethod
    def notify(name, sender):
        if os.environ.get("MAILBOX_NOTIFY") != "1" or not shutil.which("osascript"):
            return
        try:
            subprocess.run(
                ["osascript", "-e", f'display notification "New mailbox message from {sender}" with title "Mailbox: {name}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def consume(self, name, observe=False, blocking=True):
        with self.locked(name, blocking) as directory:
            if directory is None:
                return 0
            if any((directory / "inflight").glob("*.txt")):
                raise MailboxError(f"interrupted delivery for {name!r}; inspect inflight messages, then run mail.sh recover {name}")
            sources = list((directory / "inbox").glob("*.txt"))
            if not observe:
                sources += list((directory / "pending").glob("*.txt"))
            delivered = 0
            for source in sorted(sources, key=lambda path: path.name):
                try:
                    timestamp, sender = self.message_metadata(source)
                except MailboxError as exc:
                    print(f"warning: skipping {str(source)!r}: {exc}", file=sys.stderr)
                    continue
                claim = directory / "inflight" / source.name
                os.replace(source, claim)
                record = self.render(claim, timestamp, sender)
                if observe:
                    # The exclusive consumer lock also keeps event records from
                    # interleaving when multiple monitors share this mailbox.
                    event_header = record.split(b"\n", 1)[0][:-4]
                    event = event_header + f" seen {stamp()} file {claim.name} ---\n".encode()
                    event += record.split(b"\n", 1)[1]
                    with (directory / "events.log").open("ab") as log:
                        log.write(event)
                    self.output(record)
                    os.replace(claim, directory / "pending" / claim.name)
                    self.notify(name, sender)
                else:
                    self.output(record)
                    os.replace(claim, directory / "read" / claim.name)
                delivered += 1
            return delivered

    def recover(self, name):
        with self.locked(name, blocking=False) as directory:
            if directory is None:
                raise MailboxError(f"mailbox {name!r} is busy; stop the consumer before recovering")
            claims = list((directory / "inflight").glob("*.txt"))
            for claim in claims:
                destination = directory / "inbox" / claim.name
                if destination.exists():
                    raise MailboxError(f"refusing to overwrite existing message {destination}")
                os.replace(claim, destination)
            print(f"recovered {len(claims)} message(s) for {name!r}; previous output or effects may replay")

    def timed(self, name, duration, observe=False):
        self.prepare(name)
        if observe:
            # Log tailers can attach as soon as the monitor announces readiness.
            with (self.directory(name) / "events.log").open("ab"):
                pass
            print(f"monitoring mailbox for {name!r} at '{self.root}'", flush=True)
            print(f"event log: {self.directory(name) / 'events.log'}", flush=True)
        deadline = None if duration is None else time.monotonic() + duration
        while True:
            count = self.consume(name, observe=observe, blocking=False)
            if count and not observe:
                return 0
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                if observe:
                    return 0
                print(f"timed out waiting for mail for {name!r}")
                return 124
            time.sleep(POLL_SECONDS if remaining is None else min(POLL_SECONDS, remaining))

    def clean(self, name):
        if name == "all":
            directories = [] if not self.root.exists() else [
                path for path in self.root.iterdir() if path.is_dir() and path.name != ".who"
            ]
            for directory in directories:
                shutil.rmtree(directory)
            print(f"cleaned {len(directories)} mailbox(es)")
        else:
            name = self.resolve_name(name)
            directory = self.directory(name)
            if directory.exists():
                shutil.rmtree(directory)
            print(f"cleaned mailbox for {name!r}")


def main(argv=None):
    args = parse_args(argv)
    os.umask(0o077)
    try:
        mailbox = Mailbox()
        if args.command == "iam":
            mailbox.register(args.name)
        elif args.command == "send":
            if args.payload:
                payload = " ".join(args.payload).encode()
            elif sys.stdin.isatty():
                payload = b""
            else:
                payload = sys.stdin.buffer.read()
            mailbox.send(payload)
        elif args.command == "clean":
            mailbox.clean(args.name)
        else:
            name = mailbox.resolve_name(args.name)
            if args.command == "read":
                if not mailbox.consume(name):
                    print(f"no new mail for {name!r}")
            elif args.command == "recover":
                mailbox.recover(name)
            else:
                return mailbox.timed(name, args.seconds, observe=args.command == "monitor")
        return 0
    except BrokenPipeError:
        # Avoid a second failed flush during interpreter shutdown.
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
        print("error: output closed; the current claim is retained for mail.sh recover", file=sys.stderr)
        return 1
    except (MailboxError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
