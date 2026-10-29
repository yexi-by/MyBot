"""可选的 QQ 上传 TCP 转发；重试仅发生在向上游发送任何数据之前。"""

import asyncio
import contextlib
import ipaddress
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass

CHAIN = "QAH-QQ-UPLOAD"
LISTEN_PORT = 52347
UPLOAD_PORTS = {80, 8080}
CONNECT_TIMEOUT = 0.5
CONNECT_ATTEMPTS = 4
IDLE_TIMEOUT = 180
CONNECTION_LIMIT = 32
SO_ORIGINAL_DST = 80
SO_MARK = 36


@dataclass(frozen=True)
class Settings:
    source_ip: str
    listen_ip: str
    destinations: frozenset[str]
    bypass_mark: int


def main() -> None:
    try:
        if sys.platform != "linux":
            raise ValueError("此服务仅支持 Linux")
        settings = read_settings()
        asyncio.run(serve(settings))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        emit("startup_failed", error_type=type(error).__name__)
        raise SystemExit(1) from None


async def serve(settings: Settings) -> None:
    relay = UploadRelay(settings)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    server = await asyncio.start_server(relay.accept, settings.listen_ip, LISTEN_PORT)
    async with server:
        try:
            await asyncio.to_thread(install_routes, settings)
            emit("ready", destinations=len(settings.destinations))
            await stopped.wait()
        finally:
            await asyncio.to_thread(clear_routes)
            await relay.close()
            emit("stopped")


class UploadRelay:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.tasks: set[asyncio.Task[None]] = set()

    def accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self.tasks) >= CONNECTION_LIMIT:
            writer.close()
            return
        task = asyncio.create_task(self.handle(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        started = time.monotonic()
        try:
            destination = self.destination(writer)
            remote, upstream, attempts = await connect(destination, self.settings.bypass_mark)
            emit(
                "connected",
                attempts=attempts,
                connect_ms=round(1000 * (time.monotonic() - started)),
            )
            tasks = [
                asyncio.create_task(pump(reader, upstream)),
                asyncio.create_task(pump(remote, writer)),
            ]
            try:
                uploaded, downloaded = await asyncio.gather(*tasks)
                emit("completed", uploaded=uploaded, downloaded=downloaded)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, TimeoutError, ValueError) as error:
            emit("connection_closed", error_type=type(error).__name__)
        finally:
            for stream in (writer, upstream):
                if stream is not None:
                    stream.close()
                    with contextlib.suppress(OSError):
                        await stream.wait_closed()

    def destination(self, writer: asyncio.StreamWriter) -> tuple[str, int]:
        peer = writer.get_extra_info("peername")
        raw = writer.get_extra_info("socket").getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
        address = socket.inet_ntoa(raw[4:8])
        port = struct.unpack("!H", raw[2:4])[0]
        if (
            peer[0] != self.settings.source_ip
            or address not in self.settings.destinations
            or port not in UPLOAD_PORTS
        ):
            raise ValueError("连接不在授权的上传范围内")
        return address, port

    async def close(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def connect(
    destination: tuple[str, int], bypass_mark: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int]:
    loop = asyncio.get_running_loop()
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        transferred = False
        try:
            candidate.setblocking(False)
            if bypass_mark:
                candidate.setsockopt(socket.SOL_SOCKET, SO_MARK, bypass_mark)
            candidate.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            await asyncio.wait_for(loop.sock_connect(candidate, destination), CONNECT_TIMEOUT)
            reader, writer = await asyncio.open_connection(sock=candidate)
            transferred = True
            return reader, writer, attempt
        except OSError, TimeoutError:
            if attempt == CONNECT_ATTEMPTS:
                raise
        finally:
            if not transferred:
                candidate.close()
    raise AssertionError("连接尝试次数必须为正数")


async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> int:
    total = 0
    while data := await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT):
        writer.write(data)
        await asyncio.wait_for(writer.drain(), IDLE_TIMEOUT)
        total += len(data)
    writer.write_eof()
    return total


def read_settings() -> Settings:
    def address(value: str) -> str:
        parsed = ipaddress.IPv4Address(value.strip())
        if parsed.is_unspecified or parsed.is_multicast:
            raise ValueError("需要明确的 IPv4 单播地址")
        return str(parsed)

    source = address(os.environ["QQ_UPLOAD_SOURCE_IP"])
    listen = address(os.environ["QQ_UPLOAD_LISTEN_IP"])
    destinations = frozenset(
        address(value) for value in os.environ["QQ_UPLOAD_DESTINATIONS"].split(",")
    )
    mark = int(os.environ["QQ_UPLOAD_BYPASS_MARK"], 0)
    if not 0 <= mark <= 0xFFFFFFFF:
        raise ValueError("连接标记超出范围")
    return Settings(source, listen, destinations, mark)


def iptables(*args: str, probe: bool = False) -> bool:
    result = subprocess.run(
        ["iptables", "-w", "5", "-t", "nat", *args], capture_output=True, check=False
    )
    if result.returncode == 0:
        return True
    if probe and result.returncode == 1:
        return False
    raise RuntimeError("无法更新 QQ 上传转发规则")


def clear_routes() -> None:
    if not iptables("-S", CHAIN, probe=True):
        return
    while iptables("-C", "PREROUTING", "-p", "tcp", "-j", CHAIN, probe=True):
        iptables("-D", "PREROUTING", "-p", "tcp", "-j", CHAIN)
    iptables("-F", CHAIN)
    iptables("-X", CHAIN)


def install_routes(settings: Settings) -> None:
    # SIGKILL 会留下宿主规则；在唯一的 Compose 服务重启时先收回本服务专用链。
    clear_routes()
    iptables("-N", CHAIN)
    for destination in sorted(settings.destinations):
        iptables(
            "-A",
            CHAIN,
            "-s",
            settings.source_ip + "/32",
            "-d",
            destination + "/32",
            "-p",
            "tcp",
            "-m",
            "multiport",
            "--dports",
            "80,8080",
            "-j",
            "REDIRECT",
            "--to-ports",
            str(LISTEN_PORT),
        )
    iptables("-I", "PREROUTING", "1", "-p", "tcp", "-j", CHAIN)


def emit(event: str, **metadata: object) -> None:
    print(json.dumps({"event": "qq_upload_" + event, **metadata}), flush=True)


if __name__ == "__main__":
    main()
