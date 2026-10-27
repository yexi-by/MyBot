"""公网 HTTP/HTTPS 出口：解析后锁定公网地址，不转发本地 DNS 结果。"""

import asyncio
import ipaddress
import os
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit


class DestinationDenied(ValueError):
    pass


def is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global and not (
        isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped is not None
        and not address.ipv4_mapped.is_global
    )


async def resolve_public(host: str, port: int) -> list[str]:
    if not 1 <= port <= 65535 or not host or any(c in host for c in ("%", "\\", "\x00")):
        raise DestinationDenied("只允许公网 HTTP/HTTPS")
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise ConnectionError("目标域名无法解析") from error
    ips = list(dict.fromkeys(str(address[4][0]) for address in addresses))
    if not ips or any(not is_public_ip(ip) for ip in ips):
        raise DestinationDenied("目标包含非公网地址")
    return ips


Resolver = Callable[[str, int], Awaitable[list[str]]]
Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


async def connect(ip: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(ip, port)


class PublicProxy:
    def __init__(
        self,
        resolver: Resolver = resolve_public,
        connector: Connector = connect,
        *,
        allowed_ports: set[int] | None = None,
        idle_timeout: float = 180,
        connections: int = 32,
    ):
        self.resolver, self.connector = resolver, connector
        self.allowed_ports = {80, 443} if allowed_ports is None else allowed_ports
        self.idle_timeout = idle_timeout
        self.slots = asyncio.Semaphore(connections)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        response_started = False
        try:
            async with self.slots:
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.idle_timeout)
                if len(header) > 65_536:
                    raise DestinationDenied("请求头过大")
                lines = header.decode("iso-8859-1").split("\r\n")
                method, target, version = lines[0].split(" ", 2)
                if version not in ("HTTP/1.0", "HTTP/1.1"):
                    raise ValueError("仅支持 HTTP 请求")
                destination = urlsplit("//" + target if method == "CONNECT" else target)
                if destination.username or destination.password or destination.fragment:
                    raise DestinationDenied("不接受带凭据或片段的代理目标")
                if method == "CONNECT":
                    if destination.path or destination.query:
                        raise ValueError("CONNECT 目标格式无效")
                    port = destination.port or 443
                else:
                    if destination.scheme != "http":
                        raise ValueError("HTTP 请求必须包含绝对地址")
                    port = destination.port or 80
                if port not in self.allowed_ports:
                    raise DestinationDenied("目标端口未开放")
                host = destination.hostname or ""
                ips = await self.resolver(host, port)
                if not ips or any(not is_public_ip(ip) for ip in ips):
                    raise DestinationDenied("目标包含非公网地址")
                remote: asyncio.StreamReader | None = None
                for ip in ips:
                    try:
                        remote, upstream = await asyncio.wait_for(
                            self.connector(ip, port), min(30, self.idle_timeout)
                        )
                        break
                    except OSError, TimeoutError:
                        continue
                if remote is None or upstream is None:
                    raise ConnectionError("所有公网地址均无法连接")
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                    response_started = True
                else:
                    path = destination.path or "/"
                    if destination.query:
                        path += "?" + destination.query
                    forwarded = [
                        f"{method} {path} {version}",
                        f"Host: {destination.netloc}",
                        "Connection: close",
                    ]
                    for line in lines[1:]:
                        if not line:
                            continue
                        key, separator, _ = line.partition(":")
                        if not separator or key.strip() != key:
                            raise ValueError("请求头格式错误")
                        if key.lower() not in {
                            "host",
                            "connection",
                            "proxy-connection",
                            "proxy-authorization",
                            "upgrade",
                        }:
                            forwarded.append(line)
                    upstream.write(("\r\n".join(forwarded) + "\r\n\r\n").encode("iso-8859-1"))
                    await upstream.drain()

                activity = asyncio.Event()

                async def relay(
                    source: asyncio.StreamReader,
                    destination_writer: asyncio.StreamWriter,
                    *,
                    download: bool = False,
                ) -> None:
                    nonlocal response_started
                    while chunk := await source.read(65_536):
                        if download:
                            response_started = True
                        activity.set()
                        destination_writer.write(chunk)
                        await destination_writer.drain()
                    if destination_writer.can_write_eof():
                        destination_writer.write_eof()

                async def idle() -> None:
                    while True:
                        await asyncio.wait_for(activity.wait(), self.idle_timeout)
                        activity.clear()

                upload = asyncio.create_task(relay(reader, upstream))
                download = asyncio.create_task(relay(remote, writer, download=True))
                watchdog = asyncio.create_task(idle())
                try:
                    done, _ = await asyncio.wait(
                        {upload, download, watchdog}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if upload in done:
                        upload.result()
                        done, _ = await asyncio.wait(
                            {download, watchdog}, return_when=asyncio.FIRST_COMPLETED
                        )
                    for task in done:
                        task.result()
                finally:
                    for task in (upload, download, watchdog):
                        task.cancel()
                    await asyncio.gather(upload, download, watchdog, return_exceptions=True)
        except (
            ValueError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as error:
            if not response_started:
                status = (
                    "403 Forbidden"
                    if isinstance(error, DestinationDenied)
                    else "504 Gateway Timeout"
                    if isinstance(error, TimeoutError)
                    else "502 Bad Gateway"
                    if isinstance(error, OSError)
                    else "400 Bad Request"
                )
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
                )
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            if upstream is not None:
                upstream.close()
                try:
                    await upstream.wait_closed()
                except OSError:
                    pass
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def serve() -> None:
    ports = {int(value) for value in os.environ.get("HARNESS_EGRESS_PORTS", "80,443").split(",")}
    if not ports or any(not 1 <= port <= 65535 for port in ports):
        raise ValueError("出口端口必须在 1–65535 范围内")
    timeout = float(os.environ.get("HARNESS_EGRESS_IDLE_TIMEOUT", "180"))
    connections = int(os.environ.get("HARNESS_EGRESS_CONNECTIONS", "32"))
    if timeout <= 0 or connections < 1:
        raise ValueError("出口空闲超时和连接数必须为正数")
    proxy = PublicProxy(allowed_ports=ports, idle_timeout=timeout, connections=connections)
    server = await asyncio.start_server(proxy.handle, "0.0.0.0", 3128, limit=65_536)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
