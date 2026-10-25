/** 字段定义的简写构造器。 */

import type { FieldDef, Option } from "./types";

type Extra = Partial<Omit<FieldDef, "key" | "label" | "kind">>;

export const text = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "text", ...extra });
export const secret = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "secret", nullable: true, ...extra });
export const area = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "textarea", ...extra });
export const num = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "number", ...extra });
export const int = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "number", integer: true, ...extra });
export const sw = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "switch", ...extra });
export const select = (key: string, label: string, options: readonly Option[], extra: Extra = {}): FieldDef => ({ key, label, kind: "select", options, ...extra });
export const seg = (key: string, label: string, options: readonly Option[], extra: Extra = {}): FieldDef => ({ key, label, kind: "segmented", options, ...extra });
export const tags = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "tags", ...extra });
export const numtags = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "numtags", ...extra });
export const ids = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "ids", ...extra });
export const file = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "file", mono: true, ...extra });
export const provider = (key: string, extra: Extra = {}): FieldDef => ({ key, label: "Provider", kind: "provider", desc: "引用「模型服务」里已保存的服务", ...extra });
export const model = (key: string, providerKey: string, extra: Extra = {}): FieldDef => ({ key, label: "模型名", kind: "model", providerKey, required: true, ...extra });
export const keyvalue = (key: string, label: string, extra: Extra = {}): FieldDef => ({ key, label, kind: "keyvalue", nullable: true, span: 3, ...extra });

export const plain = (values: readonly string[]): Option[] => values.map((value) => [value, value] as const);
