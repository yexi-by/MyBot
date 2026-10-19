/** 声明式字段表：同一份定义驱动渲染、Ctrl K 搜索、写回回执文案和客户端预检。 */

import type { JsonObject, PathSeg } from "@/lib/types";

export type FieldKind =
  | "text"
  | "secret"
  | "textarea"
  | "number"
  | "switch"
  | "select"
  | "segmented"
  | "tags"
  | "numtags"
  | "ids"
  | "file"
  | "provider"
  | "model"
  | "keyvalue";

export type Option = readonly [value: string, label: string];

export interface FieldDef {
  /** 相对分节根的 TOML 键，可含点号表示嵌套，例如 model.provider。 */
  key: string;
  label: string;
  kind: FieldKind;
  desc?: string;
  placeholder?: string;
  unit?: string;
  /** 值为 0 时的含义，例如「不限」。 */
  zero?: string;
  /** 值为 -1 时的含义。 */
  neg?: string;
  bytes?: boolean;
  /** 大于等于。 */
  min?: number;
  /** 严格大于。 */
  gt?: number;
  /** 小于等于。 */
  max?: number;
  integer?: boolean;
  options?: readonly Option[];
  /** 在三列网格里横跨的列数。 */
  span?: 2 | 3;
  /** 只在三列网格里横跨两列，两列网格里退回一列（文件路径这类偏长但不必独占一行的值）。 */
  wide?: boolean;
  mono?: boolean;
  /** 清空即删除该键（对应后端 Optional 字段）。 */
  nullable?: boolean;
  /** 客户端预检：文本不能为空。 */
  required?: boolean;
  /** 列表项不能重复（忽略大小写）。 */
  unique?: boolean;
  /** model 字段对应的 provider 相对键。 */
  providerKey?: string;
  /** keyvalue / tags 的添加按钮文案。 */
  addLabel?: string;
  /** 联动写入：返回整份新配置。 */
  apply?: (config: JsonObject, value: unknown) => JsonObject;
  /** 返回禁用原因，false 表示可编辑。 */
  disabled?: (config: JsonObject) => string | false;
}

export interface OptionalDef {
  switchLabel: string;
  offText: string;
  enable: (config: JsonObject) => JsonObject;
  disable: (config: JsonObject) => JsonObject;
  disabled?: (config: JsonObject) => string | false;
}

export interface SectionDef {
  id: string;
  title: string;
  base: PathSeg[];
  /** 显示的 TOML 表头；缺省按 base 生成。 */
  table?: string;
  desc?: string;
  restart?: boolean;
  /** 宽屏下的列数；只有 2 或 4 个字段的分节用两列，避免末行空格子。 */
  cols?: 2;
  fields: FieldDef[];
  optional?: OptionalDef;
}

export function fieldPath(base: readonly PathSeg[], field: FieldDef): PathSeg[] {
  return [...base, ...field.key.split(".")];
}
