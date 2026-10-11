"""Started by tanishi.sandbox as `python -I -S _launch.py CONFIG ARGV...`: lower the limits, confine, then run ARGV
(by exec on macOS, where sandbox-exec is ARGV's head; as a supervising parent on Linux, see _supervise).

Standard library only and run as a script, so nothing from `tanishi` is imported before the confinement holds.
CONFIG is JSON: {"cpu": seconds, "landlock": null | {"rules": [[path, rights], ...], "deny_net": bool}}.
The environment is inherited as the sandbox built it. Any failure to confine exits 126 without running ARGV.
"""
import json
import os
import resource
import sys


def _limit(which: int, soft: int, hard: int) -> None:
    """Lower a limit; the hard value cannot be raised again by the candidate."""
    _, current = resource.getrlimit(which)
    if current != resource.RLIM_INFINITY:
        soft, hard = min(soft, current), min(hard, current)
    resource.setrlimit(which, (soft, hard))


# --- Linux: Landlock (files, TCP) and a seccomp filter (chmod/chown, which Landlock does not cover) ---------------

_FS_EXECUTE, _FS_WRITE_FILE, _FS_READ_FILE, _FS_READ_DIR = 1, 2, 4, 8
_FS_ABI1 = (1 << 13) - 1
_FS_REFER, _FS_TRUNCATE, _FS_IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
_FILE_RIGHTS = _FS_EXECUTE | _FS_WRITE_FILE | _FS_READ_FILE | _FS_TRUNCATE | _FS_IOCTL_DEV
_RIGHTS = {
    "ro": _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR,
    "list": _FS_READ_DIR,
    "dev": _FS_READ_FILE | _FS_WRITE_FILE | _FS_TRUNCATE | _FS_IOCTL_DEV,
    "rw": -1,  # everything the kernel handles
}
_SYS_CREATE, _SYS_ADD, _SYS_RESTRICT = 444, 445, 446
_PR_SET_NO_NEW_PRIVS, _PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 38, 22, 2
# (audit arch, syscalls refused with EPERM, x32 bit) per machine
_SECCOMP = {
    "x86_64": (0xC000003E, (90, 268, 452, 92, 94, 260), 0x40000000),  # chmod fchmodat fchmodat2 chown lchown fchownat
    "aarch64": (0xC00000B7, (53, 452, 54), None),  # fchmodat fchmodat2 fchownat
}


def _landlock(cfg: dict) -> None:
    import ctypes
    import platform

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.prctl.restype = ctypes.c_int

    def check(ret: int, what: str) -> int:
        if ret < 0:
            raise OSError(ctypes.get_errno(), f"{what} failed")
        return ret

    L = ctypes.c_long
    abi = check(libc.syscall(L(_SYS_CREATE), None, L(0), L(1)), "landlock ABI query")
    handled = _FS_ABI1 | (_FS_REFER if abi >= 2 else 0) | (_FS_TRUNCATE if abi >= 3 else 0) | (
        _FS_IOCTL_DEV if abi >= 5 else 0)

    class Attr(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64)]

    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed", ctypes.c_uint64), ("fd", ctypes.c_int32)]

    deny_net = bool(cfg["deny_net"])
    if deny_net and abi < 4:
        raise OSError("this kernel's Landlock cannot deny network access (ABI < 4)")
    attr = Attr(handled, 3 if deny_net else 0)  # BIND_TCP | CONNECT_TCP, with no rule granting either
    size = ctypes.sizeof(Attr) if abi >= 4 else 8
    ruleset = check(libc.syscall(L(_SYS_CREATE), ctypes.byref(attr), L(size), L(0)), "landlock_create_ruleset")
    own = [f"/proc/{os.getpid()}", "ro"]  # this process's own entry, to list its children (see _supervise)
    for path, kind in [*cfg["rules"], own]:
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            rights = handled if _RIGHTS[kind] == -1 else _RIGHTS[kind] & handled
            if not os.path.isdir(path):
                rights &= _FILE_RIGHTS
            if rights:
                rule = Beneath(rights, fd)
                check(libc.syscall(L(_SYS_ADD), L(ruleset), L(1), ctypes.byref(rule), L(0)), "landlock_add_rule")
        finally:
            os.close(fd)
    check(libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs")
    _seccomp(libc, platform.machine())
    check(libc.syscall(L(_SYS_RESTRICT), L(ruleset), L(0)), "landlock_restrict_self")
    os.close(ruleset)


def _seccomp(libc, machine: str) -> None:
    import ctypes

    if machine not in _SECCOMP:
        raise OSError(f"no seccomp filter for {machine}")
    arch, refused, x32 = _SECCOMP[machine]

    class Insn(ctypes.Structure):
        _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]

    class Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Insn))]

    ld, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06
    allow, eperm = 0x7FFF0000, 0x00050000 | 1
    tests = ([(jge, x32)] if x32 else []) + [(jeq, nr) for nr in refused]
    # 0 ld arch; 1 jeq arch ? 3 : 2; 2 ret EPERM (foreign ABI); 3 ld nr; 4.. tests -> EPERM; ret ALLOW; ret EPERM
    insns = [(ld, 0, 0, 4), (jeq, 1, 0, arch), (ret, 0, 0, eperm), (ld, 0, 0, 0)]
    n = len(tests)
    for i, (code, k) in enumerate(tests):
        insns.append((code, n - i, 0, k))  # to the final EPERM, past the remaining tests and the ALLOW
    insns += [(ret, 0, 0, allow), (ret, 0, 0, eperm)]
    arr = (Insn * len(insns))(*[Insn(*x) for x in insns])
    prog = Prog(len(insns), arr)
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "seccomp filter failed")


def _children() -> list[int]:
    me = os.getpid()
    try:
        with open(f"/proc/{me}/task/{me}/children") as f:
            return [int(x) for x in f.read().split()]
    except OSError:
        return []


def _supervise(argv: list[str]) -> None:
    """Linux: stay as the parent of the whole run. As a child subreaper this process adopts every orphan (also one
    that called setsid), so the run is always one tree under it; when the candidate exits, the rest is killed."""
    import ctypes
    import signal

    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    pid = os.fork()
    if pid == 0:
        _exec(argv)
    while True:
        try:
            done, status = os.wait()
        except ChildProcessError:
            os._exit(1)
        if done == pid:
            break
    while kids := _children():  # each kill hands its own children to us, so repeat until none are left
        for k in kids:
            try:
                os.kill(k, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            break
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)
    os._exit(os.waitstatus_to_exitcode(status) & 0xFF)


def _exec(argv: list[str]) -> None:
    try:
        os.execvp(argv[0], argv)
    except OSError as e:
        sys.stderr.write(f"sandbox: cannot run {argv[0]!r} ({e.strerror})\n")
    os._exit(127)


def main() -> None:
    try:
        cfg = json.loads(sys.argv[1])
        argv = sys.argv[2:]
        cpu = int(cfg["cpu"])
        _limit(resource.RLIMIT_CPU, cpu, cpu + 1)  # SIGXCPU at the soft limit, SIGKILL one second later
        _limit(resource.RLIMIT_CORE, 0, 0)
        if cfg.get("landlock"):
            _landlock(cfg["landlock"])
    except Exception as e:  # noqa: BLE001 - never run the candidate unconfined
        sys.stderr.write(f"sandbox: could not confine the candidate ({type(e).__name__}: {e})\n")
        os._exit(126)
    if cfg.get("landlock"):
        _supervise(argv)
    _exec(argv)


if __name__ == "__main__":
    main()
