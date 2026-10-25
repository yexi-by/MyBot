"""公网 HTTP/HTTPS 出口：解析后锁定公网地址，不转发本地 DNS 结果。"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit


class DestinationDenied(ValueError):
    pass


def is_public_ipv4(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return isinstance(address, ipaddress.IPv4Address) and address.is_global


async def resolve_public(host: str, port: int) -> str:
    if port not in (80, 443) or not host or any(c in host for c in ("%", "\\", "\x00")):
        raise DestinationDenied("只允许公网 HTTP/HTTPS")
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise DestinationDenied("目标域名无法解析为公网 IPv4 地址") from error
    ips = {str(address[4][0]) for address in addresses}
    if not ips or any(not is_public_ipv4(ip) for ip in ips):
        raise DestinationDenied("目标包含非公网地址")
    return sorted(ips)[0]


Resolver = Callable[[str, int], Awaitable[str]]
Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


async def connect(ip: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(ip, port, family=socket.AF_INET)


class PublicProxy:
    def __init__(self, resolver: Resolver = resolve_public, connector: Connector = connect):
        self.resolver, self.connector = resolver, connector
        self.slots = asyncio.Semaphore(32)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        try:
            async with self.slots, asyncio.timeout(180):
                header = await reader.readuntil(b"\r\n\r\n")
                if len(header) > 65_536:
                    raise DestinationDenied("请求头过大")
                lines = header.decode("iso-8859-1").split("\r\n")
                method, target, version = lines[0].split(" ", 2)
                if version not in ("HTTP/1.0", "HTTP/1.1"):
                    raise DestinationDenied("仅支持 HTTP 请求")
                if method == "CONNECT":
                    destination = urlsplit("//" + target)
                    port = destination.port or 443
                    if port != 443 or destination.path or destination.username:
                        raise DestinationDenied("CONNECT 只允许 HTTPS 端口")
                else:
                    destination = urlsplit(target)
                    if destination.scheme != "http" or destination.username or destination.password:
                        raise DestinationDenied("HTTP 请求必须包含公网绝对地址")
                    port = destination.port or 80
                    if port != 80:
                        raise DestinationDenied("HTTP 只允许 80 端口")
                host = destination.hostname or ""
                ip = await self.resolver(host, port)
                # 不允许自定义 resolver 或后续改动意外绕过最终地址核验。
                if not is_public_ipv4(ip):
                    raise DestinationDenied("目标不是公网 IPv4 地址")
                remote, upstream = await self.connector(ip, port)
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                else:
                    path = destination.path or "/"
                    if destination.query:
                        path += "?" + destination.query
                    forwarded = [f"{method} {path} {version}", f"Host: {host}", "Connection: close"]
                    for line in lines[1:]:
                        if not line:
                            continue
                        key, separator, _ = line.partition(":")
                        if not separator or key.strip() != key:
                            raise DestinationDenied("请求头格式错误")
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

                async def relay(
                    source: asyncio.StreamReader, target_writer: asyncio.StreamWriter
                ) -> None:
                    while chunk := await source.read(65_536):
                        target_writer.write(chunk)
                        await target_writer.drain()

                upload = asyncio.create_task(relay(reader, upstream))
                download = asyncio.create_task(relay(remote, writer))
                try:
                    await asyncio.wait({upload, download}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    upload.cancel()
                    download.cancel()
                    await asyncio.gather(upload, download, return_exceptions=True)
        except (
            ValueError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            if upstream is None:
                writer.write(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            if upstream is not None:
                upstream.close()
                await upstream.wait_closed()
            writer.close()
            await writer.wait_closed()


async def serve() -> None:
    proxy = PublicProxy()
    server = await asyncio.start_server(proxy.handle, "0.0.0.0", 3128, limit=65_536)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
