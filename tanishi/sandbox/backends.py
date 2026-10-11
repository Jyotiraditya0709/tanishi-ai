"""The three ways a candidate is confined: a sandbox-exec profile (macOS), Landlock + seccomp (Linux), Docker.

Each is default-deny: the candidate may read the interpreter, the system libraries and the paths the caller names in
`read_paths`, and may read and write its own scratch folder. Nothing else, so a guarded place the sandbox has never
heard of (a vault copy, a Frontier store, a traces folder) is unreadable because it is not on the list. Inside the
read paths, entries named like the guarded places (GUARDED_DIRS, `.env`, `.env.*`) are refused as well.
"""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

GUARDED_DIRS = ("vault", "builder_vault", "traces", ".ssh")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
DOCKER_IMAGE = "python:3.12-slim"

# System locations every process needs to start. Real paths: the kernel checks those, not /tmp or /etc links.
_DARWIN_READ = ("/usr", "/bin", "/sbin", "/System", "/Library/Apple", "/private/etc", "/private/var/db/timezone",
                "/private/var/db/dyld", "/Library/Developer/CommandLineTools", "/Library/Preferences/Logging")
_DARWIN_DEVICES = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/tty", "/dev/dtracehelper")
_DARWIN_SERVICES = ("com.apple.system.opendirectoryd.libinfo", "com.apple.system.notification_center",
                    "com.apple.system.logger", "com.apple.logd", "com.apple.diagnosticd")
_LINUX_READ = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc")
_LINUX_DEVICES = ("/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom")


def is_guarded_name(name: str) -> bool:
    return name in GUARDED_DIRS or name == ".env" or name.startswith(".env.")


def interpreter_paths() -> list[str]:
    """Where this Python and its standard library live, as given and resolved (a venv links to its base)."""
    found: set[str] = set()
    raw = [sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix, os.path.dirname(sys.executable)]
    raw += [p for k in ("stdlib", "platstdlib", "purelib", "platlib") if (p := sysconfig.get_path(k))]
    for p in raw:
        if p and os.path.exists(p):
            found.update({os.path.abspath(p), os.path.realpath(p)})
    exe = os.path.realpath(sys.executable)
    found.add(os.path.dirname(exe))
    return sorted(found)


# --- macOS --------------------------------------------------------------------------------------------------------


def _q(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


_SANDBOX_CHECK = None


def in_darwin_sandbox(pid: int, tag: tuple[str, str]) -> bool:
    """True if `pid` runs under the profile made with `tag`, whatever it did to its environment or session.

    `tag` is two existing files only the sandbox can delete; its profile lets the candidate read the first and not
    the second, so the kernel's answer for both picks out exactly this sandbox's processes (a process with no
    sandbox, a deny-default one or an allow-default one gets a different pair of answers).
    """
    global _SANDBOX_CHECK
    if _SANDBOX_CHECK is None:
        f = ctypes.CDLL("/usr/lib/libSystem.B.dylib").sandbox_check
        f.restype = ctypes.c_int
        f.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]  # then one variadic path
        _SANDBOX_CHECK = f
    f = _SANDBOX_CHECK
    yes, no = tag
    return f(pid, None, 0) == 1 and f(pid, b"file-read-data", 1, yes.encode()) == 0 \
        and f(pid, b"file-read-data", 1, no.encode()) == 1


def darwin_profile(scratch: str, read_paths: list[str], network: bool, tag: tuple[str, str]) -> str:
    """A sandbox-exec (SBPL) profile. Later rules win, so the guarded-name denial comes after the read grants and
    the scratch grant comes last (a candidate may name its own folders `traces` or `.env`)."""
    granted = sorted({p for r in (*interpreter_paths(), *read_paths) for p in (r, os.path.realpath(r))})
    lines = [
        "(version 1)",
        "(deny default)",
        f"(allow file-read* (literal {_q(tag[0])}))",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow signal (target same-sandbox))",
        "(allow process-info* (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow ipc-posix-shm* ipc-posix-sem*)",
        "(allow file-read-metadata)",
        "(allow mach-lookup " + " ".join(f"(global-name {_q(s)})" for s in _DARWIN_SERVICES) + ")",
        '(allow file-read* (literal "/"))',
        "(allow file-read* " + " ".join(f"(subpath {_q(p)})" for p in _DARWIN_READ) + ")",
        '(deny file-read* (subpath "/usr/local"))',  # Homebrew's etc and var hold user data; re-granted if named
        "(allow file-read* " + " ".join(f"(subpath {_q(p)})" for p in granted) + ")",
        "(allow file-read* file-write-data file-ioctl " + " ".join(f"(literal {_q(d)})" for d in _DARWIN_DEVICES)
        + ")",
    ]
    if network:
        lines.append("(allow network* system-socket)")
    lines += [
        '(deny file-read* file-write* (regex #"/\\.env(\\.[^/]*)?$") (regex #"/(vault|builder_vault|traces|\\.ssh)(/|$)"))',
        f"(allow file-read* file-write* (subpath {_q(scratch)}))",
    ]
    return "\n".join(lines) + "\n"


