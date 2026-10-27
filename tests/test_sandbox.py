import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from qq_agent_harness.contracts import JSON_OBJECT, JsonObject
from qq_agent_harness.sandbox.client import RunnerClient
from qq_agent_harness.sandbox.egress import (
    DestinationDenied,
    PublicProxy,
    is_public_ip,
    resolve_public,
)
from qq_agent_harness.sandbox.quota import BtrfsQuota, NetworkGuard, QuotaStatus, SandboxUnavailable
from qq_agent_harness.sandbox.runner import ExecuteRequest, Runner, create_app
from qq_agent_harness.sandbox.workspace import Workspace
from qq_agent_harness.settings import SandboxSettings


class FakeQuota:
    def __init__(self, fail: bool = False):
        self.fail = fail

    async def check(self) -> QuotaStatus:
        if self.fail:
            raise SandboxUnavailable("quota missing")
        return QuotaStatus(50_000_000_000, 100)


class FakeGuard:
    async def check(self) -> None:
        return None


class FakeEngine:
    def __init__(self, *, block: bool = False, exit_code: int = 0):
        self.block, self.exit_code = block, exit_code
        self.created: list[JsonObject] = []
        self.removed: list[str] = []
        self.started = asyncio.Event()

    async def create(self, name: str, config: JsonObject) -> str:
        self.created.append(config)
        return name

    async def start(self, container_id: str) -> None:
        self.started.set()

    async def logs(self, container_id: str) -> AsyncIterator[bytes]:
        yield b"hello "
        yield b"world" * 1000
        if self.block:
            await asyncio.Event().wait()

    async def wait(self, container_id: str) -> int:
        if self.block:
            await asyncio.Event().wait()
        return self.exit_code

    async def remove(self, container_id: str) -> None:
        self.removed.append(container_id)

    async def prune_managed(self) -> None:
        return None

    async def close(self) -> None:
        return None


def make_runner(
    path: Path,
    *,
    quota: FakeQuota | None = None,
    engine: FakeEngine | None = None,
    timeout: float = 2,
) -> Runner:
    return Runner(
        workspace=Workspace(path),
        quota=quota or FakeQuota(),
        network_guard=FakeGuard(),
        engine=engine or FakeEngine(),
        host_workspace="/test/workspace",
        settings=SandboxSettings(enabled=True, command_timeout=timeout, output_limit=1024),
    )


async def test_quota_failure_never_creates_execution_container(tmp_path: Path) -> None:
    engine = FakeEngine()
    runner = make_runner(tmp_path, quota=FakeQuota(True), engine=engine)
    with pytest.raises(SandboxUnavailable):
        await runner.execute(ExecuteRequest(command="echo hello"))
    assert engine.created == []
    assert (await runner.status())["available"] is False


@pytest.mark.parametrize("quota_unavailable", [False, True])
async def test_status_exposes_applied_snapshot_without_runner_credentials(
    tmp_path: Path, quota_unavailable: bool
) -> None:
    settings = SandboxSettings(
        enabled=True,
        cpus=2,
        runner_token="private-token-value",
        runner_url="http://private-runner:6060",
    )
    runner = Runner(
        workspace=Workspace(tmp_path),
        quota=FakeQuota(quota_unavailable),
        network_guard=FakeGuard(),
        engine=FakeEngine(),
        settings=settings,
        host_workspace="/test/workspace",
    )
    # 配置草稿变化不能让运行中的 runner 谎报已使用新资源限制。
    settings.cpus = 8
    result = await runner.status()
    applied = result["applied_settings"]
    assert isinstance(applied, dict)
    assert applied["cpus"] == 2
    assert "runner_token" not in applied and "runner_url" not in applied
    assert "private-token-value" not in str(result)
    assert result["available"] is not quota_unavailable


async def test_output_is_bounded_and_container_always_reclaimed(tmp_path: Path) -> None:
    engine = FakeEngine()
    runner = make_runner(tmp_path, engine=engine)
    result = await runner.execute(ExecuteRequest(command="printf test"))
    assert result["status"] == "ok"
    assert len(str(result["output"]).encode()) == 1024
    assert result["truncated"] is True
    assert result["output_complete"] is True
    assert isinstance(result["output_path"], str)
    assert Workspace(tmp_path).read(result["output_path"]) == b"hello " + b"world" * 1000
    assert len(engine.removed) == 1
    assert runner.execution_id is None


@pytest.mark.parametrize("cancel_task", [False, True])
async def test_cancel_reclaims_processes_without_deleting_files(
    tmp_path: Path, cancel_task: bool
) -> None:
    (tmp_path / "keep.txt").write_text("shared", encoding="utf8")
    engine = FakeEngine(block=True)
    runner = make_runner(tmp_path, engine=engine)
    task = asyncio.create_task(runner.execute(ExecuteRequest(command="wait", owner="turn-a")))
    await engine.started.wait()
    assert (await runner.cancel("turn-b"))["cancelled"] is False
    if cancel_task:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await runner.cancel("turn-a"))["cancelled"] is True
        assert (await task)["status"] == "cancelled"
    assert len(engine.removed) == 1
    assert (tmp_path / "keep.txt").read_text(encoding="utf8") == "shared"


