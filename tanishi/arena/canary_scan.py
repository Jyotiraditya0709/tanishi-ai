"""The canary scan (AR4): find any sealed-task canary outside the Sealed Vault.

Every sealed task carries a canary, `TNSH-CANARY-` followed by 32 lowercase hex digits, in its prompt, its fixtures and
its check. Nothing outside the vault should ever hold one. A canary in the repo, a worktree, the Core State, a skill
or an agent transcript means sealed text leaked to where something that learns can read it.

This file is standalone: standard library only, Python 3.9+, no `tanishi` import. The vault setup installs a copy of
it as root (`/usr/local/libexec/tanishi-vault/canary_scan.py`) and runs it nightly from a root LaunchDaemon, so
neither the agent's user nor the vault's user can change the scan or its report. On a hit it writes the report,
creates the leak marker (the vault refuses every read while it exists; only the human removes it) and sends one
Telegram message. It never prints a secret: the bot token is read from a root-only file and used only in the request.

    python3 canary_scan.py --target DIR [--target DIR ...] [--git REPO --git-as USER] [--exclude NAME ...]
                           [--report FILE] [--leak-file FILE] [--telegram-env FILE]

Exit 0: clean. Exit 1: canary found. Exit 2: the scan itself failed (a target missing is reported, not fatal).
Tests and docs must never contain a whole canary: build one at run time (`PREFIX + "ab" * 16`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

PREFIX = "TNSH-CANARY-"
CANARY = re.compile(re.escape(PREFIX).encode() + rb"[0-9a-f]{32}")
CANARY_LEN = len(PREFIX) + 32
CHUNK = 1 << 20
MAX_FILE_BYTES = 512 * 1024 * 1024  # bigger files are read up to this and noted as truncated
DEFAULT_EXCLUDES = (".venv", "venv", "node_modules", ".git", "__pycache__", ".pytest_cache", ".ruff_cache")
TOKEN_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "BOT_TOKEN")
CHAT_KEYS = ("TELEGRAM_CHAT_ID", "TELEGRAM_OWNER_ID", "OWNER_ID", "CHAT_ID", "ALLOWED_USER_ID")


def scan_stream(read, limit=None):
    """Canaries in a byte stream given by `read(n)`: list of (offset, canary). Hits across chunk edges are found."""
    hits = []
    seen = set()
    carry = b""
    base = 0  # offset of carry[0]
    total = 0
    while True:
        want = CHUNK if limit is None else min(CHUNK, limit - total)
        if want <= 0:
            break
        chunk = read(want)
        if not chunk:
            break
        total += len(chunk)
        buf = carry + chunk
        for m in CANARY.finditer(buf):
            off = base + m.start()
            if off not in seen:
                seen.add(off)
                hits.append((off, m.group().decode()))
        keep = min(len(buf), CANARY_LEN - 1)
        base += len(buf) - keep
        carry = buf[len(buf) - keep:]
    return hits


def scan_bytes(data):
    view = memoryview(data)
    pos = [0]

    def read(n):
        out = view[pos[0]:pos[0] + n].tobytes()
        pos[0] += len(out)
        return out

    return scan_stream(read)


def scan_file(path):
    """(hits, bytes_read, truncated) for one regular file, never following a link."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        hits = scan_stream(f.read, limit=MAX_FILE_BYTES)
    return hits, min(size, MAX_FILE_BYTES), size > MAX_FILE_BYTES


def scan_tree(root, excludes=DEFAULT_EXCLUDES):
    """Walk `root` without following links; scan every regular file. Returns (hits, stats)."""
    hits = []
    stats = {"files": 0, "bytes": 0, "truncated": 0, "unreadable": 0}
    skip = {e.lower() for e in excludes}
    if os.path.isfile(root) and not os.path.islink(root):
        walker = [(os.path.dirname(root), [], [os.path.basename(root)])]
    else:
        walker = os.walk(root, followlinks=False)
    for dirpath, dirnames, filenames in walker:
        dirnames[:] = [d for d in dirnames if d.lower() not in skip]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
                if not stat.S_ISREG(st.st_mode):
                    continue
                found, n, truncated = scan_file(path)
            except OSError:
                stats["unreadable"] += 1
                continue
            stats["files"] += 1
            stats["bytes"] += n
            stats["truncated"] += truncated
            hits.extend({"path": path, "offset": off, "canary": c} for off, c in found)
    return hits, stats


