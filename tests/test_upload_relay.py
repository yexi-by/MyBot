"""在隔离的 Docker 网络中注入 TCP 故障，不修改宿主网络或联系 QQ。"""

import json
import os
import subprocess
import time
from uuid import uuid4

import pytest

SERVER = r"""
import asyncio, json, os, signal, socket, subprocess
from pathlib import Path

address = socket.gethostbyname(socket.gethostname())
os.environ['QQ_UPLOAD_LISTEN_IP'] = address
os.environ['QQ_UPLOAD_DESTINATIONS'] = address
os.environ['QQ_UPLOAD_BYPASS_MARK'] = '128'

async def echo(reader, writer):
    first = await reader.readexactly(4)
    print(json.dumps({'backend': True, 'failure': first == b'FAIL'}), flush=True)
    if first == b'FAIL':
        writer.write(b'ack')
        await writer.drain()
        writer.transport.abort()
        return
    content = first + await reader.read()
    writer.write(content)
    await writer.drain()
    writer.close()
    await writer.wait_closed()

async def main():
    subprocess.run(['iptables', '-t', 'nat', '-N', 'TEST_KEEP'], check=True)
    before = subprocess.check_output(['iptables', '-t', 'nat', '-S'])
    Path('/tmp/rules-before').write_bytes(before)
    subprocess.run(['iptables', '-t', 'nat', '-N', 'QAH-QQ-UPLOAD'], check=True)
    subprocess.run(
        ['iptables', '-t', 'nat', '-I', 'PREROUTING', '-p', 'tcp', '-j', 'QAH-QQ-UPLOAD'],
        check=True,
    )
    servers = [await asyncio.start_server(echo, '0.0.0.0', port) for port in (8080, 8081)]
    relay = await asyncio.create_subprocess_exec(
        'python', '-m', 'qq_agent_harness.napcat.upload_relay'
    )
    Path('/tmp/relay.pid').write_text(str(relay.pid))
    await relay.wait()
    for server in servers:
        server.close()
        await server.wait_closed()
    after = subprocess.check_output(['iptables', '-t', 'nat', '-S'])
    assert after == before, 'relay left rules behind or changed another chain'
    print('RULES_RESTORED', flush=True)

asyncio.run(main())
"""

CLIENT = r"""
import json, socket, sys, time
address, port, mode = sys.argv[1], int(sys.argv[2]), sys.argv[3]
content = b'FAIL' if mode == 'reset' else bytes(range(256)) * 8969
started = time.monotonic()
with socket.create_connection((address, port), timeout=5) as client:
    client.settimeout(5)
    client.sendall(content if mode != 'blocked' else b'test')
    client.shutdown(socket.SHUT_WR)
    response = bytearray()
    try:
        while data := client.recv(65536):
            response.extend(data)
    except ConnectionResetError:
        if mode not in ('blocked', 'reset'):
            raise
if mode == 'echo':
    assert response == content, (len(response), len(content))
elif mode == 'reset':
    assert response == b'ack', response
else:
    assert not response, len(response)
print(json.dumps({'elapsed': time.monotonic() - started, 'bytes': len(response)}))
"""


