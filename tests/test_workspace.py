"""持久化共享工作区（Workspace）行为验证：路径边界、软容量预算、真实本地子进程、洁净环境与 Web 接口。"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_harness_core import make_store
from test_napcat import Bot

from qq_agent_harness.contracts import ConversationScope, ToolContext
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.settings import WorkspaceSettings
from qq_agent_harness.web import create_app
from qq_agent_harness.workspace import (
    ExecuteRequest,
    Workspace,
    WorkspaceService,
    WorkspaceUnavailable,
    workspace_exec_ids,
)
from qq_agent_harness.workspace import build_tools as workspace_tools
from qq_agent_harness.workspace.executor import LocalSpawner


def test_workspace_path_boundaries_and_config_overlap(tmp_path: Path) -> None:
    ws_dir = tmp_path / "workspace"
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    ws = Workspace(ws_dir)
    ws.prepare()
    ws.ensure_distinct_from(cfg_dir)

    # 拒绝工作区与配置目录重叠或互为父子目录
    with pytest.raises(WorkspaceUnavailable):
        Workspace(cfg_dir).ensure_distinct_from(cfg_dir)
    with pytest.raises(WorkspaceUnavailable):
        Workspace(cfg_dir / "sub").ensure_distinct_from(cfg_dir)
    with pytest.raises(WorkspaceUnavailable):
        Workspace(tmp_path).ensure_distinct_from(cfg_dir)

    # 拒绝父目录穿越、绝对路径、反斜杠与盘符
    for invalid in ("../secret.txt", "/etc/passwd", "a\\b.txt", "C:test.txt", "a/../.."):
        with pytest.raises(ValueError):
            ws.path(invalid)

    # 合法包含双点的文件名可正常读写
    ws.write("notes/v1..v2.md", b"diff ok")
    assert ws.read("notes/v1..v2.md", 1024) == b"diff ok"

    # 若平台支持创建符号链接，拒绝指向工作区外部的符号链接
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = ws_dir / "escape_link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pass
    else:
        with pytest.raises(ValueError):
            ws.read("escape_link.txt", 1024)


async def test_workspace_soft_budget_blocks_writes_but_keeps_read_and_cleanup(
    tmp_path: Path,
) -> None:
    ws_dir = tmp_path / "ws"
    settings = WorkspaceSettings(enabled=True, root=str(ws_dir), max_bytes=1_000_000)
    service = WorkspaceService(lambda: settings)

    # 写入接近上限的数据
    payload = "a" * 900_000
    await service.write_file("big.txt", payload)
    # 再次写入超出 1 MB 软上限时拒绝
    with pytest.raises(WorkspaceUnavailable, match="预算上限"):
        await service.write_file("extra.txt", "b" * 200_000)
    # 执行命令前若已超限同样拒绝
    settings.max_bytes = 500_000
    with pytest.raises(WorkspaceUnavailable, match="预算上限"):
        await service.execute(ExecuteRequest(command='python -c "print(1)"'))

    # 但读取、列表、删除与清理在超限状态下全部可用
    read_res = await service.read_text("big.txt", 0, 32)
    assert read_res["content"] == "a" * 32
    total_files = (await service.files())["total"]
    assert isinstance(total_files, int) and total_files >= 1
    await service.delete_path("big.txt")
    assert not (ws_dir / "big.txt").exists()


async def test_real_local_subprocess_clean_env_tmpdir_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 注入宿主敏感环境变量哨兵，确认子进程绝不可见
    monkeypatch.setenv("HARNESS_TEST_SECRET_TOKEN", "SUPER_SECRET_SENTINEL_999")
    ws_dir = tmp_path / "real_ws"
    settings = WorkspaceSettings(
        enabled=True,
        root=str(ws_dir),
        command_timeout=10.0,
        output_limit=65_536,
    )
    service = WorkspaceService(lambda: settings)
    scope = ConversationScope(bot_id="10001", kind="group", peer_id="20002")
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    store = make_store(cfg_dir)
    store.settings.workspace = settings
    store.settings.groups["20002"] = store.settings.groups["a"].model_copy(deep=True)
    store.settings.groups["20002"].policy.categories["workspace"] = True
    reg = ToolRegistry(lambda: store.settings)
    for tool in workspace_tools(service):
        reg.add(tool)
    ctx = ToolContext(scope=scope, turn_id="t-real", napcat=Bot())

    # 1. 真实 Python 子进程读写工作区、检查洁净环境与独占 TMPDIR
    script = (
        "import json, os, pathlib, tempfile; "
        "tmp = pathlib.Path(tempfile.gettempdir()); "
        "(tmp / 'scratch.tmp').write_text('ephemeral', encoding='utf-8'); "
        "pathlib.Path('hello.txt').write_text('persisted-ok', encoding='utf-8'); "
        "print(json.dumps({"
        "'secret': os.environ.get('HARNESS_TEST_SECRET_TOKEN'), "
        "'tmpdir': str(tmp), "
        "'cwd': os.getcwd()"
        "}))"
    )
    exec_res = await reg.execute(
        ctx,
        "workspace__exec",
        {"command": f'python -c "{script}"'},
    )
    assert exec_res.status == "ok" and isinstance(exec_res.data, dict)
    assert exec_res.data["exit_code"] == 0
    out = str(exec_res.data["output"])
    assert '"secret": null' in out
    assert "SUPER_SECRET_SENTINEL_999" not in out
    assert (ws_dir / "hello.txt").read_text(encoding="utf-8") == "persisted-ok"
    # 单次命令结束后 .tmp/exec-* 已被自动清理
    assert list((ws_dir / ".tmp").glob("exec-*")) == []

    # 2. 非零退出码返回 NonZeroExit 错误且保留输出与退出码
    fail_res = await reg.execute(
        ctx,
        "workspace__exec",
        {"command": "python -c \"import sys; print('boom'); sys.exit(7)\""},
    )
    assert fail_res.status == "error" and fail_res.error_type == "NonZeroExit"
    assert isinstance(fail_res.data, dict) and fail_res.data["exit_code"] == 7
    assert "boom" in str(fail_res.data["output"])

    # 3. 真实子进程超时强杀整棵进程树并回收锁
    timeout_res = await reg.execute(
        ctx,
        "workspace__exec",
        {"command": 'python -c "import time; time.sleep(30)"', "timeout": 0.4},
    )
    assert timeout_res.status == "error" and timeout_res.error_type == "CommandTimeout"
    assert not service.is_busy
    assert list((ws_dir / ".tmp").glob("exec-*")) == []


def test_web_workspace_endpoints_status_files_and_cleanup(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    ws_dir = tmp_path / "ws_web"
    cfg_dir.mkdir()
    store = make_store(cfg_dir)
    data = store.settings.model_dump(mode="json")
    data["workspace"]["enabled"] = True
    data["workspace"]["root"] = str(ws_dir)
    store.save(data, store.last_sha)

    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "report.md").write_text("# Report\n", encoding="utf-8")
    (ws_dir / ".cache").mkdir(exist_ok=True)
    (ws_dir / ".cache" / "wheel.bin").write_bytes(b"123456")

    app = create_app(store.path, connect_mcp=False)
    with TestClient(app) as client:
        st = client.get("/api/workspace/status")
        assert st.status_code == 200
        st_json = st.json()
        assert st_json["enabled"] is True and st_json["available"] is True
        assert os.path.normcase(st_json["root"]) == os.path.normcase(str(ws_dir.resolve()))
        assert isinstance(st_json["used_bytes"], int) and st_json["used_bytes"] >= 6

        listing = client.get("/api/workspace/files").json()
        names = {item["name"]: item for item in listing["files"]}
        assert "report.md" in names and names["report.md"]["is_system"] is False
        assert ".cache" in names and names[".cache"]["is_system"] is True

        # 未确认时拒绝清理
        unconfirmed = client.post(
            "/api/workspace/cleanup", json={"mode": "cache", "confirmed": False}
        )
        assert unconfirmed.status_code == 400

        # 清理缓存保留用户文件
        cleaned = client.post("/api/workspace/cleanup", json={"mode": "cache", "confirmed": True})
        assert cleaned.status_code == 200
        assert (ws_dir / "report.md").exists()
        assert list((ws_dir / ".cache").iterdir()) == []


async def test_workspace_dual_uid_privilege_drop_and_chown_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qq_agent_harness.workspace import executor as executor_mod
    from qq_agent_harness.workspace import storage as storage_mod

    # 1. 校验非法/特权 UID 被明确拒绝（模拟 POSIX root 环境）
    monkeypatch.setattr(storage_mod.sys, "platform", "linux")
    monkeypatch.setattr(storage_mod.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setenv("HARNESS_WORKSPACE_UID", "0")
    with pytest.raises(ValueError, match="必须为大于 0"):
        workspace_exec_ids()

    monkeypatch.setenv("HARNESS_WORKSPACE_UID", "1000")
    monkeypatch.setenv("HARNESS_WORKSPACE_GID", "1000")
    assert workspace_exec_ids() == (1000, 1000)

    # 2. POSIX LocalSpawner.spawn 在双 UID 启用时必须传递 user/group/extra_groups=[]
    captured: list[tuple[tuple[object, ...], dict[str, Any]]] = []

    class FakeProc:
        stdout = None
        returncode = 0
        pid = 12345

        async def wait(self) -> int:
            return 0

    async def fake_exec(*args: object, **kwargs: Any) -> FakeProc:
        captured.append((args, kwargs))
        return FakeProc()

    with monkeypatch.context() as mp:
        mp.setattr(executor_mod.os, "name", "posix")
        mp.setattr(executor_mod, "workspace_exec_ids", lambda: (1000, 1000))
        mp.setattr(asyncio, "create_subprocess_exec", fake_exec)

        await LocalSpawner().spawn("id", tmp_path, {"PATH": "/usr/bin"})
    assert len(captured) == 1
    spawn_kwargs = captured[0][1]
    assert spawn_kwargs["user"] == 1000
    assert spawn_kwargs["group"] == 1000
    assert spawn_kwargs["extra_groups"] == []
    assert spawn_kwargs["start_new_session"] is True

    # 3. 验证 Workspace 写入文件和创建目录时在双 UID 模式下同步触发 _chown_path / _chown_fd
    chown_paths: list[tuple[Path, int, int]] = []
    chown_fds: list[tuple[int, int, int]] = []

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        storage_mod.os,
        "chown",
        lambda path, uid, gid, **_kw: chown_paths.append((Path(path), uid, gid)),
        raising=False,
    )
    monkeypatch.setattr(
        storage_mod.os,
        "fchown",
        lambda fd, uid, gid: chown_fds.append((fd, uid, gid)),
        raising=False,
    )
    ws = Workspace(tmp_path / "dual_ws")
    ws.write("sub/data.txt", b"hello")
    assert any(p == ws.root and u == 1000 and g == 1000 for p, u, g in chown_paths)
    assert any(p == ws.root / "sub" and u == 1000 and g == 1000 for p, u, g in chown_paths)
    assert len(chown_fds) == 1 and chown_fds[0][1:] == (1000, 1000)
