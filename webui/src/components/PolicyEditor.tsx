import { useEffect, useState } from "react";
import { request, errorMessage } from "../lib/api";
import { change, useConfig } from "../lib/config";
import { getAt, object } from "../lib/types";
import type {
  Json,
  ObjectValue,
  Path,
  Tool,
  ToolsEnvelope,
} from "../lib/types";
import { ask, notify } from "../lib/ui";
import { FieldGrid, Section } from "./Forms";
import { Dialog } from "./Dialog";
import { Combobox } from "./Combobox";
import { parameterSection, policySection } from "../schema/fields";

const categoryLabels: Record<string, string> = {
  "messages.read": "消息读取",
  "messages.send": "发送消息",
  "messages.manage": "消息管理",
  "members.read": "成员资料",
  "groups.manage": "群管理",
  "groups.read": "群信息",
  "group.manage": "群管理",
  "files.read": "文件读取",
  "files.write": "文件操作",
  "files.manage": "文件管理",
  "account.read": "账号信息",
  "account.manage": "账号操作",
  "contacts.read": "联系人信息",
  "contacts.manage": "联系人管理",
  "albums.read": "群相册",
  "media.read": "媒体读取",
  "notices.read": "群公告读取",
  "notices.manage": "群公告管理",
  "utilities.read": "辅助查询",
  sandbox: "代码沙箱",
  "sandbox.execute": "代码执行",
  "sandbox.files": "沙箱文件",
  mcp: "外部 MCP 工具",
  harness: "运行内置",
};
export function PolicyEditor({
  kind,
  peerId,
  base,
  filter,
}: {
  kind: "group" | "private";
  peerId: string;
  base: Path;
  filter: string;
}) {
  const state = useConfig(),
    policy = object(getAt(state.draft, base)),
    categories = object(policy.categories),
    overrides = object(policy.tools),
    parameters = object(policy.parameters);
  const [catalogue, setCatalogue] = useState<ToolsEnvelope>({
      tools: [],
      categories: [],
    }),
    [error, setError] = useState(""),
    [tab, setTab] = useState("all");
  const [editing, setEditing] = useState<{
    tool: Tool;
    name: string;
    rule: ObjectValue;
  } | null>(null);
  useEffect(() => {
    let alive = true;
    request<ToolsEnvelope>(
      "/api/tools?kind=" + kind + "&peer_id=" + encodeURIComponent(peerId),
    )
      .then((data) => {
        if (alive) {
          setCatalogue(data);
          setError("");
        }
      })
      .catch((reason) => alive && setError(errorMessage(reason)));
    return () => {
      alive = false;
    };
  }, [kind, peerId, state.sha]);
  const categoryNames = [
    ...new Set([...catalogue.categories, ...Object.keys(categories)]),
  ].sort();
  const tools = catalogue.tools.filter(
    (tool) =>
      (tab === "all" || tool.category === tab) &&
      (!filter ||
        (tool.name + tool.description + tool.category)
          .toLowerCase()
          .includes(filter.toLowerCase())),
  );
  const setRule = (toolName: string, name: string, value: Json | undefined) =>
    change([...base, "parameters", toolName, name], value);
  return (
    <>
      <Section definition={policySection} base={base} filter={filter} />
      <Section
        definition={{
          id: "capabilities",
          title: "能力分类",
          description: "分类开关决定默认权限；工具级设置可以单独覆盖。",
          fields: categoryNames.map((name) => ({
            key: name,
            label: categoryLabels[name] || name,
            description: "开放此分类中的已登记工具；实际执行仍检查参数边界。",
            type: "toggle",
          })),
        }}
        base={[...base, "categories"]}
        filter={filter}
      />
      <section className="sec" id="section-tool-policy">
        <header className="sec-head">
          <div>
            <h2>工具覆盖与参数限制</h2>
            <p>调用权交给 Agent；同一个群内不区分发言者身份。</p>
          </div>
          <span className="badge">{catalogue.tools.length} 个工具</span>
        </header>
        {error && <p className="notice bad">{error}</p>}
        <div className="catalogue-toolbar">
          <label>
            分类{" "}
            <Combobox
              label="工具分类"
              value={tab}
              onChange={setTab}
              options={[
                { value: "all", label: "全部分类" },
                ...categoryNames.map((name) => ({
                  value: name,
                  label: categoryLabels[name] || name,
                })),
              ]}
            />
          </label>
          <span className="muted">
            显示 {tools.length} 项 · 变更随配置自动写入
          </span>
        </div>
        <div className="tool-list">
          {tools.map((tool) => {
            const builtin = tool.name.startsWith("harness__");
            const configured = overrides[tool.name],
              allowed = builtin
                ? tool.allowed === true
                : configured === undefined
                  ? categories[tool.category] === true
                  : configured === true,
              constraints = object(parameters[tool.name]);
            return (
              <article className="tool-card" key={tool.name}>
                <div className="tool-card-top">
                  <div>
                    <code>{tool.name}</code>
                    <div className="tool-badges">
                      <span className={"badge " + (allowed ? "ok" : "")}>
                        {allowed ? "配置允许" : "配置禁止"}
                      </span>
                      <span className="badge">
                        {tool.effects ? "有副作用" : "读取"}
                      </span>
                      <span className="badge">{tool.scope}</span>
                    </div>
                  </div>
                  {builtin ? (
                    <span className="badge">运行内置</span>
                  ) : (
                    <label className="tool-override">
                      <span>工具覆盖</span>
                      <Combobox
                        label={tool.name + " 权限"}
                        value={
                          configured === undefined
                            ? "inherit"
                            : configured
                              ? "allow"
                              : "deny"
                        }
                        onChange={(value) =>
                          change(
                            [...base, "tools", tool.name],
                            value === "inherit" ? undefined : value === "allow",
                          )
                        }
                        options={[
                          { value: "inherit", label: "跟随分类" },
                          { value: "allow", label: "允许" },
                          { value: "deny", label: "禁止" },
                        ]}
                      />
                    </label>
                  )}
                </div>
                <p>{tool.description}</p>
                {tool.reason && (
                  <small className="field-hint">当前生效：{tool.reason}</small>
                )}
                <div className="constraint-list">
                  {Object.entries(constraints).map(([name, rule]) => (
                    <div className="constraint" key={name}>
                      <code>{name}</code>
                      <span>
                        {Object.entries(object(rule))
                          .filter(([, value]) => value !== null)
                          .map(
                            ([key, value]) =>
                              key + ": " + JSON.stringify(value),
                          )
                          .join(" · ") || "尚未设置限制"}
                      </span>
                      <button
                        className="small"
                        onClick={() =>
                          setEditing({ tool, name, rule: object(rule) })
                        }
                      >
                        编辑
                      </button>
                      <button
                        className="icon-button"
                        aria-label={"移除参数约束 " + name}
                        onClick={() => setRule(tool.name, name, undefined)}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
                <div className="tool-actions">
                  {!builtin && (
                    <button
                      className="small"
                      onClick={async () => {
                        const name = await ask(
                          "新增参数约束",
                          "输入工具参数路径；仅限制该参数，不涉及个人授权。",
                          {
                            input: "",
                            placeholder: "例如 duration",
                            confirm: "添加约束",
                          },
                        );
                        if (typeof name === "string" && name.trim())
                          setEditing({
                            tool,
                            name: name.trim(),
                            rule: {
                              minimum: null,
                              maximum: null,
                              max_length: null,
                              allowed: null,
                            },
                          });
                      }}
                    >
                      ＋ 参数约束
                    </button>
                  )}
                  <details>
                    <summary>参数定义</summary>
                    <pre>{JSON.stringify(tool.parameters, null, 2)}</pre>
                  </details>
                </div>
              </article>
            );
          })}
          {!tools.length && (
            <div className="empty">
              没有匹配工具。MCP 连接生效后，工具会加入此目录。
            </div>
          )}
        </div>
      </section>
      <Dialog
        open={!!editing}
        title="工具参数约束"
        onClose={() => setEditing(null)}
      >
        {editing && (
          <>
            <div className="dialog-body">
              <code>
                {editing.tool.name} · {editing.name}
              </code>
              <FieldGrid
                fields={parameterSection.fields}
                base={[...base, "parameters", editing.tool.name, editing.name]}
                values={editing.rule}
                onChange={(key, value) =>
                  setEditing({
                    ...editing,
                    rule: { ...editing.rule, [key]: value },
                  })
                }
              />
            </div>
            <div className="dialog-actions">
              <button onClick={() => setEditing(null)}>取消</button>
              <button
                className="primary"
                onClick={() => {
                  const { minimum, maximum } = editing.rule;
                  if (
                    typeof minimum === "number" &&
                    typeof maximum === "number" &&
                    minimum > maximum
                  ) {
                    notify("最小值不能大于最大值");
                    return;
                  }
                  setRule(editing.tool.name, editing.name, editing.rule);
                  setEditing(null);
                }}
              >
                应用约束
              </button>
            </div>
          </>
        )}
      </Dialog>
    </>
  );
}
