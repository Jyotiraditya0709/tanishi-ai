"""The three ways a candidate is confined: a sandbox-exec profile (macOS), Landlock + seccomp (Linux), Docker.

Each is default-deny: the candidate may read the interpreter, the system libraries and the paths the caller names in
`read_paths`, and may read and write its own scratch folder. Nothing else, so a guarded place the sandbox has never
heard of (a vault copy, a Frontier store, a traces folder) is unreadable because it is not on the list. Inside the
read paths, entries named like the guarded places (GUARDED_DIRS, `.env`, `.env.*`) or like a database (`*.db`,
`*.sqlite*`) are refused as well, in any letter case.

Only Docker and Landlock hide the user's other processes from a candidate. sandbox-exec cannot: any process of the
same user can read the argv and environment of every other one (sysctl KERN_PROCARGS2), and no profile rule gates
it. So code a model wrote (`trusted=False`) never runs under sandbox-exec; see `pick_backend`.
"""
from __future__ import annotations

import ctypes
import json
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
_LINUX_READ = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc",
               # machine facts only; /proc/<pid> stays closed, since a process's environ there can hold keys
               "/proc/meminfo", "/proc/cpuinfo", "/proc/loadavg", "/proc/uptime", "/proc/version", "/proc/stat")
_LINUX_DEVICES = ("/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom")


def is_guarded_name(name: str) -> bool:
    """A guarded place (vault, builder_vault, traces, .ssh, .env, .env.*), in any letter case."""
    n = name.lower()
    return n in GUARDED_DIRS or n == ".env" or n.startswith(".env.")


def is_refused_name(name: str) -> bool:
    """Never readable inside a read path: a guarded name or a database (`*.db`, `*.sqlite*`), in any letter case."""
    n = name.lower()
    return is_guarded_name(n) or n.endswith(".db") or ".sqlite" in n


def is_not_copied_name(name: str) -> bool:
    """Left out of a worktree copy: a refused name, `.git` or any `.env*`, in any letter case."""
    n = name.lower()
    return is_refused_name(n) or n == ".git" or n.startswith(".env")