async def test_timeout_and_nonzero_exit_are_observable(tmp_path: Path) -> None:
    engine = FakeEngine(block=True)
    runner = make_runner(tmp_path, engine=engine, timeout=0.01)
    assert (await runner.execute(ExecuteRequest(command="wait")))["status"] == "timeout"
    assert len(engine.removed) == 1
    failure = make_runner(tmp_path, engine=FakeEngine(exit_code=3))
    result = await failure.execute(ExecuteRequest(command="exit 3"))
    assert (result["status"], result["exit_code"]) == ("error", 3)


async def test_http_files_auth_cleanup_and_busy(tmp_path: Path) -> None:
    engine = FakeEngine(block=True)
    runner = make_runner(tmp_path, engine=engine)
    settings = SandboxSettings(enabled=True, runner_token="a" * 32, runner_url="http://test")
    app = create_app(runner, settings.runner_token)
    transport = httpx.ASGITransport(app=app)
    connection = RunnerClient(lambda: settings, transport=transport)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as unauthenticated:
        assert (await unauthenticated.get("/status")).status_code == 401
    await connection.write_file("hello.txt", "你好")
    assert await connection.read_file("hello.txt") == "你好".encode()
    # 新 runner/client 会话仍读到同一个共享文件，不依赖运行状态恢复。
    assert Workspace(tmp_path).read("hello.txt") == "你好".encode()
    await connection.write_bytes("imports/picture.bin", b"\x00\xff\x01")
    assert await connection.read_file("imports/picture.bin") == b"\x00\xff\x01"
    with pytest.raises(httpx.HTTPStatusError):
        await connection.write_bytes("imports/picture.bin", b"overwrite")
    with pytest.raises(httpx.HTTPStatusError) as conflict:
        await connection.write_file("hello.txt", "overwrite")
    assert conflict.value.response.status_code == 400
    with pytest.raises(httpx.HTTPStatusError):
        await connection.cleanup("all", [], False)
    execution = asyncio.create_task(connection.execute("wait", owner="turn-a"))
    await engine.started.wait()
    with pytest.raises(httpx.HTTPStatusError) as busy:
        await connection.cleanup("all", [], True)
    assert busy.value.response.status_code == 409
    await connection.cancel()
    await execution
    await connection.cleanup("selected", ["hello.txt"], True)
    assert not (tmp_path / "hello.txt").exists()
    await connection.close()


def test_paths_and_cleanup_are_validated_before_any_deletion(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / "keep").write_text("keep")
    with pytest.raises(ValueError):
        workspace.cleanup("selected", ["keep", "../outside"])
    assert (tmp_path / "keep").exists()
    for bad in ("/etc/passwd", "../x", "a/../../x", "C:/secret", "a\\..\\x"):
        with pytest.raises(ValueError):
            workspace.read(bad)


def test_symlink_read_write_and_cleanup_never_follow_outside(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("private")
    root = tmp_path / "workspace"
    root.mkdir()
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires platform privilege")
    workspace = Workspace(root)
    for operation in (
        lambda: workspace.read("escape/secret"),
        lambda: workspace.write("escape/secret", b"replace", overwrite=True),
        lambda: workspace.cleanup("selected", ["escape/secret"]),
    ):
        with pytest.raises(ValueError):
            operation()
    assert (outside / "secret").read_text() == "private"
    workspace.cleanup("all", [])
    assert not (root / "escape").is_symlink()
    assert (outside / "secret").read_text() == "private"


def test_special_files_do_not_block_runner(tmp_path: Path) -> None:
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("需要 POSIX FIFO")
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    workspace = Workspace(tmp_path)
    with pytest.raises(ValueError):
        workspace.read("pipe")
    with pytest.raises(ValueError):
        workspace.write("pipe", b"blocked", overwrite=True)
    workspace.cleanup("all", [])
    assert not fifo.exists()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "::ffff:127.0.0.1",
        "2001:db8::1",
    ],
)
def test_nonpublic_destinations_are_rejected(address: str) -> None:
    assert not is_public_ip(address)


