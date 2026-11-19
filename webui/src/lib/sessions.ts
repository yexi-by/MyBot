import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, get, post } from "./api";
import { runtimeResource } from "./resource";
import { object } from "./types";
import type { Json, ObjectValue, QQMessage, SessionDetail, SessionEntry, SubagentDetail } from "./types";
import { confirm, toast } from "./ui";

export const sessionRoute = (key: string) => "/sessions/" + encodeURIComponent(key);
export const subagentRoute = (key: string, id: string) =>
  sessionRoute(key) + "/subagents/" + encodeURIComponent(id);

/** 排队、运行、重试或压缩中的会话不能直接重置，查看器也按更短间隔刷新。 */
export const isBusy = (state: string | undefined) =>
  state === "queued" || state === "running" || state === "compacting" || state === "retrying";

export const isSubagentActive = (state: string | undefined) =>
  state === "running" || state === "retrying" || state === "compacting";

/**
 * QQ 消息段或发送参数中的一段转成可读文字。NapCat 消息段的字段在 data 里，
 * 发送工具的 parts 字段直接在段上；媒体、卡片和转发只显示类型。
 */
export function segmentText(segment: Json): string {
  const part = object(segment);
  const data = part.data !== undefined ? object(part.data) : part;
  const value = (...keys: string[]) => {
    for (const key of keys) {
      const item = data[key];
      if (typeof item === "string" || typeof item === "number") return String(item);
    }
    return "";
  };
  switch (part.type) {
    case "text":
      return value("text");
    case "at":
      return value("qq") === "all" ? "@全体成员 " : "@" + (value("name") || value("qq")) + " ";
    case "reply":
      return "[回复 " + value("id", "message_id") + "] ";
    case "face":
      return "[表情]";
    case "mface":
      return "[商城表情]";
    case "image":
      return value("summary") || "[图片]";
    case "record":
      return "[语音]";
    case "video":
      return "[视频]";
    case "file":
      return "[文件" + (value("name", "file") ? " " + value("name", "file") : "") + "]";
    case "forward":
      return "[合并转发]";
    case "json":
    case "xml":
      return "[卡片消息]";
    case "poke":
      return "[戳一戳]";
    case "dice":
      return "[骰子]";
    case "rps":
      return "[猜拳]";
    case "ai_voice":
      return "[AI 语音] " + value("text");
    case "markdown":
      return value("content") || "[Markdown]";
    default:
      return "[" + String(part.type ?? "未知内容") + "]";
  }
}

export function messageText(segments: Json[]): string {
  return segments.map(segmentText).join("").trim();
}

/** 发送工具参数里的消息正文；其他工具返回 null。 */
export function sendPreview(name: string, args: Json): string | null {
  if (name !== "qq__send_message") return null;
  const parts = object(args).parts;
  return Array.isArray(parts) ? messageText(parts) : null;
}

export function senderLabel(message: QQMessage, botId: string): string {
  const name = message.sender_name || message.sender_id;
  return message.sender_id === botId ? name + "（机器人）" : name;
}

export type TimelineBlock =
  | { type: "entry"; entry: SessionEntry }
  | { type: "turn"; turn: number; entries: SessionEntry[] };

/**
 * 按上下文原顺序分段：系统提示、压缩摘要和切换模型转入的资料单独成块，
 * 其余条目按所属轮次合并为一段。
 */
export function groupEntries(entries: SessionEntry[]): TimelineBlock[] {
  const blocks: TimelineBlock[] = [];
  for (const entry of entries) {
    if (
      entry.turn === null ||
      entry.kind === "system" ||
      entry.kind === "summary" ||
      entry.kind === "transfer"
    ) {
      blocks.push({ type: "entry", entry });
      continue;
    }
    const last = blocks.at(-1);
    if (last?.type === "turn" && last.turn === entry.turn) last.entries.push(entry);
    else blocks.push({ type: "turn", turn: entry.turn, entries: [entry] });
  }
  return blocks;
}

export const pretty = (value: Json | undefined) =>
  typeof value === "string" ? value : JSON.stringify(value ?? null, null, 2);

