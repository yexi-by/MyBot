"""查询真实 Btrfs 配额；无配额或核验失败时不允许运行。"""

import asyncio
import ipaddress
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..contracts import JsonObject


class SandboxUnavailable(RuntimeError):
    pass


async def command(*args: str) -> tuple[str, str]:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        async with asyncio.timeout(15):
            stdout, stderr = await process.communicate()
    except BaseException:
        process.kill()
        await process.wait()
        raise
    out, err = stdout.decode(errors="replace"), stderr.decode(errors="replace")
    if process.returncode:
        raise SandboxUnavailable(f"环境核验失败：{args[0]}（退出码 {process.returncode}）")
    return out, err


@dataclass(frozen=True)
class QuotaStatus:
    quota_bytes: int
    used_bytes: int


class BtrfsQuota:
    def __init__(self, workspace: Path, quota_bytes: int):
        self.workspace = workspace
        self.quota_bytes = quota_bytes

    async def check(self) -> QuotaStatus:
        if self.workspace.is_symlink() or self.workspace.stat().st_ino != 256:
            raise SandboxUnavailable("工作区必须直接挂载专用 Btrfs 子卷")
        rootid, _ = await command("btrfs", "inspect-internal", "rootid", str(self.workspace))
        output, warning = await command(
            "btrfs", "qgroup", "show", "--raw", "-reF", str(self.workspace)
        )
        if any(word in warning.lower() for word in ("inconsistent", "rescan", "not enabled")):
            raise SandboxUnavailable("Btrfs 配额状态不一致，拒绝启用代码执行")
        expected = f"0/{rootid.strip()}"
        for line in output.splitlines():
            fields = line.split()
            if fields and fields[0] == expected and len(fields) >= 5:
                try:
                    used, limit = int(fields[1]), int(fields[3])
                except ValueError as error:
                    raise SandboxUnavailable("工作区未设置有效硬配额") from error
                if limit != self.quota_bytes:
                    raise SandboxUnavailable("工作区实际硬配额与配置不一致")
                return QuotaStatus(quota_bytes=limit, used_bytes=used)
        raise SandboxUnavailable("未找到工作区的 Btrfs 配额")


class NetworkGuard:
    def __init__(
        self,
        bridge: str,
        proxy_ip: str,
        proxy_port: int,
        inspect_network: Callable[[], Awaitable[JsonObject]],
        *,
        bridge_filter_path: Path = Path("/proc/sys/net/bridge/bridge-nf-call-iptables"),
    ):
        self.bridge, self.proxy_ip, self.proxy_port = bridge, proxy_ip, proxy_port
        self.inspect_network = inspect_network
        self.bridge_filter_path = bridge_filter_path

    @staticmethod
    def normalize(rule: str) -> list[str]:
        tokens = shlex.split(rule)
        result: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if tokens[index : index + 2] == ["-m", "tcp"]:
                index += 2
                continue
            if token in ("--ctstate", "-d") and index + 1 < len(tokens):
                value = tokens[index + 1]
                value = (
                    ",".join(sorted(value.split(",")))
                    if token == "--ctstate"
                    else str(ipaddress.ip_network(value, strict=False))
                )
                result.extend([token, value])
                index += 2
                continue
            if tokens[index : index + 2] in (
                ["--reject-with", "icmp-port-unreachable"],
                ["--reject-with", "icmp6-port-unreachable"],
            ):
                index += 2
                continue
            result.append(token)
            index += 1
        return result

    async def rules(self, binary: str, chain: str) -> list[list[str]]:
        listing, _ = await command(binary, "-S", chain)
        return [
            self.normalize(line) for line in listing.splitlines() if line.startswith(f"-A {chain} ")
        ]

    async def first(self, binary: str, chain: str, expected: str) -> None:
        rules = await self.rules(binary, chain)
        if not rules or rules[0] != self.normalize(expected):
            raise SandboxUnavailable(f"{binary} {chain} 的沙箱规则必须位于第一条")

    async def check(self) -> None:
        if self.bridge_filter_path.read_text(encoding="ascii").strip() != "1":
            raise SandboxUnavailable("bridge netfilter 未启用")
        network = await self.inspect_network()
        options, containers = network.get("Options"), network.get("Containers")
        if (
            network.get("Driver") != "bridge"
            or network.get("Internal") is not True
            or network.get("EnableIPv6") is not False
            or not isinstance(options, dict)
            or options.get("com.docker.network.bridge.name") != self.bridge
        ):
            raise SandboxUnavailable("Docker 沙箱网络与隔离配置不一致")
        if not isinstance(containers, dict) or not any(
            isinstance(value, dict)
            and value.get("IPv4Address") == f"{self.proxy_ip}/24"
            and not str(value.get("Name", "")).startswith("qah-exec-")
            for value in containers.values()
        ):
            raise SandboxUnavailable("公网出口代理未连接到沙箱网络")
        for chain in ("INPUT", "FORWARD", "DOCKER-USER"):
            target = "REJECT" if chain == "INPUT" else "QAH-SANDBOX"
            await self.first("iptables", chain, f"-A {chain} -i {self.bridge} -j {target}")
        expected = [
            "-A QAH-SANDBOX -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
            f"-A QAH-SANDBOX -d {self.proxy_ip} -p tcp --dport {self.proxy_port} -j ACCEPT",
            "-A QAH-SANDBOX -j REJECT",
        ]
        if await self.rules("iptables", "QAH-SANDBOX") != [
            self.normalize(rule) for rule in expected
        ]:
            raise SandboxUnavailable("沙箱专用防火墙链包含意外规则或顺序错误")
        for chain in ("INPUT", "FORWARD"):
            await self.first("ip6tables", chain, f"-A {chain} -i {self.bridge} -j REJECT")