# --- Linux --------------------------------------------------------------------------------------------------------


def landlock_abi() -> int:
    """The kernel's Landlock ABI version, or 0 if there is none."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        abi = libc.syscall(ctypes.c_long(444), None, ctypes.c_long(0), ctypes.c_long(1))
    except (OSError, AttributeError):
        return 0
    return max(int(abi), 0)


def landlock_rules(scratch: str, read_paths: list[str]) -> list[list[str]]:
    """Rules for _launch.py. Landlock has no deny rule, so a read path is granted child by child, leaving out
    guarded names; the path itself is granted for listing only, so guarded names show up but cannot be opened."""
    rules = [[p, "ro"] for p in (*_LINUX_READ, *interpreter_paths())]
    rules += [[d, "dev"] for d in _LINUX_DEVICES]
    for root in read_paths:
        if os.path.isdir(root):
            rules.append([root, "list"])
            try:
                children = os.listdir(root)
            except OSError:
                children = []
            rules += [[os.path.join(root, c), "ro"] for c in children if not is_guarded_name(c)]
        else:
            rules.append([root, "ro"])
    rules.append([scratch, "rw"])
    return rules


# --- Docker -------------------------------------------------------------------------------------------------------

_DOCKER_OK: dict[str, bool] = {}


def docker_config_dir() -> str:
    """The real user's Docker client config, so the client finds its daemon even when HOME was moved."""
    if os.environ.get("DOCKER_CONFIG"):
        return os.environ["DOCKER_CONFIG"]
    try:
        import pwd

        return str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".docker")
    except (ImportError, KeyError):
        return str(Path.home() / ".docker")


def docker_available() -> bool:
    """True if `docker` is on PATH and its daemon answers. Cached per executable."""
    exe = shutil.which("docker")
    if not exe:
        return False
    if exe not in _DOCKER_OK:
        env = dict(os.environ, DOCKER_CONFIG=docker_config_dir())
        try:
            done = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=15,
                                  check=False, env=env)
            _DOCKER_OK[exe] = done.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _DOCKER_OK[exe] = False
    return _DOCKER_OK[exe]


def docker_argv(*, name: str, image: str, scratch: str, cwd: str, env_names: list[str], read_paths: list[str],
                network: bool, cpu_seconds: int, memory_mb: int, argv: list[str]) -> list[str]:
    """`docker run` for one candidate. The scratch folder is mounted at its own path, so HOME and cwd are the same
    inside and out; read paths are mounted read-only with their guarded entries covered. The host's interpreter is
    replaced by the image's, since a macOS binary cannot run in a Linux container."""
    cmd = [shutil.which("docker") or "docker", "run", "--rm", "-i", "--init", "--name", name,
           "--network", "bridge" if network else "none",
           "--memory", f"{memory_mb}m", "--memory-swap", f"{memory_mb}m",
           "--ulimit", f"cpu={cpu_seconds}:{cpu_seconds + 1}", "--ulimit", "core=0", "--pids-limit", "256",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
           "--tmpfs", "/tmp:rw,size=64m", "-v", f"{scratch}:{scratch}:rw", "-w", cwd]
    if hasattr(os, "getuid"):
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
    for root in read_paths:
        cmd += ["-v", f"{root}:{root}:ro"]
        if os.path.isdir(root):
            for c in sorted(os.listdir(root)):
                p = os.path.join(root, c)
                if is_guarded_name(c):
                    cmd += ["--tmpfs", f"{p}:ro,size=1k"] if os.path.isdir(p) else ["-v", f"/dev/null:{p}:ro"]
    for n in env_names:
        cmd += ["-e", n]  # the value comes from the client's environment, never the command line
    head = argv[0]
    if os.path.realpath(head) == os.path.realpath(sys.executable) or os.path.basename(head).startswith("python"):
        head = "python3"
    return [*cmd, image, head, *argv[1:]]


def pick_backend(prefer_docker: bool, network: bool) -> str:
    """The backend this machine can confine a candidate with, or RuntimeError: nothing ever runs unconfined."""
    if prefer_docker and docker_available():
        return "docker"
    if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC):
        return "sandbox-exec"
    if sys.platform.startswith("linux") and platform.machine() in ("x86_64", "aarch64"):
        abi = landlock_abi()
        if abi >= 4 or (abi >= 2 and network):
            return "landlock"
    raise RuntimeError("no sandbox is available here (need sandbox-exec, Landlock ABI 4+ or Docker); "
                       "refusing to run candidate code unconfined")
