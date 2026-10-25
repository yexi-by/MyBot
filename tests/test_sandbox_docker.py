"""显式 QAH_DOCKER_TEST=1 时使用本机 Docker 做真实隔离烟测。"""

import os
import subprocess
from uuid import uuid4

import pytest


@pytest.mark.skipif(os.environ.get("QAH_DOCKER_TEST") != "1", reason="需要显式启用 Docker 集成测试")
def test_real_toolchain_isolation_and_shared_files() -> None:
    image = os.environ.get("QAH_SANDBOX_IMAGE", "qq-agent-harness-sandbox:1.0.0")
    volume = "qah-test-" + uuid4().hex

    def docker(*args: str) -> str:
        return subprocess.run(
            ["docker", *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf8",
            timeout=120,
        ).stdout

    docker("volume", "create", volume)
    try:
        docker(
            "run",
            "--rm",
            "--user",
            "0:0",
            "--network",
            "none",
            "-v",
            f"{volume}:/workspace",
            image,
            "chown",
            "1000:1000",
            "/workspace",
        )
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
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "-v",
            f"{volume}:/workspace",
            "-e",
            "HOME=/workspace",
            "-e",
            "UV_CACHE_DIR=/workspace/cache",
            "-e",
            "TMPDIR=/workspace",
            image,
        ]
        check = """import os,pathlib,subprocess
assert os.getuid()==1000
s=pathlib.Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in s and 'NoNewPrivs:\\t1' in s
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/app/config').exists()
try:
 pathlib.Path('/etc/qah-test').write_text('forbidden')
 raise AssertionError('root filesystem was writable')
except OSError: pass
pathlib.Path('/workspace/answer.txt').write_text('42')
subprocess.run(['cp','--reflink=auto','/workspace/answer.txt','/workspace/copy.txt'],check=True)
assert pathlib.Path('/workspace/copy.txt').read_text()=='42'
subprocess.run(['uv','venv','/workspace/venv'],check=True)
print(subprocess.check_output(['node','-p','6*7'],text=True).strip())
"""
        assert docker(*base, "python", "-c", check).strip() == "42"
        assert (
            docker(
                *base,
                "python",
                "-c",
                "from pathlib import Path; print(Path('/workspace/answer.txt').read_text())",
            ).strip()
            == "42"
        )
    finally:
        docker("volume", "rm", volume)