async def test_proxy_rechecks_dns_and_pins_connected_address() -> None:
    connected: list[tuple[str, int]] = []

    async def resolve(host: str, port: int) -> list[str]:
        return ["127.0.0.1" if host == "evil.example" else "8.8.8.8"]

    async def denied_connector(
        host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        connected.append((host, port))
        raise OSError("test endpoint")

    proxy = PublicProxy(resolve, denied_connector)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        for host in ("evil.example", "public.example"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
            await writer.drain()
            assert (await reader.read()).startswith(
                b"HTTP/1.1 403" if host == "evil.example" else b"HTTP/1.1 502"
            )
            writer.close()
            await writer.wait_closed()
    assert connected == [("8.8.8.8", 443)]


async def test_dns_numeric_alias_cannot_escape_public_only_policy() -> None:
    for host in ("127.0.0.1", "2130706433", "localhost", "::ffff:127.0.0.1"):
        with pytest.raises((DestinationDenied, ConnectionError)):
            await resolve_public(host, 443)


@pytest.mark.parametrize("redirect", [False, True])
async def test_http_proxy_forwards_public_response_but_blocks_private_redirect(
    redirect: bool,
) -> None:
    requests: list[bytes] = []

    async def endpoint(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        if redirect:
            writer.write(
                b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/private\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
        else:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(endpoint, "127.0.0.1", 0)
    origin_port = origin.sockets[0].getsockname()[1]

    async def resolve(host: str, port: int) -> list[str]:
        return ["8.8.8.8"] if host == "public.example" else ["127.0.0.1"]

    async def connector(ip: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        assert (ip, port) == ("8.8.8.8", 80)
        return await asyncio.open_connection("127.0.0.1", origin_port)

    proxy = PublicProxy(resolve, connector)
    listener = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    proxy_port = listener.sockets[0].getsockname()[1]
    async with (
        origin,
        listener,
        httpx.AsyncClient(proxy=f"http://127.0.0.1:{proxy_port}", follow_redirects=True) as client,
    ):
        response = await client.get("http://public.example/source")
        assert response.status_code == (403 if redirect else 200)
        if not redirect:
            assert response.text == "hello"
    assert len(requests) == 1
    assert requests[0].startswith(b"GET /source HTTP/1.1")


async def test_btrfs_inconsistent_or_wrong_quota_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warning = ""
    limit = 10_000_000

    async def fake_command(*args: str) -> tuple[str, str]:
        if args[1] == "inspect-internal":
            return "123\n", ""
        return f"qgroupid rfer excl max_rfer max_excl\n0/123 4096 4096 {limit} none\n", warning

    monkeypatch.setattr("qq_agent_harness.sandbox.quota.command", fake_command)
    real_stat = Path.stat

    def quota_stat(path: Path, *, follow_symlinks: bool = True):
        result = real_stat(path, follow_symlinks=follow_symlinks)
        if path == tmp_path:
            import os

            values = list(result)
            values[1] = 256
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", quota_stat)
    quota = BtrfsQuota(tmp_path, 10_000_000)
    assert (await quota.check()).used_bytes == 4096
    warning = "WARNING: qgroup data inconsistent"
    with pytest.raises(SandboxUnavailable):
        await quota.check()
    warning, limit = "", 20_000_000
    with pytest.raises(SandboxUnavailable):
        await quota.check()


@pytest.mark.parametrize(
    "bypass", ["input", "forward", "docker_user", "dedicated", "ipv6", "network", "bridge_filter"]
)
async def test_network_guard_rejects_rule_order_and_network_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bypass: str
) -> None:
    flag = tmp_path / "bridge-filter"
    flag.write_text("1", encoding="ascii")
    rules: dict[tuple[str, str], str] = {}
    for chain in ("INPUT", "FORWARD", "DOCKER-USER"):
        target = "REJECT" if chain == "INPUT" else "QAH-SANDBOX"
        rules[("iptables", chain)] = f"-A {chain} -i br-qah-sbx -j {target}"
    for chain in ("INPUT", "FORWARD"):
        rules[("ip6tables", chain)] = (
            f"-A {chain} -i br-qah-sbx -j REJECT --reject-with icmp6-port-unreachable"
        )
    rules[("iptables", "QAH-SANDBOX")] = "\n".join(
        [
            "-A QAH-SANDBOX -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
            "-A QAH-SANDBOX -d 172.30.240.2/32 -p tcp -m tcp --dport 3128 -j ACCEPT",
            "-A QAH-SANDBOX -j REJECT --reject-with icmp-port-unreachable",
        ]
    )
    network = JSON_OBJECT.validate_python(
        {
            "Driver": "bridge",
            "Internal": True,
            "EnableIPv6": False,
            "Options": {"com.docker.network.bridge.name": "br-qah-sbx"},
            "Containers": {"proxy": {"IPv4Address": "172.30.240.2/24", "Name": "egress"}},
        }
    )

    async def inspect() -> JsonObject:
        return network

    async def fake_command(*args: str) -> tuple[str, str]:
        assert args[1] == "-S"
        return rules[(args[0], args[2])], ""

    monkeypatch.setattr("qq_agent_harness.sandbox.quota.command", fake_command)
    guard = NetworkGuard("br-qah-sbx", "172.30.240.2", 3128, inspect, bridge_filter_path=flag)
    await guard.check()
    if bypass == "network":
        network["EnableIPv6"] = True
    elif bypass == "bridge_filter":
        flag.write_text("0", encoding="ascii")
    else:
        binary = "ip6tables" if bypass == "ipv6" else "iptables"
        chain = {
            "input": "INPUT",
            "forward": "FORWARD",
            "docker_user": "DOCKER-USER",
            "dedicated": "QAH-SANDBOX",
            "ipv6": "FORWARD",
        }[bypass]
        # 所需规则仍然存在；仅 -C 会错误地放行这些布局。
        rules[(binary, chain)] = f"-A {chain} -j RETURN\n" + rules[(binary, chain)]
    with pytest.raises(SandboxUnavailable):
        await guard.check()