interface DetailState {
  detail: SessionDetail | null;
  missing: boolean;
  error: string;
  loading: boolean;
}
const blank: DetailState = { detail: null, missing: false, error: "", loading: true };

/**
 * 轮询会话详情：处理中每 2 秒、空闲或尚未建立时每 10 秒，页面隐藏时暂停。
 * 带上已有的会话和版本，服务端未变化时只返回摘要字段，条目沿用上次结果。
 * `signal` 取自运行摘要；它变化（会话建立、新一轮开始或结束、重置）时立即刷新，不等下一次轮询。
 */
export function useSessionDetail(key: string, signal: string) {
  const [state, setState] = useState<DetailState>(blank);
  const reload = useRef<() => void>(() => {});
  const lastSignal = useRef(signal);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let held: SessionDetail | null = null;
    let sequence = 0;
    setState(blank);
    const load = async () => {
      clearTimeout(timer);
      if (cancelled || document.hidden) return;
      const request = ++sequence;
      const params = new URLSearchParams({ key });
      if (held) {
        params.set("since", String(held.version));
        params.set("session", held.session_id);
      }
      try {
        const next = await get<SessionDetail>("/api/sessions/detail?" + params.toString());
        if (cancelled || request !== sequence) return;
        held =
          next.entries === null && held?.session_id === next.session_id
            ? { ...next, entries: held.entries }
            : next;
        setState({ detail: held, missing: false, error: "", loading: false });
      } catch (error) {
        if (cancelled || request !== sequence) return;
        if (error instanceof ApiError && error.status === 404) {
          held = null;
          setState({ detail: null, missing: true, error: "", loading: false });
        } else setState((current) => ({ ...current, error: errorMessage(error), loading: false }));
      }
      timer = setTimeout(
        () => void load(),
        isBusy(held?.state) || (held?.active_subagents ?? 0) > 0 ? 2000 : 10_000,
      );
    };
    reload.current = () => void load();
    const visible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", visible);
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [key]);
  useEffect(() => {
    if (lastSignal.current === signal) return;
    lastSignal.current = signal;
    reload.current();
  }, [signal]);
  const refresh = useCallback(() => reload.current(), []);
  return { ...state, refresh };
}

interface SubagentDetailState {
  detail: SubagentDetail | null;
  missing: boolean;
  error: string;
  loading: boolean;
}
const blankSubagent: SubagentDetailState = { detail: null, missing: false, error: "", loading: true };

/** 轮询单个子代理详情：运行中每 2 秒、结束后每 10 秒，带版本号避免重复传输全部条目。 */
export function useSubagentDetail(id: string, signal: string) {
  const [state, setState] = useState<SubagentDetailState>(blankSubagent);
  const reload = useRef<() => void>(() => {});
  const lastSignal = useRef(signal);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let held: SubagentDetail | null = null;
    let sequence = 0;
    setState(blankSubagent);
    const load = async () => {
      clearTimeout(timer);
      if (cancelled || document.hidden) return;
      const request = ++sequence;
      const params = new URLSearchParams({ id });
      if (held) params.set("since", String(held.version));
      try {
        const next = await get<SubagentDetail>("/api/subagents/detail?" + params.toString());
        if (cancelled || request !== sequence) return;
        held =
          next.entries === null && held?.subagent_id === next.subagent_id
            ? { ...next, entries: held.entries }
            : next;
        setState({ detail: held, missing: false, error: "", loading: false });
      } catch (error) {
        if (cancelled || request !== sequence) return;
        if (error instanceof ApiError && error.status === 404) {
          held = null;
          setState({ detail: null, missing: true, error: "", loading: false });
        } else setState((current) => ({ ...current, error: errorMessage(error), loading: false }));
      }
      timer = setTimeout(() => void load(), isSubagentActive(held?.state) ? 2000 : 10_000);
    };
    reload.current = () => void load();
    const visible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", visible);
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [id]);
  useEffect(() => {
    if (lastSignal.current === signal) return;
    lastSignal.current = signal;
    reload.current();
  }, [signal]);
  const refresh = useCallback(() => reload.current(), []);
  return { ...state, refresh };
}

