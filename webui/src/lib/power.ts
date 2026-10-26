import { useSyncExternalStore } from "react";
import { errorMessage, post, request } from "./api";
import { loadConfig } from "./config";
import { confirm, toast } from "./ui";

export type PowerPhase = "idle" | "restarting" | "stopped" | "failed";
interface PowerState {
  phase: PowerPhase;
  message: string;
}
let state: PowerState = { phase: "idle", message: "" };
const listeners = new Set<() => void>();
function emit(next: PowerState) {
  state = next;
  listeners.forEach((listener) => listener());
}
export function usePower(): PowerState {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => state,
  );
}

async function bootId(): Promise<string | null> {
  try {
    return (await request<{ boot_id: string }>("/api/health")).boot_id;
  } catch {
    return null;
  }
}

/** 请求重启并等待新进程（boot_id 改变）就绪，最多 90 秒。 */
export async function restartHarness(): Promise<void> {
  const accepted = await confirm({
    title: "重启 Harness",
    message: "正在进行的对话会被中断，已经发出的消息和执行过的操作不会撤回。重启通常需要几秒钟。",
    confirmLabel: "重启",
    danger: true,
  });
  if (!accepted) return;
  const before = await bootId();
  try {
    await post("/api/system/restart");
  } catch (error) {
    toast("无法重启：" + errorMessage(error), "danger");
    return;
  }
  emit({ phase: "restarting", message: "" });
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const current = await bootId();
    if (current && current !== before) {
      emit({ phase: "idle", message: "" });
      await loadConfig();
      toast("Harness 已重启", "success");
      return;
    }
  }
  emit({
    phase: "failed",
    message: "90 秒内服务没有恢复。请在服务器上查看容器状态和日志。",
  });
}

export async function shutdownHarness(): Promise<void> {
  const accepted = await confirm({
    title: "关闭 Harness",
    message: "关闭后 Agent 停止响应，控制台也无法访问，需要在服务器上重新启动容器才能恢复。",
    confirmLabel: "关闭服务",
    danger: true,
  });
  if (!accepted) return;
  try {
    await post("/api/system/shutdown");
  } catch (error) {
    toast("无法关闭：" + errorMessage(error), "danger");
    return;
  }
  emit({ phase: "stopped", message: "" });
}

export function dismissPower() {
  emit({ phase: "idle", message: "" });
}
