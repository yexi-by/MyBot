import { object } from "./types";
import type { ObjectValue, Tool } from "./types";

export type Override = "inherit" | "allow" | "deny";
export interface Access {
  allowed: boolean;
  reason: string;
}

export function overrideOf(policy: ObjectValue, name: string): Override {
  const value = object(policy.tools)[name];
  return value === true ? "allow" : value === false ? "deny" : "inherit";
}

/**
 * 与后端 registry.permitted 相同的判断，只用于界面即时反馈草稿的效果；
 * 实际调用时由服务端按已保存的配置重新检查。
 */
export function evaluate(
  config: ObjectValue,
  conversation: ObjectValue,
  kind: "group" | "private",
  tool: Tool,
  catalog: Map<string, Tool>,
): Access {
  if (conversation.enabled !== true) return { allowed: false, reason: "会话未启用" };
  if (tool.name.startsWith("sandbox__") && object(config.sandbox).enabled !== true)
    return { allowed: false, reason: "共享沙箱未启用" };
  if (tool.name.startsWith("image__")) {
    const image = object(config.image);
    if (image.enabled !== true) return { allowed: false, reason: "尚未启用图像生成" };
    if (
      typeof image.base_url !== "string" ||
      !image.base_url.trim() ||
      typeof image.model !== "string" ||
      !image.model.trim()
    )
      return { allowed: false, reason: "图像生成尚未配置接口地址或模型名称" };
  }
  if (tool.scope !== "both" && tool.scope !== "account" && tool.scope !== kind)
    return { allowed: false, reason: kind === "group" ? "不适用于群聊" : "不适用于私聊" };
  const policy = object(conversation.policy);
  const override = object(policy.tools)[tool.name];
  const allowed =
    typeof override === "boolean"
      ? override
      : object(policy.categories)[tool.category] === true;
  for (const name of tool.requires ?? []) {
    const prerequisite = catalog.get(name);
    if (!prerequisite || !evaluate(config, conversation, kind, prerequisite, catalog).allowed)
      return { allowed: false, reason: "还需开放「" + (prerequisite?.title ?? name) + "」" };
  }
  return { allowed, reason: allowed ? "" : "未开放" };
}

/** 工具是否适用于该类会话；不适用的工具在权限列表中折叠展示。 */
export function applicable(tool: Tool, kind: "group" | "private") {
  return tool.scope === "both" || tool.scope === "account" || tool.scope === kind;
}
