import errno
import os
import subprocess
from uuid import uuid4

import pytest

from qq_agent_harness.sandbox import bootstrap


def evaluate(machine: str, arch: int, number: int, request: int) -> int:
    """用 syscall 输入验证 BPF 分支；不在 pytest 进程安装过滤器。"""
    program = bootstrap.filter_instructions(machine)
    accumulator, pc = 0, 0
    fields = {0: number & 0xFFFFFFFF, 4: arch, 24: request & 0xFFFFFFFF}
    while pc < len(program):
        code, yes, no, value = program[pc]
        if code == 0x20:
            accumulator = fields[value]
        elif code == 0x54:
            accumulator &= value
        elif code == 0x15:
            pc += yes if accumulator == value else no
        elif code == 0x45:
            pc += yes if accumulator & value else no
        elif code == 0x06:
            return value
        else:
            raise AssertionError(f"unexpected BPF opcode {code}")
        pc += 1
    raise AssertionError("BPF did not terminate")


@pytest.mark.parametrize(
    "machine,arch,ioctl",
    [
        ("x86_64", 0xC000003E, 16),
        ("aarch64", 0xC00000B7, 29),
    ],
)
def test_filter_rejects_subvolume_ioctl_variants_and_foreign_abis(
    machine: str, arch: int, ioctl: int
) -> None:
    for command in (0x9401, 0x940E, 0x9417, 0x9418, 0x9428, 0x9429, 0x942A, 0x942B):
        for prefix in (0, 0x50000000, 0xC0300000, 0x1234567800000000):
            assert evaluate(machine, arch, ioctl, prefix | command) == 0x50000 | errno.EPERM
    assert evaluate(machine, arch, ioctl, 0x40049409) == 0x7FFF0000  # FICLONE 仍允许
    assert evaluate(machine, 0x40000003, 54, 0x5000940E) == 0x80000000  # i386 compat
    assert evaluate(machine, 0x40000028, 54, 0x5000940E) == 0x80000000  # ARM compat
    if machine == "x86_64":
        assert evaluate(machine, arch, 0x40000202, 0x5000940E) == 0x80000000  # x32 ioctl


@pytest.mark.parametrize("failed_option", [21, 38, 22])
def test_guard_install_failure_does_not_execute_command(
    monkeypatch: pytest.MonkeyPatch, failed_option: int
) -> None:
    executed: list[str] = []

    class FakePrctl:
        restype: object = None

        def __call__(self, option: int, *_arguments: object) -> int:
            if option == 21:
                return 0 if failed_option == 21 else 2
            return -1 if option == failed_option else 0

    class FakeLib:
        prctl = FakePrctl()

    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(bootstrap.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLib())
    monkeypatch.setattr(bootstrap.ctypes, "get_errno", lambda: errno.EACCES)
    monkeypatch.setattr(bootstrap.sys, "argv", ["guard.py", "/bin/true"])
    monkeypatch.setattr(bootstrap.os, "execvp", lambda path, _args: executed.append(path))
    with pytest.raises(SystemExit) as failure:
        bootstrap.main()
    assert failure.value.code == 126
    assert executed == []
    with pytest.raises(RuntimeError):
        bootstrap.filter_instructions("i686")


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True, encoding="utf8", timeout=120
    )