/** 停止会话当前一轮；已发出的消息和执行过的操作不会撤回，后台子代理不受影响。 */
export async function stopTurn(key: string): Promise<boolean> {
  const accepted = await confirm({
    title: "停止这一轮对话？",
    message: "已经发出的消息和执行过的操作不会撤回，会话上下文与后台子代理会继续保留。",
    confirmLabel: "停止",
    danger: true,
  });
  if (!accepted) return false;
  try {
    const result = await post<{ cancelled: boolean }>("/api/runtime/cancel", { scope_key: key });
    toast(result.cancelled ? "已停止" : "这一轮已经结束", result.cancelled ? "success" : "neutral");
    return result.cancelled;
  } catch (error) {
    toast("停止失败：" + errorMessage(error), "danger");
    return false;
  } finally {
    void runtimeResource.refresh();
  }
}

/** 清空会话的模型上下文；处理中的会话或运行中的子代理先停止再重置。 */
export async function resetSession(key: string, busy: boolean, activeSubagents = 0): Promise<boolean> {
  const needStop = busy || activeSubagents > 0;
  const accepted = await confirm({
    title: needStop ? "停止并重置这个会话？" : "重置这个会话的上下文？",
    message:
      (busy ? "正在处理的一轮会先停止。" : "") +
      (activeSubagents > 0 ? `当前会话关联的 ${activeSubagents} 个后台子代理也会一并停止并清理。` : "") +
      "清空当前会话的对话上下文、工具调用记录和摘要，下次唤醒时从近期 QQ 历史重新开始。已发出的消息不会撤回。",
    confirmLabel: needStop ? "停止并重置" : "重置",
    danger: true,
  });
  if (!accepted) return false;
  try {
    await post<{ session_id: string }>("/api/sessions/reset", { scope_key: key, stop: needStop });
    toast("会话上下文已重置", "success");
    return true;
  } catch (error) {
    toast("重置失败：" + errorMessage(error), "danger");
    return false;
  } finally {
    void runtimeResource.refresh();
  }
}

/** 停止运行中的指定子代理（不会自动触发主会话对外发言）。 */
export async function stopSubagent(subagentId: string, title?: string): Promise<boolean> {
  const accepted = await confirm({
    title: "停止这个后台子代理？",
    message:
      (title ? `将立即终止子代理「${title}」（${subagentId}）` : `将立即终止子代理 ${subagentId}`) +
      " 及其正在执行的工作区命令。已保留的轨迹可在控制台继续查看，也可以随时续跑。",
    confirmLabel: "停止子代理",
    danger: true,
  });
  if (!accepted) return false;
  try {
    const result = await post<{ cancelled: boolean }>("/api/subagents/cancel", {
      subagent_id: subagentId,
    });
    toast(result.cancelled ? "子代理已停止" : "该子代理已经结束", result.cancelled ? "success" : "neutral");
    return result.cancelled;
  } catch (error) {
    toast("停止子代理失败：" + errorMessage(error), "danger");
    return false;
  } finally {
    void runtimeResource.refresh();
  }
}

/** 管理员向子代理发送纠偏指令（运行中）或续跑指令（已结束）。 */
export async function sendSubagentInstruction(
  subagentId: string,
  message: string,
  options?: { extraSteps?: number; isResume?: boolean },
): Promise<{ mode: "steered" | "resumed"; state: string } | null> {
  try {
    const payload: ObjectValue = {
      subagent_id: subagentId,
      message,
    };
    if (options?.isResume && options.extraSteps) payload.extra_steps = options.extraSteps;
    const result = await post<{ mode: "steered" | "resumed"; state: string }>("/api/subagents/send", payload);
    toast(
      result.mode === "steered"
        ? "纠偏指令已入队，将在子代理下一步生效"
        : "子代理已按新指令继续运行",
      "success",
    );
    return result;
  } catch (error) {
    toast("发送指令失败：" + errorMessage(error), "danger");
    return null;
  } finally {
    void runtimeResource.refresh();
  }
}
