"""只读镜像入口：在任何工作区代码执行前叠加不可撤销的 seccomp 约束。

此文件必须用绝对 Python 路径和 -I -S 启动，只依赖标准库。
保留 Docker 已安装的 seccomp；这里的 ALLOW 仍受已有过滤器约束。
"""

import ctypes
import errno
import os
import platform
import sys

# Linux v6.12 include/uapi/linux/btrfs.h 中的 type/nr，忽略 ioctl 的方向与尺寸位。
BLOCKED_BTRFS_COMMANDS = (0x9401, 0x940E, 0x9417, 0x9418, 0x9428, 0x9429, 0x942A, 0x942B)
AUDIT_ARCH_X86_64 = 0xC000003E
AUDIT_ARCH_AARCH64 = 0xC00000B7
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000


class SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]


def filter_instructions(machine: str) -> list[tuple[int, int, int, int]]:
    if machine == "x86_64":
        arch, ioctl_number = AUDIT_ARCH_X86_64, 16
    elif machine == "aarch64":
        arch, ioctl_number = AUDIT_ARCH_AARCH64, 29
    else:
        raise RuntimeError("sandbox guard only supports native x86_64/aarch64")
    # struct seccomp_data: nr@0, arch@4, instruction_pointer@8, args[0]@16。
    instructions = [
        (0x20, 0, 0, 4),  # LD arch
        (0x15, 1, 0, arch),  # 同一原生 ABI 才能继续
        (0x06, 0, 0, SECCOMP_RET_KILL_PROCESS),
        (0x20, 0, 0, 0),  # LD syscall nr
    ]
    if machine == "x86_64":
        instructions.extend(
            [
                (0x45, 0, 1, 0x40000000),  # x32 与 x86_64 共用 arch，但 nr 有 x32 位
                (0x06, 0, 0, SECCOMP_RET_KILL_PROCESS),
            ]
        )
    instructions.extend(
        [
            (0x15, 1, 0, ioctl_number),
            (0x06, 0, 0, SECCOMP_RET_ALLOW),
            (0x20, 0, 0, 24),  # 小端 args[1]（ioctl request）的低32位
            (0x54, 0, 0, 0xFFFF),  # 保留 type/nr
        ]
    )
    for command in BLOCKED_BTRFS_COMMANDS:
        instructions.extend(
            [
                (0x15, 0, 1, command),
                (0x06, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM),
            ]
        )
    instructions.append((0x06, 0, 0, SECCOMP_RET_ALLOW))
    return instructions


def install_filter() -> None:
    if sys.platform != "linux" or sys.byteorder != "little" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("sandbox guard requires native 64-bit little-endian Linux")
    instructions = filter_instructions(platform.machine())
    filters = (SockFilter * len(instructions))(*(SockFilter(*item) for item in instructions))
    program = SockFprog(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    if prctl(21, ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)) != 2:
        raise RuntimeError("sandbox guard requires an existing container seccomp filter")
    if prctl(38, ctypes.c_ulong(1), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        raise OSError(ctypes.get_errno(), "cannot set no_new_privs")
    if (
        prctl(22, ctypes.c_ulong(2), ctypes.byref(program), ctypes.c_ulong(0), ctypes.c_ulong(0))
        != 0
    ):
        raise OSError(ctypes.get_errno(), "cannot install sandbox seccomp guard")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("sandbox guard requires a command")
    try:
        install_filter()
    except (OSError, RuntimeError) as error:
        print(f"sandbox guard initialization failed: {error}", file=sys.stderr)
        raise SystemExit(126) from error
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