def scan_git(repo, as_user=None):
    """Every object in a repo's store (all commits, deleted files and dangling objects too), via
    `git cat-file --batch-all-objects --batch`, run as `as_user` (never as root: the repo's config is not ours)."""
    cmd = ["git", "-c", "core.fsmonitor=false", "-C", repo, "cat-file", "--batch-all-objects", "--batch"]
    if as_user:
        cmd = ["sudo", "-n", "-u", as_user, "-H", *cmd]
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_TERMINAL_PROMPT": "0", "LANG": "C"}
    hits = []
    stats = {"objects": 0, "bytes": 0}
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    out = proc.stdout
    try:
        while True:
            header = out.readline()
            if not header:
                break
            parts = header.split()
            if len(parts) != 3 or not parts[2].isdigit():
                continue  # "<sha> missing" and the like
            sha, size = parts[0].decode("ascii", "replace"), int(parts[2])
            left = [size]

            def read(n, left=left):
                n = min(n, left[0])
                data = out.read(n) if n > 0 else b""
                left[0] -= len(data)
                return data

            found = scan_stream(read)
            while left[0] > 0 and read(CHUNK):
                pass
            out.read(1)  # the newline after each object
            stats["objects"] += 1
            stats["bytes"] += size
            hits.extend({"path": f"{repo} (git object {sha})", "offset": off, "canary": c} for off, c in found)
    finally:
        out.close()
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"git cat-file exited {code} for {repo}")
    return hits, stats


def read_env_file(path):
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            values[key] = value.strip().strip('"').strip("'")
    return values


def send_telegram(env_path, text, timeout=20):
    """One message to the owner. Returns None or the error's class name; never the token."""
    try:
        values = read_env_file(env_path)
        token = next((values[k] for k in TOKEN_KEYS if values.get(k)), None)
        chat = next((values[k] for k in CHAT_KEYS if values.get(k)), None)
        if not token or not chat:
            return "MissingTelegramSettings"
        cafile = "/etc/ssl/cert.pem" if os.path.exists("/etc/ssl/cert.pem") else None
        ctx = ssl.create_default_context(cafile=cafile)
        body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            resp.read()
        return None
    except Exception as e:  # noqa: BLE001 - report the kind only; a message could carry the URL with the token
        return type(e).__name__


def write_json_atomic(path, data, mode=0o644):
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".scan-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def run_scan(targets, git_repos=(), git_as=None, excludes=DEFAULT_EXCLUDES):
    started = time.time()
    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)), "targets": [], "hits": [],
              "errors": []}
    for target in targets:
        entry = {"path": target}
        if not os.path.lexists(target):
            entry["missing"] = True
        else:
            hits, stats = scan_tree(target, excludes)
            entry.update(stats)
            report["hits"].extend(hits)
        report["targets"].append(entry)
    for repo in git_repos:
        entry = {"git": repo}
        try:
            hits, stats = scan_git(repo, git_as)
            entry.update(stats)
            report["hits"].extend(hits)
        except (OSError, RuntimeError) as e:
            entry["error"] = type(e).__name__
            report["errors"].append(f"git scan failed for {repo}")
        report["targets"].append(entry)
    report["seconds"] = round(time.time() - started, 1)
    report["clean"] = not report["hits"]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find sealed-task canaries outside the Sealed Vault.")
    parser.add_argument("--target", action="append", default=[], help="file or folder to scan (repeatable)")
    parser.add_argument("--git", action="append", default=[], help="repo whose whole object store to scan")
    parser.add_argument("--git-as", help="run git as this user (required when running as root)")
    parser.add_argument("--exclude", action="append", default=[], help="folder name to skip (adds to the defaults)")
    parser.add_argument("--report", help="write the JSON report here (atomically, 0644)")
    parser.add_argument("--leak-file", help="create this file when a canary is found")
    parser.add_argument("--telegram-env", help="env file with the bot token and chat id; one alert on a hit")
    args = parser.parse_args(argv)
    if not args.target and not args.git:
        parser.error("nothing to scan")
    if args.git and hasattr(os, "geteuid") and os.geteuid() == 0 and not args.git_as:
        parser.error("--git-as is required when running as root: never run git as root on a repo we do not own")
    try:
        report = run_scan(args.target, args.git, args.git_as, tuple(DEFAULT_EXCLUDES) + tuple(args.exclude))
    except Exception as e:  # noqa: BLE001
        print(f"canary scan failed: {type(e).__name__}", file=sys.stderr)
        return 2
    if report["hits"] and args.leak_file and not os.path.exists(args.leak_file):
        write_json_atomic(args.leak_file, {"found": report["started"], "hits": len(report["hits"])})
    if report["hits"] and args.telegram_env:
        where = sorted({h["path"] for h in report["hits"]})
        listing = "\n".join(where[:10])
        text = (f"Tanishi canary scan: {len(report['hits'])} sealed canary hit(s) in {len(where)} place(s). "
                f"Sealed reads are locked until you clear the leak.\n{listing}")
        report["alert_error"] = send_telegram(args.telegram_env, text)
    if args.report:
        write_json_atomic(args.report, report)
    print(json.dumps({"clean": report["clean"], "hits": len(report["hits"]), "errors": len(report["errors"]),
                      "seconds": report["seconds"]}))
    if report["hits"]:
        return 1
    return 2 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