@pytest.mark.skipif(os.environ.get("QAH_DOCKER_TEST") != "1", reason="需要显式启用 Docker 集成测试")
def test_real_ioctl_guard_returns_eperm_and_is_inherited() -> None:
    image = os.environ.get("QAH_SANDBOX_IMAGE", "qq-agent-harness-sandbox:1.0.0")
    base = [
        "run",
        "--rm",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--network",
        "none",
    ]
    probe = r"""
import ctypes, errno, json, os, platform
libc = ctypes.CDLL(None, use_errno=True)
fd = os.open('/dev/null', os.O_RDONLY)
number = 16 if platform.machine() == 'x86_64' else 29
results=[]
for low in (0x9401,0x940e,0x9417,0x9418,0x9428,0x9429,0x942a,0x942b):
 for prefix in (0,0x50000000,0xc0300000,0x1234567800000000):
  ctypes.set_errno(0)
  assert libc.syscall(ctypes.c_long(number), ctypes.c_int(fd), ctypes.c_ulong(prefix|low), ctypes.c_void_p()) == -1
  results.append(ctypes.get_errno())
ctypes.set_errno(0)
assert libc.syscall(ctypes.c_long(number),ctypes.c_int(fd),ctypes.c_ulong(0x40049409),ctypes.c_void_p()) == -1
clone_errno=ctypes.get_errno()
os.close(fd)
filters=int(next(line.split(':')[1] for line in open('/proc/self/status') if line.startswith('Seccomp_filters:')))
print(json.dumps({'errors':results,'clone_errno':clone_errno,'filters':filters}))
"""
    # 在非 Btrfs 的 /dev/null 上，未加过滤器是 ENOTTY；加过滤器必须先得到 EPERM。
    unguarded = docker(
        *base, "--entrypoint", "/usr/local/bin/python", image, "-I", "-S", "-c", probe
    )
    guarded = docker(*base, image, "/usr/local/bin/python", "-I", "-S", "-c", probe)
    import json

    baseline = json.loads(unguarded.stdout)
    protected = json.loads(guarded.stdout)
    assert set(baseline["errors"]) == {errno.ENOTTY}
    assert set(protected["errors"]) == {errno.EPERM}
    assert protected["clone_errno"] == baseline["clone_errno"] != errno.EPERM
    assert protected["filters"] == baseline["filters"] + 1
    abi_probe = r"""
import ctypes, mmap, platform, signal, subprocess, sys
status=open('/proc/self/status').read()
assert int(next(line.split(':')[1] for line in status.splitlines() if line.startswith('Seccomp_filters:'))) >= 2
libc=ctypes.CDLL(None,use_errno=True)
assert libc.prctl(22,0,0,0,0) == -1, 'filters must not be removable'
if platform.machine() == 'x86_64':
 x32='import ctypes; ctypes.CDLL(None).syscall(ctypes.c_long(0x40000202),-1,0x5000940e,0)'
 compat="import ctypes,mmap; m=mmap.mmap(-1,4096,prot=7); m.write(bytes.fromhex('b814000000cd80c3')); ctypes.CFUNCTYPE(ctypes.c_int)(ctypes.addressof(ctypes.c_char.from_buffer(m)))()"
 for script in (x32,compat):
  result=subprocess.run([sys.executable,'-I','-S','-c',script],capture_output=True)
  assert result.returncode == -signal.SIGSYS, (result.returncode,result.stderr)
print('guard inherited; default Docker filter retained; unsupported ABI rejected')
"""
    assert (
        "unsupported ABI rejected"
        in docker(*base, image, "/usr/local/bin/python", "-I", "-S", "-c", abi_probe).stdout
    )


@pytest.mark.skipif(os.environ.get("QAH_DOCKER_TEST") != "1", reason="需要显式启用 Docker 集成测试")
def test_persistent_python_startup_hooks_cannot_run_before_guard() -> None:
    image = os.environ.get("QAH_SANDBOX_IMAGE", "qq-agent-harness-sandbox:1.0.0")
    volume = "qah-guard-test-" + uuid4().hex
    docker("volume", "create", volume)
    try:
        setup = r"""
from pathlib import Path
import os
root=Path('/workspace')
(root/'poison').mkdir()
(root/'poison/sitecustomize.py').write_text("from pathlib import Path; Path('/workspace/site-hook-ran').touch()")
site=root/'userbase/lib/python3.14/site-packages'
site.mkdir(parents=True)
(site/'poison.pth').write_text("import pathlib; pathlib.Path('/workspace/pth-hook-ran').touch()\n")
for directory,dirs,files in os.walk(root):
 os.chown(directory,1000,1000)
 for name in files: os.chown(os.path.join(directory,name),1000,1000)
"""
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "-v",
            f"{volume}:/workspace",
            image,
            "/usr/local/bin/python",
            "-I",
            "-S",
            "-c",
            setup,
        )
        base = [
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-v",
            f"{volume}:/workspace",
            "-e",
            "HOME=/workspace",
            "-e",
            "PYTHONPATH=/workspace/poison",
            "-e",
            "PYTHONUSERBASE=/workspace/userbase",
        ]
        docker(*base, "--entrypoint", "/usr/local/bin/python", image, "-c", "pass")
        docker(
            *base,
            image,
            "/bin/sh",
            "-c",
            "test -f /workspace/site-hook-ran && test -f /workspace/pth-hook-ran && rm /workspace/site-hook-ran /workspace/pth-hook-ran",
        )
        docker(*base, image, "/bin/true")
        docker(
            *base,
            image,
            "/bin/sh",
            "-c",
            "test ! -e /workspace/site-hook-ran && test ! -e /workspace/pth-hook-ran",
        )
    finally:
        docker("volume", "rm", volume)