@pytest.mark.skipif(
    os.environ.get("QAH_UPLOAD_RELAY_TEST") != "1", reason="需要显式启用隔离 Docker 网络验收"
)
def test_upload_relay_real_tcp_faults_and_cleanup() -> None:
    image = os.environ["QAH_APP_IMAGE"]
    suffix = uuid4().hex[:10]
    network = "qah-upload-test-" + suffix
    client, server, other = (network + name for name in ("-client", "-server", "-other"))
    containers: list[str] = []

    def docker(*args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, encoding="utf8", timeout=30
        )
        if check:
            assert result.returncode == 0, result.stderr
        return result.stdout

    def address(name: str) -> str:
        data = json.loads(docker("inspect", name))[0]
        return data["NetworkSettings"]["Networks"][network]["IPAddress"]

    def logs() -> list[dict[str, object]]:
        return [
            json.loads(line) for line in docker("logs", server).splitlines() if line.startswith("{")
        ]

    def request(mode: str = "echo", *, caller: str = client, port: int = 8080) -> dict[str, object]:
        return json.loads(
            docker("exec", caller, "python", "-c", CLIENT, address(server), str(port), mode)
        )

    docker("network", "create", "--internal", network)
    try:
        for name in (client, other):
            containers.append(name)
            docker(
                "run",
                "-d",
                "--name",
                name,
                "--network",
                network,
                "--cap-drop",
                "ALL",
                "--memory",
                "64m",
                "--pids-limit",
                "32",
                image,
                "python",
                "-c",
                "import time; time.sleep(180)",
            )
        containers.append(server)
        docker(
            "run",
            "-d",
            "--name",
            server,
            "--network",
            network,
            "--user",
            "0:0",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "NET_ADMIN",
            "--memory",
            "128m",
            "--pids-limit",
            "64",
            "-e",
            "QQ_UPLOAD_SOURCE_IP=" + address(client),
            image,
            "python",
            "-u",
            "-c",
            SERVER,
        )
        for _ in range(100):
            if any(row.get("event") == "qq_upload_ready" for row in logs()):
                break
            time.sleep(0.1)
        else:
            pytest.fail("转发进程未就绪：" + docker("logs", server))

        # 在转发器的隔离网络空间丢掉首个 SYN，第二次新连接才放行。
        fault = (
            "OUTPUT",
            "-d",
            address(server),
            "-p",
            "tcp",
            "--dport",
            "8080",
            "--syn",
            "-m",
            "statistic",
            "--mode",
            "nth",
            "--every",
            "2",
            "--packet",
            "0",
            "-j",
            "DROP",
        )
        docker("exec", server, "iptables", "-A", *fault)
        try:
            result = request()
        finally:
            docker("exec", server, "iptables", "-D", *fault)
        assert result["bytes"] == 2_296_064
        connected = [row for row in logs() if row.get("event") == "qq_upload_connected"]
        assert len(connected) == 1 and connected[0]["attempts"] == 2
        connect_ms = connected[0]["connect_ms"]
        assert isinstance(connect_ms, int) and 400 <= connect_ms < 2000

        request("reset")
        time.sleep(0.1)
        assert sum(row.get("failure") is True for row in logs()) == 1
        assert sum(row.get("event") == "qq_upload_connected" for row in logs()) == 2

        # 未匹配端口、未匹配来源均保持直达后端，不进入转发器。
        request(port=8081)
        request(caller=other)
        request("blocked", caller=other, port=52347)
        assert sum(row.get("event") == "qq_upload_connected" for row in logs()) == 2

        fault = (
            "OUTPUT",
            "-d",
            address(server),
            "-p",
            "tcp",
            "--dport",
            "8080",
            "--syn",
            "-j",
            "DROP",
        )
        backends_before = sum(row.get("backend") is True for row in logs())
        docker("exec", server, "iptables", "-A", *fault)
        try:
            result = request("blocked")
        finally:
            docker("exec", server, "iptables", "-D", *fault)
        elapsed = result["elapsed"]
        assert isinstance(elapsed, float) and 1.8 <= elapsed < 4
        assert sum(row.get("backend") is True for row in logs()) == backends_before

        docker(
            "exec",
            server,
            "python",
            "-c",
            "import os,signal; from pathlib import Path; "
            "os.kill(int(Path('/tmp/relay.pid').read_text()), signal.SIGTERM)",
        )
        assert docker("wait", server).strip() == "0"
        assert "RULES_RESTORED" in docker("logs", server)
    finally:
        for name in reversed(containers):
            docker("rm", "-f", name, check=False)
        docker("network", "rm", network, check=False)