def refused_under(root: str) -> list[str]:
    """Every entry under `root` (not following links) with a refused name; a refused folder is not entered."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for n in [*dirnames, *filenames]:
            if is_refused_name(n):
                found.append(os.path.join(dirpath, n))
        dirnames[:] = [d for d in dirnames if not is_refused_name(d)]
    return found


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


def _ci(word: str) -> str:
    """`word` as a regex that matches it in any letter case (SBPL regexes have no case-insensitive flag)."""
    return "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else c for c in word)


# Refused at any depth: guarded folders, `.env`/`.env.*`, and databases (`*.db`, `*.sqlite*`, file or folder).
_DARWIN_REFUSED = (
    r'(regex #"/\.' + _ci("env") + r'(\.[^/]*)?$")',
    r'(regex #"/(' + "|".join(_ci(d).replace(".", r"\.") for d in GUARDED_DIRS) + r')(/|$)")',
    r'(regex #"/[^/]*\.' + _ci("db") + r'(/|$)")',
    r'(regex #"/[^/]*\.' + _ci("sqlite") + r'[^/]*(/|$)")',
)


def darwin_profile(scratch: str, read_paths: list[str], network: bool, tag: tuple[str, str],
                   list_paths: list[str] = ()) -> str:
    """A sandbox-exec (SBPL) profile. Later rules win, so the refused-name denial comes after the read grants and
    the scratch grant comes last (a candidate may name its own folders `traces` or `.env`). `list_paths` are folders
    whose names may be listed but whose contents stay closed (the parent of a granted package, for imports).

    With `network`, only IP sockets are granted (and the system DNS resolver's socket): never a unix socket by path,
    so a docker.sock or an ssh-agent stays out of reach."""
    granted = sorted({p for r in (*interpreter_paths(), *read_paths) for p in (r, os.path.realpath(r))})
    listed = sorted({p for r in list_paths for p in (r, os.path.realpath(r))})
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
    if listed:
        lines.append("(allow file-read-data " + " ".join(f"(literal {_q(p)})" for p in listed) + ")")
    if network:
        lines += [
            "(allow system-socket)",
            "(allow network-outbound (remote ip))",
            "(allow network-bind (local ip))",
            '(allow network-outbound (literal "/private/var/run/mDNSResponder"))',
            '(allow mach-lookup (global-name "com.apple.dnssd.service"))',
        ]
    lines += [
        "(deny file-read* file-write* " + " ".join(_DARWIN_REFUSED) + ")",
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


def landlock_rules(scratch: str, read_paths: list[str], list_paths: list[str] = ()) -> list[list[str]]:
    """Rules for _launch.py. Landlock has no deny rule, so a folder holding a refused name (at any depth) is granted
    for listing only and its other children one by one; a folder with no refused name below it is granted whole.
    Refused names show up in listings but cannot be opened."""
    rules = [[p, "ro"] for p in (*_LINUX_READ, *interpreter_paths())]
    rules += [[d, "dev"] for d in _LINUX_DEVICES]
    rules += [[p, "list"] for p in list_paths]
    for root in read_paths:
        rules += _carved(root, refused_under(root)) if os.path.isdir(root) else [[root, "ro"]]
    rules.append([scratch, "rw"])
    return rules


def _carved(root: str, refused: list[str]) -> list[list[str]]:
    """Grant `root` read-only except the `refused` paths below it."""
    if not refused:
        return [[root, "ro"]]
    rules = [[root, "list"]]
    try:
        children = sorted(os.listdir(root))
    except OSError:
        return rules
    for c in children:
        p = os.path.join(root, c)
        if p in refused:
            continue
        below = [r for r in refused if r.startswith(p + os.sep)]
        if below and os.path.isdir(p) and not os.path.islink(p):
            rules += _carved(p, below)
        else:
            rules.append([p, "ro"])
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


# Runs first inside the container (image python, -I -S): the one passed fd rides on the container's stdout (the
# candidate's own stdout goes to /dev/null), the environment is cut to the names the sandbox set (the image's own
# variables and Docker's HOSTNAME are dropped), and an alarm ends the run at the wall limit even if no one on the
# host is left to kill it.
_DOCKER_SHIM = """\
import json, os, signal, sys
cfg = json.loads(sys.argv[1])
for fd in cfg["fds"]:
    os.dup2(1, fd)
if cfg["fds"]:
    os.dup2(os.open("/dev/null", os.O_WRONLY), 1)
env = {k: os.environ[k] for k in cfg["env"] if k in os.environ}
env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
signal.alarm(cfg["wall"])
try:
    os.execvpe(sys.argv[2], sys.argv[2:], env)
except OSError as e:
    sys.stderr.write(f"sandbox: cannot run {sys.argv[2]!r} ({e.strerror})\\n")
os._exit(127)
"""


def docker_argv(*, name: str, image: str, scratch: str, cwd: str, env_names: list[str], read_paths: list[str],
                network: bool, cpu_seconds: int, memory_mb: int, argv: list[str], wall_seconds: int = 3600,
                max_processes: int = 256, max_disk_mb: int = 64, fds: tuple[int, ...] = ()) -> list[str]:
    """`docker run` for one candidate. The scratch folder is mounted at its own path, so HOME and cwd are the same
    inside and out; read paths are mounted read-only with their refused entries (any depth) covered. The host's
    interpreter is replaced by the image's, since a macOS binary cannot run in a Linux container. `fds` (at most
    one) is made to exist in the container on its stdout; see _DOCKER_SHIM."""
    if len(fds) > 1:
        raise ValueError("the Docker backend passes at most one file descriptor")
    cmd = [shutil.which("docker") or "docker", "run", "--rm", "-i", "--init", "--name", name,
           "--network", "bridge" if network else "none",
           "--memory", f"{memory_mb}m", "--memory-swap", f"{memory_mb}m",
           "--ulimit", f"cpu={cpu_seconds}:{cpu_seconds + 1}", "--ulimit", "core=0",
           "--ulimit", f"fsize={max_disk_mb * 1024 * 1024}", "--pids-limit", str(max_processes),
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
           "--tmpfs", f"/tmp:rw,size={max_disk_mb}m", "-v", f"{scratch}:{scratch}:rw", "-w", cwd]
    if hasattr(os, "getuid"):
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
    for root in read_paths:
        cmd += ["-v", f"{root}:{root}:ro"]
        if os.path.isdir(root):
            for p in refused_under(root):
                cmd += ["--tmpfs", f"{p}:ro,size=1k"] if os.path.isdir(p) else ["-v", f"/dev/null:{p}:ro"]
    for n in env_names:
        cmd += ["-e", n]  # the value comes from the client's environment, never the command line
    head = argv[0]
    if os.path.realpath(head) == os.path.realpath(sys.executable) or os.path.basename(head).startswith("python"):
        head = "python3"
    shim = json.dumps({"fds": list(fds), "env": list(env_names), "wall": max(1, int(wall_seconds))})
    return [*cmd, image, "python3", "-I", "-S", "-c", _DOCKER_SHIM, shim, head, *argv[1:]]


class SandboxUnavailable(RuntimeError):
    """No backend on this machine can confine the code as asked, so it is not run."""


def pick_backend(prefer_docker: bool, network: bool, trusted: bool | None = None) -> str:
    """The backend this machine can confine a candidate with, or SandboxUnavailable: nothing ever runs unconfined.

    `trusted=False` (code a model wrote) needs a backend that hides the user's other processes: Docker, or Landlock
    on Linux. On macOS that means Docker whatever `prefer_docker` says, and no Docker means SandboxUnavailable (fail
    closed), never sandbox-exec. `trusted=True` or unset may use sandbox-exec (code the system itself wrote)."""
    untrusted = trusted is False
    if prefer_docker and docker_available():
        return "docker"
    if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC) and not untrusted:
        return "sandbox-exec"
    if sys.platform.startswith("linux") and platform.machine() in ("x86_64", "aarch64"):
        abi = landlock_abi()
        if abi >= 4 or (abi >= 2 and network):
            return "landlock"
    if untrusted and docker_available():
        return "docker"
    if untrusted and sys.platform == "darwin":
        raise SandboxUnavailable("code a model wrote needs Docker on macOS: sandbox-exec cannot hide other "
                                 "processes' arguments and environment from it, so it is not run (decision 0025)")
    raise SandboxUnavailable("no sandbox is available here (need sandbox-exec, Landlock ABI 4+ or Docker); "
                             "refusing to run candidate code unconfined")
