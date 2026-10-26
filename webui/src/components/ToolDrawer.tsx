import { useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { change, useConfig } from "../lib/config";
import { accessScope } from "../lib/conversations";
import { toolScopes } from "../lib/labels";
import { linkHandler } from "../lib/navigation";
import { evaluate, overrideOf } from "../lib/permissions";
import type { Override } from "../lib/permissions";
import { useGroupNames } from "../lib/resource";
import { getAt, object } from "../lib/types";
import type { Json, ObjectValue, Path, Tool } from "../lib/types";
import { fieldId } from "../schema/fields";
import { Combobox } from "./Combobox";
import { Dialog } from "./Dialog";
import { FieldShell, NumberInput, TagInput } from "./form";
import { Badge, Button, IconButton, Segmented, StatusDot } from "./primitives";
import { CopyButton } from "./shell";

const typeNames: Record<string, string> = {
  string: "文本",
  integer: "整数",
  number: "数字",
  boolean: "布尔",
  object: "对象",
  array: "列表",
  null: "空",
};
/** 解析 pydantic 生成的本地引用（#/$defs/…）。 */
function resolve(schema: ObjectValue, root: ObjectValue): ObjectValue {
  const ref = schema.$ref;
  return typeof ref === "string" && ref.startsWith("#/$defs/") ? object(object(root.$defs)[ref.slice(8)]) : schema;
}

function variantsOf(schema: ObjectValue) {
  return [schema.anyOf, schema.oneOf].find(Array.isArray);
}

function choicesOf(schema: ObjectValue): Json[] | undefined {
  return schema.const !== undefined ? [schema.const] : Array.isArray(schema.enum) ? schema.enum : undefined;
}

function typeLabel(raw: ObjectValue, root: ObjectValue): string {
  const schema = resolve(raw, root);
  if (schema.const !== undefined) return "固定值";
  if (Array.isArray(schema.enum)) return "可选值";
  // 可省略的参数常写成“类型或 null”，只展示实际类型。
  const variants = variantsOf(schema);
  if (variants)
    return [...new Set(variants.map((item) => typeLabel(object(item), root)))].filter((item) => item !== "空").join(" 或 ") || "空";
  const type = schema.type;
  if (Array.isArray(type))
    return type.filter((item) => item !== "null").map((item) => typeNames[String(item)] ?? String(item)).join(" 或 ") || "空";
  if (type === "array") {
    const items = object(schema.items);
    return Object.keys(items).length ? typeLabel(items, root) + "列表" : "列表";
  }
  return typeof type === "string" ? (typeNames[type] ?? type) : "任意";
}

/** 对象或对象联合（如消息片段）合并为一个对象：属性取并集，各变体都必填才算必填，常量合并为可选值。 */
function objectShape(raw: ObjectValue, root: ObjectValue): ObjectValue | null {
  const schema = resolve(raw, root);
  const variants = variantsOf(schema);
  const objects = (variants ? variants.map((item) => resolve(object(item), root)) : [schema]).filter(
    (item) => item.type === "object" && Object.keys(object(item.properties)).length > 0,
  );
  if (objects.length <= 1) return objects[0] ?? null;
  const properties: ObjectValue = {};
  for (const variant of objects)
    for (const [name, value] of Object.entries(object(variant.properties))) {
      const property = resolve(object(value), root);
      const choices = choicesOf(property);
      const existing = properties[name] === undefined ? undefined : object(properties[name]);
      if (existing && !(choices && Array.isArray(existing.enum))) continue;
      const merged: ObjectValue = { ...(existing ?? property) };
      if (choices) {
        merged.enum = [...new Set([...(existing && Array.isArray(existing.enum) ? existing.enum : []), ...choices])];
        delete merged.const;
      }
      properties[name] = merged;
    }
  const required = objects
    .map((item) => (Array.isArray(item.required) ? item.required.map(String) : []))
    .reduce((all, names) => all.filter((name) => names.includes(name)));
  return { type: "object", properties, required };
}

interface ParameterRow {
  path: string;
  type: string;
  required: boolean;
  description: string;
  values: Json[];
  depth: number;
}
/** 由 JSON Schema 展开参数表；参数限制的路径与后端一致：点号分隔，列表元素用 *。 */
function parameterRows(shape: ObjectValue, root: ObjectValue, prefix = "", depth = 0): ParameterRow[] {
  const required = Array.isArray(shape.required) ? shape.required.map(String) : [];
  const rows: ParameterRow[] = [];
  for (const [name, raw] of Object.entries(object(shape.properties))) {
    const property = resolve(object(raw), root);
    const path = prefix ? prefix + "." + name : name;
    rows.push({
      path,
      type: typeLabel(property, root),
      required: required.includes(name),
      description: typeof property.description === "string" ? property.description : "",
      values: choicesOf(property) ?? [],
      depth,
    });
    if (depth >= 2) continue;
    const list = property.type === "array";
    const nested = objectShape(list ? object(property.items) : property, root);
    if (nested) rows.push(...parameterRows(nested, root, list ? path + ".*" : path, depth + 1));
  }
  return rows;
}

const ruleKeys = ["minimum", "maximum", "max_length", "allowed"] as const;

function ParameterRules({ tool, policyPath, rows }: { tool: Tool; policyPath: Path; rows: ParameterRow[] }) {
  const state = useConfig();
  const rulesPath = [...policyPath, "parameters", tool.name];
  const rules = object(getAt(state.draft, rulesPath));
  const [pending, setPending] = useState("");
  const candidates = rows.map((row) => row.path).filter((path) => !Object.hasOwn(rules, path));
  const example = rows.find((row) => row.path.includes(".*."))?.path;
  const add = () => {
    const path = pending.trim();
    if (!path || Object.hasOwn(rules, path)) return;
    change([...rulesPath, path], {});
    setPending("");
  };
  const update = (path: string, key: (typeof ruleKeys)[number], value: Json) => {
    const empty = value === null || (Array.isArray(value) && value.length === 0);
    // 清空的条件直接删除键，不写入 null。
    change([...rulesPath, path, key], empty ? undefined : value);
  };
  const remove = (path: string) => {
    const next = { ...rules };
    delete next[path];
    change(rulesPath, Object.keys(next).length ? next : undefined);
  };
  return (
    <section className="drawer-section">
      <h3>参数限制</h3>
      <p className="muted small">
        调用时参数必须满足这里的条件，否则会被拒绝并告知模型。
        {example && "列表元素用 * 表示，例如 " + example + "。"}
      </p>
      {Object.entries(rules).map(([path, raw]) => {
        const rule = object(raw);
        const row = rows.find((item) => item.path === path);
        const issue = state.issues.find(
          (item) => item.path.slice(0, rulesPath.length + 1).join("\u0000") === [...rulesPath, path].join("\u0000"),
        );
        const id = (key: string) => fieldId([...rulesPath, path, key]);
        return (
          <div className="rule" key={path}>
            <div className="rule-head">
              <code>{path}</code>
              {row && <span className="muted tiny">{row.type}</span>}
              <span className="grow" />
              <IconButton icon={Trash2} size="sm" tone="danger" label={"删除 " + path + " 的限制"} onClick={() => remove(path)} />
            </div>
            <div className="rule-grid">
              <FieldShell id={id("minimum")} label="最小值">
                <NumberInput
                  id={id("minimum")}
                  value={rule.minimum}
                  nullable
                  placeholder="不限"
                  onChange={(value) => update(path, "minimum", value)}
                />
              </FieldShell>
              <FieldShell id={id("maximum")} label="最大值">
                <NumberInput
                  id={id("maximum")}
                  value={rule.maximum}
                  nullable
                  placeholder="不限"
                  onChange={(value) => update(path, "maximum", value)}
                />
              </FieldShell>
              <FieldShell id={id("max_length")} label="最大长度">
                <NumberInput
                  id={id("max_length")}
                  value={rule.max_length}
                  nullable
                  placeholder="不限"
                  unit="项/字"
                  onChange={(value) => update(path, "max_length", value)}
                />
              </FieldShell>
              <FieldShell id={id("allowed")} label="只允许这些值">
                <TagInput
                  id={id("allowed")}
                  value={rule.allowed}
                  typed
                  placeholder="输入后按回车；数字和 true/false 按原类型保存"
                  suggestions={(row?.values ?? []).map((value) => ({
                    value: typeof value === "string" ? value : JSON.stringify(value),
                    label: typeof value === "string" ? value : JSON.stringify(value),
                  }))}
                  onChange={(value) => update(path, "allowed", value)}
                />
              </FieldShell>
            </div>
            {issue && (
              <p className="field-error" role="alert">
                {issue.message}
              </p>
            )}
          </div>
        );
      })}
      <div className="rule-add">
        <Combobox
          label="要限制的参数"
          value={pending}
          editable
          placeholder="选择参数或输入路径"
          listLabel="工具参数"
          emptyText="没有可选参数，可直接输入路径"
          freeTextHint="将按输入的路径添加"
          options={candidates.map((path) => ({ value: path, label: path }))}
          onChange={setPending}
        />
        <Button icon={Plus} onClick={add} disabled={!pending.trim() || Object.hasOwn(rules, pending.trim())}>
          添加限制
        </Button>
      </div>
    </section>
  );
}

const overrides: { value: Override; label: string }[] = [
  { value: "inherit", label: "跟随分类" },
  { value: "allow", label: "允许" },
  { value: "deny", label: "禁止" },
];

/** 工具详情抽屉：说明、参数、在当前会话中的权限与参数限制。 */
export function ToolDrawer({
  tool,
  categoryLabel,
  onClose,
  context,
  openIn,
  catalog,
}: {
  tool: Tool | null;
  categoryLabel: string;
  onClose: () => void;
  context?: { base: Path; kind: "group" | "private" };
  openIn?: string[];
  catalog: Map<string, Tool>;
}) {
  const state = useConfig();
  const groups = useGroupNames();
  const rows = useMemo(() => (tool ? parameterRows(tool.parameters, tool.parameters) : []), [tool]);
  const conversation = context ? object(getAt(state.draft, context.base)) : {};
  const policyPath = context ? [...context.base, "policy"] : [];
  const access = tool && context ? evaluate(state.draft, conversation, context.kind, tool, catalog) : null;
  return (
    <Dialog
      open={!!tool}
      variant="drawer"
      size="lg"
      title={tool?.title ?? ""}
      description={
        tool && (
          <span className="row">
            <code>{tool.name}</code>
            <CopyButton text={tool.name} label="复制工具名" />
          </span>
        )
      }
      onClose={onClose}
      initialFocus={false}
    >
      {tool && (
        <>
          <div className="row wrap">
            <Badge>{categoryLabel}</Badge>
            <Badge>{toolScopes[tool.scope]}</Badge>
            {tool.effects ? <Badge tone="warning">会执行操作</Badge> : <Badge tone="success">只读</Badge>}
          </div>
          {context && access && (
            <section className="drawer-section">
              <h3>在此会话中</h3>
              <div className="row wrap">
                <Segmented
                  label="工具权限"
                  value={overrideOf(object(conversation.policy), tool.name)}
                  options={overrides}
                  onChange={(value) =>
                    change([...policyPath, "tools", tool.name], value === "inherit" ? undefined : value === "allow")
                  }
                />
                <span className={"perm-effect" + (access.allowed ? " on" : "")}>
                  <StatusDot tone={access.allowed ? "success" : "neutral"} />
                  {access.allowed ? "可以调用" : access.reason}
                </span>
              </div>
            </section>
          )}
          {context && <ParameterRules tool={tool} policyPath={policyPath} rows={rows} />}
          {openIn && (
            <section className="drawer-section">
              <h3>已开放于</h3>
              {openIn.length ? (
                <ul className="chips">
                  {openIn.map((key) => {
                    const scope = accessScope(key, groups);
                    const to = scope.route + "?tab=permissions";
                    return (
                      <li key={key}>
                        <a className="chip" href={to} onClick={linkHandler(to, onClose)}>
                          {scope.label}
                        </a>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="muted small">还没有会话可以调用这个工具。可在会话的“能力权限”中开放。</p>
              )}
            </section>
          )}
          <section className="drawer-section">
            <h3>参数</h3>
            {rows.length ? (
              <div className="table-wrap">
                <table className="table param-table">
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th>类型</th>
                      <th>说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.path}>
                        <td className="nowrap">
                          <code style={{ paddingLeft: row.depth * 14 }}>{row.path}</code>
                          {row.required && <span className="required">必填</span>}
                        </td>
                        <td className="nowrap secondary">{row.type}</td>
                        <td className="secondary param-description">
                          {row.description && <span>{row.description}</span>}
                          {row.values.length > 0 && (
                            <span className="param-values">
                              <span className="muted">可选</span>
                              {row.values.map((value) => (
                                <code key={JSON.stringify(value)}>
                                  {typeof value === "string" ? value : JSON.stringify(value)}
                                </code>
                              ))}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted small">这个工具没有参数。</p>
            )}
          </section>
          <section className="drawer-section">
            <h3>模型看到的说明</h3>
            <p className="tool-description">{tool.description}</p>
          </section>
        </>
      )}
    </Dialog>
  );
}
