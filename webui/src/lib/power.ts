/**
 * 重启 / 关闭 MyBot：先把待写入的配置和文本落盘，再调用电源接口；
 * 重启后以 /api/config 的 boot_id 变化判定新进程已接管，避免把停机中的旧进程当成重启完成。
 */

import { api, errorMessage } from "./api";
import { config } from "./config";
import { files } from "./files";
import { Store, useStore } from "./store";
import { toast } from "./toast";

export type PowerAction = "restart" | "shutdown";

export type PowerPhase =
  | { kind: "idle" }
  | { kind: "confirm"; action: PowerAction }
  | { kind: "flushing"; action: PowerAction }
  | { kind: "restarting" }
  | { kind: "restart_timeout" }
  | { kind: "shutdown" };

const RESTART_FIRST_POLL_MS = 1500;
const RESTART_POLL_MS = 1500;
const RESTART_LIMIT_MS = 60_000;

export const powerStore = new Store<{ phase: PowerPhase }>({ phase: { kind: "idle" } });

let generation = 0;
const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

export function requestPower(action: PowerAction): void {
  const phase = powerStore.get().phase;
  if (phase.kind !== "idle") return;
  powerStore.set({ phase: { kind: "confirm", action } });
}

export function cancelPower(): void {
  if (powerStore.get().phase.kind === "confirm") powerStore.set({ phase: { kind: "idle" } });
}

export async function confirmPower(): Promise<void> {
  const phase = powerStore.get().phase;
  if (phase.kind !== "confirm") return;
  const { action } = phase;
  powerStore.set({ phase: { kind: "flushing", action } });
  config.setPowerBusy(true);
  const configClean = await config.flush();
  const filesClean = await files.flush();
  if (!configClean || !filesClean) {
    config.setPowerBusy(false);
    powerStore.set({ phase: { kind: "idle" } });
    toast.error(
      !configClean
        ? "mybot.toml 还有没能写入的修改（需要修正或冲突未处理），处理后再试"
        : "当前文本文件还有没能保存的修改，处理后再试",
    );
    return;
  }
  const bootId = config.state.meta?.boot_id ?? "";
  try {
    if (action === "restart") await api.restart();
    else await api.shutdown();
  } catch (error) {
    config.setPowerBusy(false);
    powerStore.set({ phase: { kind: "idle" } });
    toast.error(`${action === "restart" ? "重启" : "关闭"}失败：${errorMessage(error, "请求失败")}`);
    return;
  }
  if (action === "shutdown") {
    powerStore.set({ phase: { kind: "shutdown" } });
    return;
  }
  powerStore.set({ phase: { kind: "restarting" } });
  void waitForRestart(bootId);
}

async function waitForRestart(previousBootId: string): Promise<void> {
  const current = ++generation;
  const deadline = Date.now() + RESTART_LIMIT_MS;
  await sleep(RESTART_FIRST_POLL_MS);
  while (Date.now() < deadline) {
    if (current !== generation) return;
    try {
      const fresh = await api.getConfig();
      if (fresh.meta.boot_id !== previousBootId) {
        await recover();
        toast.success("MyBot 已重启完成");
        return;
      }
    } catch {
      // 进程尚未恢复，继续等待。
    }
    await sleep(RESTART_POLL_MS);
  }
  if (current === generation) powerStore.set({ phase: { kind: "restart_timeout" } });
}

async function recover(): Promise<void> {
  generation += 1;
  config.setPowerBusy(false);
  await config.load();
  await files.refreshList();
  powerStore.set({ phase: { kind: "idle" } });
}

/** 关机或重启超时后手动重连。 */
export async function reconnect(): Promise<void> {
  try {
    await api.getConfig();
  } catch {
    toast.error("仍然连不上，MyBot 还没有恢复");
    return;
  }
  await recover();
  toast.success("已重新连接");
}

export function usePower(): PowerPhase {
  return useStore(powerStore, (state) => state.phase);
}
