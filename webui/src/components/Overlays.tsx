/** 全局浮层：配置冲突三出口、电源确认与等待。 */

import { PlugZap, Power, RotateCw } from "lucide-react";
import { useState } from "react";

import { config, useConfig, type Change } from "@/lib/config";
import { cancelPower, confirmPower, reconnect, usePower, type PowerAction } from "@/lib/power";

import { Dialog, useLatest } from "./Dialog";

export function ChangeList({ changes, limit = 12 }: { changes: Change[]; limit?: number }) {
  return (
    <ul className="dlg-list">
      {changes.slice(0, limit).map((change) => (
        <li key={change.key}>
          <span>{change.label}</span>
          <span className="dlg-diff">
            {change.from} → <b>{change.to}</b>
          </span>
        </li>
      ))}
      {changes.length > limit ? <li className="dlg-note">另有 {changes.length - limit} 处</li> : null}
    </ul>
  );
}

export function ConfigConflictDialog() {
  const conflict = useConfig((state) => state.conflict);
  const shown = useLatest(conflict) ?? [];
  const [busy, setBusy] = useState(false);
  const run = async (choice: "overwrite" | "discard" | "keep") => {
    setBusy(true);
    try {
      await config.resolveConflict(choice);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={conflict !== null}
      onClose={() => {
        if (!busy) void run("keep");
      }}
      className="dlg--danger dlg--wide"
      labelledBy="conflict-title"
    >
      <h2 className="dlg-title" id="conflict-title">
        mybot.toml 已被外部修改
      </h2>
      <div className="dlg-body">
        <p>写入时发现文件在别处被改过（直接编辑了文件，或另一个窗口先保存了）。这里还有 {shown.length} 处修改没有写入：</p>
        {shown.length > 0 ? <ChangeList changes={shown} /> : null}
        <p className="dlg-note">
          覆盖：用当前表单整体写回，外部修改会丢失。丢弃并刷新：放弃这些修改，载入外部改过的文件。继续编辑：暂不处理，下一次写入时覆盖。
        </p>
      </div>
      <div className="dlg-actions">
        <button type="button" className="btn" onClick={() => void run("keep")} disabled={busy} autoFocus>
          继续编辑
        </button>
        <span className="spacer" />
        <button type="button" className="btn" onClick={() => void run("discard")} disabled={busy}>
          丢弃并刷新
        </button>
        <button type="button" className="btn btn--danger" onClick={() => void run("overwrite")} disabled={busy}>
          覆盖外部修改
        </button>
      </div>
    </Dialog>
  );
}

const COPY: Record<PowerAction, { title: string; body: string; button: string }> = {
  restart: {
    title: "重启 MyBot？",
    body: "会先写完待保存的修改，再让进程优雅停机。能否自动拉起取决于部署方式：Docker 的 restart: unless-stopped 会自动拉起，手动运行时需要自己重新启动。",
    button: "重启",
  },
  shutdown: {
    title: "关闭 MyBot？",
    body: "会先写完待保存的修改，再让进程优雅退出，机器人随之离线。Docker 守护下进程会被重新拉起，效果等同重启。",
    button: "关闭",
  },
};

export function PowerLayer() {
  const phase = usePower();
  const restart = useConfig((state) => state.restartSections);
  const asking = phase.kind === "confirm" || phase.kind === "flushing";
  const action: PowerAction | null = asking ? phase.action : null;
  const shownAction = useLatest(action) ?? "restart";
  const copy = COPY[shownAction];
  const waiting = phase.kind === "restarting" || phase.kind === "restart_timeout" || phase.kind === "shutdown";
  const shownWait = useLatest(waiting ? phase.kind : null);
  const [busy, setBusy] = useState(false);
  const retry = async () => {
    setBusy(true);
    await reconnect();
    setBusy(false);
  };
  return (
    <>
      <Dialog
        open={asking}
        onClose={() => {
          if (phase.kind === "confirm") cancelPower();
        }}
        className={shownAction === "shutdown" ? "dlg--danger" : undefined}
        labelledBy="power-title"
      >
        <h2 className="dlg-title" id="power-title">
          {copy.title}
        </h2>
        <div className="dlg-body">
          <p>{copy.body}</p>
          {shownAction === "restart" && restart.length > 0 ? <p className="dlg-note">重启后，待重启生效的改动会一起生效。</p> : null}
        </div>
        <div className="dlg-actions">
          <button type="button" className="btn" onClick={cancelPower} disabled={phase.kind === "flushing"} autoFocus>
            取消
          </button>
          <button
            type="button"
            className={shownAction === "shutdown" ? "btn btn--danger" : "btn btn--primary"}
            onClick={() => void confirmPower()}
            disabled={phase.kind === "flushing"}
          >
            {phase.kind === "flushing" ? <span className="spinner" aria-hidden /> : null}
            {phase.kind === "flushing" ? "正在写入待保存的修改…" : copy.button}
          </button>
        </div>
      </Dialog>
      <Dialog open={waiting} onClose={() => undefined} dismissible={false} className="power" labelledBy="power-wait-title">
        {shownWait === "restarting" ? (
          <>
            <span className="power-spin" aria-hidden />
            <p className="power-title" id="power-wait-title">
              正在重启 MyBot…
            </p>
            <p className="power-sub">等待新进程接管，通常需要几秒；期间不能编辑。</p>
          </>
        ) : shownWait === "restart_timeout" ? (
          <>
            <span className="power-icon" aria-hidden>
              <RotateCw className="ic" />
            </span>
            <p className="power-title" id="power-wait-title">
              60 秒内没有等到新进程
            </p>
            <p className="power-sub">可能没有配置自动拉起，或启动失败了。检查容器状态和日志后再重新连接。</p>
            <div className="dlg-actions">
              <button type="button" className="btn btn--primary" onClick={() => void retry()} disabled={busy} autoFocus>
                <PlugZap className="ic" aria-hidden />
                重新连接
              </button>
            </div>
          </>
        ) : (
          <>
            <span className="power-icon" aria-hidden>
              <Power className="ic" />
            </span>
            <p className="power-title" id="power-wait-title">
              MyBot 已关闭
            </p>
            <p className="power-sub">进程已优雅退出。Docker 守护下会被自动拉起；手动部署需要重新启动后再连接。</p>
            <div className="dlg-actions">
              <button type="button" className="btn btn--primary" onClick={() => void retry()} disabled={busy} autoFocus>
                <PlugZap className="ic" aria-hidden />
                重新连接
              </button>
            </div>
          </>
        )}
      </Dialog>
    </>
  );
}
