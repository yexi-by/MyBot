import { conversationEntries } from "../schema/fields";
import { conversationRoute, groupTitle } from "./conversations";
import { getAt, object } from "./types";
import type { GroupInfo, Json, ObjectValue, Path } from "./types";

/** 可在会话之间复制或批量应用的配置模块；默认全选。 */
export interface ConversationCopyPart {
  id: string;
  label: string;
  description: string;
}

export const conversationCopyParts: ConversationCopyPart[] = [
  {
    id: "wake_limits",
    label: "唤醒与发送限制",
    description: "唤醒关键词、每轮最多发送次数、允许 @全体成员、跨会话私聊与可操作群",
  },
  {
    id: "prompts",
    label: "提示词与知识库",
    description: "人格提示词文件与知识库文件列表",
  },
  {
    id: "model",
    label: "会话模型",
    description: "服务商、模型名称、思考等级、上下文预算、压缩目标与输入类型",
  },
  {
    id: "idle_compaction",
    label: "闲时压缩与压缩模型",
    description: "闲时压缩开关、触发条件与独立压缩模型",
  },
  {
    id: "permissions",
    label: "能力权限与参数限制",
    description: "能力分类开关、单项工具授权与工具参数限制",
  },
];

/** 把来源会话中所选模块的配置写到各目标会话。 */
export function conversationChanges(
  source: ObjectValue,
  sourceBase: Path,
  targets: Path[],
  parts: string[],
): [Path, Json][] {
  const changes: [Path, Json][] = [];
  const pushAll = (relative: Path) => {
    const value = getAt(source, relative);
    if (value === undefined) return;
    for (const target of targets) {
      changes.push([[...target, ...relative], structuredClone(value)]);
    }
  };

  if (parts.includes("wake_limits")) {
    const policy = object(source.policy);
    for (const target of targets) {
      if (sourceBase[0] === "groups" && target[0] === "groups") {
        if (source.keywords !== undefined) {
          changes.push([[...target, "keywords"], structuredClone(source.keywords)]);
        }
        if (policy.allow_at_all !== undefined) {
          changes.push([[...target, "policy", "allow_at_all"], Boolean(policy.allow_at_all)]);
        }
      }
      if (policy.max_sends_per_turn !== undefined) {
        changes.push([
          [...target, "policy", "max_sends_per_turn"],
          structuredClone(policy.max_sends_per_turn),
        ]);
      }
      if (policy.cross_private !== undefined) {
        changes.push([[...target, "policy", "cross_private"], Boolean(policy.cross_private)]);
      }
      if (Array.isArray(policy.cross_groups)) {
        const ownGroup = target[0] === "groups" ? String(target[1]) : "";
        const crossGroups = policy.cross_groups.map(String).filter((item) => item !== ownGroup);
        changes.push([[...target, "policy", "cross_groups"], crossGroups]);
      }
    }
  }

  if (parts.includes("prompts")) {
    pushAll(["persona_file"]);
    pushAll(["knowledge_files"]);
  }

  if (parts.includes("model")) {
    pushAll(["model"]);
  }

  if (parts.includes("idle_compaction")) {
    pushAll(["idle_compaction"]);
  }

  if (parts.includes("permissions")) {
    pushAll(["policy", "categories"]);
    pushAll(["policy", "tools"]);
    pushAll(["policy", "parameters"]);
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

/** 配置中的全部会话，带显示名和会话页地址。 */
export function conversationRefs(
  config: ObjectValue,
  groups: Map<string, GroupInfo>,
): ConversationRef[] {
  return conversationEntries(config).map(([base]) => {
    const kind = base[0] === "private" ? "private" : "group";
    const id = kind === "group" ? String(base[1]) : "";
    return {
      base,
      kind,
      id,
      label: kind === "private" ? "私聊" : groupTitle(id, groups),
      route: conversationRoute(kind, id),
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
      ...(getAt(conversation, ["idle_compaction", "model", "provider"]) === provider
        ? ["压缩模型"]
        : []),
    ];
    if (parts.length) uses.push({ ...ref, parts });
  }
  return uses;
}

/** 引用某个提示词或知识库文件的会话。 */
export function promptUsage(
  config: ObjectValue,
  filePath: string,
  groups: Map<string, GroupInfo>,
): ProviderUse[] {
  const uses: ProviderUse[] = [];
  for (const ref of conversationRefs(config, groups)) {
    const conversation = object(getAt(config, ref.base));
    const knowledge = Array.isArray(conversation.knowledge_files)
      ? conversation.knowledge_files.map(String)
      : [];
    const parts = [
      ...(conversation.persona_file === filePath ? ["人格"] : []),
      ...(knowledge.includes(filePath) ? ["知识库"] : []),
    ];
    if (parts.length) uses.push({ ...ref, parts });
  }
  return uses;
}
