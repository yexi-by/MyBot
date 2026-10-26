import { parseScopeKey } from "./format";
import type { GroupInfo } from "./types";

/** 群显示名：NapCat 返回了群名就用群名，否则显示群号。 */
export function groupTitle(id: string, groups: Map<string, GroupInfo>): string {
  return groups.get(id)?.group_name || "群 " + id;
}

/** 运行时会话键（机器人:类型:对端）对应的显示名。 */
export function scopeLabel(key: string, groups: Map<string, GroupInfo>): string {
  const scope = parseScopeKey(key);
  if (!scope) return key;
  return scope.kind === "group" ? groupTitle(scope.peer, groups) : "私聊 · " + scope.peer;
}

/** /api/tools/access 的键（private 或 group:群号）对应的显示名与页面。 */
export function accessScope(key: string, groups: Map<string, GroupInfo>) {
  if (key === "private") return { label: "私聊", route: "/conversations/private" };
  const id = key.startsWith("group:") ? key.slice(6) : key;
  return {
    label: groupTitle(id, groups),
    route: "/conversations/group/" + encodeURIComponent(id),
  };
}

export function conversationRoute(kind: "group" | "private", id: string, permissions = false) {
  return (
    (kind === "private" ? "/conversations/private" : "/conversations/group/" + encodeURIComponent(id)) +
    (permissions ? "?tab=permissions" : "")
  );
}
