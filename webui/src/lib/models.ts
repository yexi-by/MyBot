import { conversationEntries } from "../schema/fields";
import { conversationRoute, groupTitle } from "./conversations";
import { getAt, object } from "./types";
import type { GroupInfo, Json, ObjectValue, Path } from "./types";

/** 可在会话之间复制的模型设置；路径相对于会话。 */
export interface ModelPart {
  id: string;
  label: string;
  paths: Path[];
}
export const modelParts: ModelPart[] = [
  { id: "model", label: "服务商与模型名称", paths: [["model", "provider"], ["model", "name"]] },
  { id: "reasoning", label: "思考等级", paths: [["model", "reasoning_effort"]] },
  {
    id: "budget",
    label: "上下文预算与压缩目标",
    paths: [
      ["model", "max_context_tokens"],
      ["model", "compaction_target_percent"],
    ],
  },
  { id: "modalities", label: "支持的输入类型", paths: [["model", "modalities"]] },
  {
    id: "idle",
    label: "闲时压缩开关与条件",
    paths: [
      ["idle_compaction", "enabled"],
      ["idle_compaction", "after_minutes"],
      ["idle_compaction", "min_tokens"],
    ],
  },
  { id: "compactor", label: "压缩模型", paths: [["idle_compaction", "model"]] },
];

/** 把来源会话中所选部分的设置写到各目标会话。 */
export function modelChanges(source: ObjectValue, targets: Path[], parts: string[]): [Path, Json][] {
  const changes: [Path, Json][] = [];
  for (const part of modelParts) {
    if (!parts.includes(part.id)) continue;
    for (const relative of part.paths) {
      const value = getAt(source, relative);
      if (value === undefined) continue;
      for (const target of targets) changes.push([[...target, ...relative], structuredClone(value)]);
    }
  }
  return changes;
}

/** 会话是否选好了会话模型；与服务端一致：服务商存在且模型名称非空。 */
export function hasSessionModel(config: ObjectValue, conversation: ObjectValue): boolean {
  const model = object(conversation.model);
  return (
    typeof model.provider === "string" &&
    Object.hasOwn(object(config.providers), model.provider) &&
    typeof model.name === "string" &&
    model.name !== ""
  );
}

export interface ConversationRef {
  base: Path;
  kind: "group" | "private";
  id: string;
  label: string;
  route: string;
}

/** 配置中的全部会话，带显示名和模型页地址。 */
export function conversationRefs(config: ObjectValue, groups: Map<string, GroupInfo>): ConversationRef[] {
  return conversationEntries(config).map(([base]) => {
    const kind = base[0] === "private" ? "private" : "group";
    const id = kind === "group" ? String(base[1]) : "";
    return {
      base,
      kind,
      id,
      label: kind === "private" ? "私聊" : groupTitle(id, groups),
      route: conversationRoute(kind, id, "model"),
    };
  });
}

export interface ProviderUse extends ConversationRef {
  parts: string[];
}

/** 引用某个服务商的会话，以及引用它的是会话模型还是压缩模型。 */
export function providerUsage(
  config: ObjectValue,
  provider: string,
  groups: Map<string, GroupInfo>,
): ProviderUse[] {
  const uses: ProviderUse[] = [];
  for (const ref of conversationRefs(config, groups)) {
    const conversation = getAt(config, ref.base);
    const parts = [
      ...(getAt(conversation, ["model", "provider"]) === provider ? ["会话模型"] : []),
      ...(getAt(conversation, ["idle_compaction", "model", "provider"]) === provider ? ["压缩模型"] : []),
    ];
    if (parts.length) uses.push({ ...ref, parts });
  }
  return uses;
}
