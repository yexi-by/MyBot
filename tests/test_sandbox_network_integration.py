"""本机 Docker 网络验收；只在 QAH_NETWORK_TEST=1 时创建并回收专用资源。"""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    os.environ.get("QAH_NETWORK_TEST") != "1", reason="需要显式启用特权 Docker 网络验收"
)
def test_real_public_egress_and_bypass_denial() -> None:
    repo = Path(__file__).resolve().parents[1]
    image = os.environ.get("QAH_APP_IMAGE", "qq-agent-harness:1.0.0")
    sandbox = os.environ.get("QAH_SANDBOX_IMAGE", "qq-agent-harness-sandbox:1.0.0")
    suffix = uuid4().hex[:12]
    helper_image = f"qah-network-test:{suffix}"
    internet = f"qah-test-internet-{suffix}"
    proxy = f"qah-test-egress-{suffix}"
    decoy = f"qah-test-decoy-{suffix}"
    execution = f"qah-test-execution-{suffix}"
    volume = f"qah-test-workspace-{suffix}"
    network_created = False
    image_created = False
    owned_containers: list[str] = []
    owned_networks: list[str] = []
    volume_created = False

    def docker(
        *args: str, check: bool = True, text_input: str | None = None, timeout: int = 180
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", *args],
            input=text_input,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf8",
            timeout=timeout,
            cwd=repo,
        )

    def helper(script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return docker(
            "run",
            "--rm",
            "--privileged",
            "--network",
            "host",
            "--user",
            "0:0",
            "--mount",
            "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
            "--entrypoint",
            "sh",
            helper_image if image_created else image,
            "-c",
            script,
            check=check,
        )

    names = docker("network", "ls", "--format", "{{.Name}}").stdout.splitlines()
    assert "qah-sandbox" not in names, "专用网络已存在，测试拒绝接管"
    initial = helper(
        "set -e; test ! -d /sys/class/net/br-qah-sbx; iptables -S; ip6tables -S"
    ).stdout
    assert "QAH-SANDBOX" not in initial and "br-qah-sbx" not in initial, (
        "发现已有专用防火墙配置，测试拒绝接管"
    )
    try:
        dockerfile = f"FROM {image}\nUSER 0:0\nCOPY qq_agent_harness/sandbox/ /app/qq_agent_harness/sandbox/\nCOPY deploy/sandbox/ /app/deploy/sandbox/\n"
        docker("build", "-f", "-", "-t", helper_image, ".", text_input=dockerfile)
        image_created = True
        docker("network", "create", internet)
        owned_networks.append(internet)
        docker(
            "network",
            "create",
            "--internal",
            "--subnet",
            "172.30.240.0/24",
            "--opt",
            "com.docker.network.bridge.name=br-qah-sbx",
            "qah-sandbox",
        )
        owned_networks.append("qah-sandbox")
        network_created = True
        owned_containers.append(proxy)
        docker(
            "create",
            "--name",
            proxy,
            "--network",
            internet,
            "--user",
            "1000:1000",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            helper_image,
            "python",
            "-m",
            "qq_agent_harness.sandbox.egress",
        )
        docker("network", "connect", "--ip", "172.30.240.2", "qah-sandbox", proxy)
        docker("start", proxy)
        owned_containers.append(decoy)
        docker(
            "run",
            "-d",
            "--name",
            decoy,
            "--network",
            "qah-sandbox",
            "--ip",
            "172.30.240.4",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            sandbox,
            "python",
            "-m",
            "http.server",
            "8080",
            "--bind",
            "0.0.0.0",
        )
        helper("sh /app/deploy/sandbox/prepare-network.sh")
        # 核验真实 host 规则和真实 Docker 网络，而非 mock 规则列表。
        helper(
            'python -c \'import asyncio; from qq_agent_harness.sandbox.docker import DockerEngine; from qq_agent_harness.sandbox.quota import NetworkGuard; engine=DockerEngine("/unused"); asyncio.run(NetworkGuard("br-qah-sbx","172.30.240.2",3128,lambda:engine.network_info("qah-sandbox")).check())\''
        )
        docker(
            "exec",
            decoy,
            "python",
            "-c",
            "import socket; socket.create_connection(('127.0.0.1',8080),timeout=5).close()",
        )
        docker("volume", "create", volume)
        volume_created = True
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "-v",
            f"{volume}:/workspace",
            sandbox,
            "chown",
            "1000:1000",
            "/workspace",
        )
        owned_containers.append(execution)
        script = r"""
import json, os, socket, subprocess, urllib.request, urllib.error
from pathlib import Path
results = {}
for name, address in {
    "host": ("172.30.240.1", 6060),
    "private_service": ("172.30.240.4", 8080),
    "public_bypass": ("1.1.1.1", 443),
    "ipv6_bypass": ("2606:4700:4700::1111", 443),
    "ipv6_mapped_host": ("::ffff:172.30.240.1", 6060),
}.items():
    try:
        connection = socket.create_connection(address, timeout=3)
    except OSError:
        results[name] = "blocked"
    else:
        connection.close()
        raise AssertionError(name + " unexpectedly connected")
for protocol in ("http", "https"):
    with urllib.request.urlopen(protocol + "://example.com/", timeout=25) as response:
        assert response.status == 200
    results[protocol] = "ok"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
url = "https://httpbin.org/redirect-to?url=http%3A%2F%2F172.30.240.4%3A8080%2F"
try:
    urllib.request.build_opener(NoRedirect()).open(url, timeout=25)
except urllib.error.HTTPError as error:
    assert error.code == 302, "redirect fixture did not return 302"
try:
    urllib.request.urlopen(url, timeout=25)
except urllib.error.HTTPError as error:
    assert error.code == 403, "private redirect was not rejected by proxy"
else:
    raise AssertionError("private redirect unexpectedly succeeded")
results["private_redirect"] = "blocked"
subprocess.run(["uv", "venv", "/workspace/venv"], check=True, capture_output=True)
subprocess.run(["uv", "pip", "install", "--python", "/workspace/venv/bin/python", "idna==3.10"], check=True, capture_output=True, timeout=60)
results["uv"] = "ok"
version = subprocess.check_output(["npm", "view", "is-number@7.0.0", "version"], text=True, timeout=45).strip()
assert version == "7.0.0"
results["npm"] = "ok"
print(json.dumps(results))
"""
        result = docker(
            "run",
            "--rm",
            "--name",
            execution,
            "--network",
            "qah-sandbox",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "256",
            "--memory",
            "4g",
            "--cpus",
            "2",
            "--dns",
            "127.0.0.1",
            "-v",
            f"{volume}:/workspace",
            "-e",
            "HOME=/workspace",
            "-e",
            "TMPDIR=/workspace",
            "-e",
            "UV_CACHE_DIR=/workspace/cache",
            "-e",
            "npm_config_cache=/workspace/npm-cache",
            "-e",
            "HTTP_PROXY=http://172.30.240.2:3128",
            "-e",
            "HTTPS_PROXY=http://172.30.240.2:3128",
            "-e",
            "NO_PROXY=",
            "-e",
            "no_proxy=",
            sandbox,
            "python",
            "-c",
            script,
            check=False,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        results = json.loads(result.stdout.strip().splitlines()[-1])
        assert results["https"] == "ok" and results["uv"] == "ok" and results["npm"] == "ok"
        print("sandbox network results:", results)
    finally:
        for container in reversed(owned_containers):
            docker("rm", "-f", container, check=False)
        if network_created:
            cleanup = r"""
set -eu
for chain in FORWARD DOCKER-USER; do
 while iptables -C "$chain" -i br-qah-sbx -j QAH-SANDBOX 2>/dev/null; do iptables -D "$chain" -i br-qah-sbx -j QAH-SANDBOX; done
done
while iptables -C INPUT -i br-qah-sbx -j REJECT 2>/dev/null; do iptables -D INPUT -i br-qah-sbx -j REJECT; done
for chain in INPUT FORWARD; do
 while ip6tables -C "$chain" -i br-qah-sbx -j REJECT 2>/dev/null; do ip6tables -D "$chain" -i br-qah-sbx -j REJECT; done
done
iptables -F QAH-SANDBOX 2>/dev/null || true
iptables -X QAH-SANDBOX 2>/dev/null || true
"""
            helper(cleanup)
        for network in reversed(owned_networks):
            docker("network", "rm", network)
        if volume_created:
            docker("volume", "rm", volume)
        if image_created:
            docker("image", "rm", helper_image)
            image_created = False
        final = helper("iptables -S; ip6tables -S").stdout
        assert "QAH-SANDBOX" not in final and "br-qah-sbx" not in final
        assert (
            "qah-sandbox"
            not in docker("network", "ls", "--format", "{{.Name}}").stdout.splitlines()
        )
