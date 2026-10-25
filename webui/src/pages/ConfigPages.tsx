import { useEffect, useState } from "react";
import { change, useConfig } from "../lib/config";
import { navigate } from "../lib/navigation";
import { object } from "../lib/types";
import { ask, notify } from "../lib/ui";
import { Section } from "../components/Forms";
import { PolicyEditor } from "../components/PolicyEditor";
import { ToolCoverage } from "../components/ToolCoverage";
import {
  activeProviderSection,
  conversationSection,
  defaultConversation,
  defaultMcp,
  defaultProvider,
  mcpSection,
  napcatSection,
  personaSection,
  providerSection,
  serverSection,
} from "../schema/fields";
export function ConnectionsPage({
  filter,
  query,
}: {
  filter: string;
  query: URLSearchParams;
}) {
  const state = useConfig(),
    providers = Object.keys(object(state.draft.providers)),
    selected = providers.includes(query.get("provider") || "")
      ? query.get("provider")!
      : providers[0];
  return (
    <>
      <Section definition={napcatSection} base={["napcat"]} filter={filter} />
      <Section definition={serverSection} base={["server"]} filter={filter} />
      <Section definition={activeProviderSection} filter={filter} />
      <div className="collection-head">
        <div>
          <h2>模型连接</h2>
          <p>协议、地址与模型名称明确配置。</p>
        </div>
        <button
          className="primary"
          onClick={async () => {
            const id = await ask(
              "新增模型连接",
              "使用英文、数字、短横线或下划线作为稳定标识。",
              { input: "", placeholder: "例如 primary", confirm: "创建连接" },
            );
            if (typeof id !== "string") return;
            if (
              !/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(id) ||
              ["constructor", "prototype"].includes(id)
            ) {
              notify("连接标识格式无效");
              return;
            }
            if (providers.includes(id)) {
              notify("连接标识已存在");
              return;
            }
            change(["providers", id], defaultProvider());
            if (!state.draft.active_provider) change(["active_provider"], id);
            navigate("/connections?provider=" + encodeURIComponent(id));
          }}
        >
          ＋ 新建 Provider
        </button>
      </div>
      {providers.length ? (
        <>
          <div className="entity-tabs">
            {providers.map((id) => (
              <button
                key={id}
                className={id === selected ? "active" : ""}
                onClick={() =>
                  navigate("/connections?provider=" + encodeURIComponent(id))
                }
              >
                {id}
                {state.draft.active_provider === id && (
                  <span className="dot ok" />
                )}
              </button>
            ))}
          </div>
          {selected && (
            <>
              <Section
                definition={{
                  ...providerSection,
                  title: "Provider · " + selected,
                }}
                base={["providers", selected]}
                filter={filter}
              />
              <div className="danger-row">
                <span>删除连接将使依赖它的模型选择失效。</span>
                <button
                  className="danger"
                  onClick={async () => {
                    if (
                      (await ask(
                        "删除模型连接",
                        "将删除 " +
                          selected +
                          " 的地址、密钥与模型设置；若正在使用，当前模型选择也会清空。",
                        { danger: true, confirm: "删除连接" },
                      )) !== true
                    )
                      return;
                    change(["providers", selected], undefined);
                    if (state.draft.active_provider === selected)
                      change(["active_provider"], "");
                    navigate("/connections");
                  }}
                >
                  删除 {selected}
                </button>
              </div>
            </>
          )}
        </>
      ) : (
        <div className="empty">
          还没有模型连接。新建 Provider 后选择当前模型。
        </div>
      )}
    </>
  );
}
export function GroupsPage({
  filter,
  query,
}: {
  filter: string;
  query: URLSearchParams;
}) {
  const state = useConfig(),
    groups = object(state.draft.groups),
    ids = Object.keys(groups),
    selected = query.get("id") || ids[0];
  return (
    <>
      <div className="collection-head">
        <p>每个群独立配置能力。群内所有发言者使用相同策略。</p>
        <button
          className="primary"
          onClick={async () => {
            const id = await ask(
              "添加群配置",
              "填写群号。只配置群，不建立群员名单或个人权限。",
              { input: "", placeholder: "群号", confirm: "添加群" },
            );
            if (typeof id !== "string") return;
            if (!/^\d{1,20}$/.test(id)) {
              notify("请输入有效群号");
              return;
            }
            if (ids.includes(id)) {
              navigate("/groups?id=" + id);
              return;
            }
            change(["groups", id], defaultConversation());
            navigate("/groups?id=" + id);
          }}
        >
          ＋ 添加群
        </button>
      </div>
      <div className="entity-tabs">
        {ids.map((id) => (
          <button
            key={id}
            className={id === selected ? "active" : ""}
            onClick={() => navigate("/groups?id=" + encodeURIComponent(id))}
          >
            <span
              className={"dot " + (object(groups[id]).enabled ? "ok" : "")}
            />
            {id}
          </button>
        ))}
      </div>
      {selected && groups[selected] ? (
        <>
          <Section
            definition={{ ...conversationSection, title: "群 " + selected }}
            base={["groups", selected]}
            filter={filter}
          />
          <PolicyEditor
            key={selected}
            kind="group"
            peerId={selected}
            base={["groups", selected, "policy"]}
            filter={filter}
          />
          <div className="danger-row">
            <span>删除会清除该群的唤醒与权限配置。</span>
            <button
              className="danger"
              onClick={async () => {
                if (
                  (await ask(
                    "删除群配置",
                    "将删除群 " +
                      selected +
                      " 的全部配置。该群不再触发 Agent；共享工作区文件不会被删除。",
                    { danger: true, confirm: "删除群配置" },
                  )) !== true
                )
                  return;
                change(["groups", selected], undefined);
                navigate("/groups");
              }}
            >
              删除此群配置
            </button>
          </div>
        </>
      ) : (
        <div className="empty large">
          <span className="empty-mark">＋</span>
          <h2>从一个群开始</h2>
          <p>添加群号后，为该群中的 Agent 选择能力。</p>
        </div>
      )}
    </>
  );
}
export function PrivatePage({ filter }: { filter: string }) {
  return (
    <>
      <p className="notice">所有私聊使用同一策略；不按 QQ 号设置个人权限。</p>
      <Section
        definition={{ ...conversationSection, title: "统一私聊行为" }}
        base={["private"]}
        filter={filter}
      />
      <PolicyEditor
        kind="private"
        peerId=""
        base={["private", "policy"]}
        filter={filter}
      />
    </>
  );
}
export function ToolsPage({ filter }: { filter: string }) {
  const state = useConfig(),
    ids = Object.keys(object(state.draft.groups)),
    [selected, setSelected] = useState(ids[0] || "private"),
    privateScope = selected === "private";
  useEffect(() => {
    if (selected !== "private" && !ids.includes(selected))
      setSelected("private");
  }, [selected, ids.join("|")]);
  return (
    <>
      <ToolCoverage filter={filter} />
      <div className="scope-picker">
        <label>
          查看和编辑策略{" "}
          <select
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="private">统一私聊策略</option>
            {ids.map((id) => (
              <option key={id} value={id}>
                群 {id}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">目录只披露能力，调用时仍重新检查权限。</span>
      </div>
      <PolicyEditor
        key={selected}
        kind={privateScope ? "private" : "group"}
        peerId={privateScope ? "" : selected}
        base={
          privateScope ? ["private", "policy"] : ["groups", selected, "policy"]
        }
        filter={filter}
      />
    </>
  );
}
export function McpPage({
  filter,
  query,
}: {
  filter: string;
  query: URLSearchParams;
}) {
  const state = useConfig(),
    servers = object(state.draft.mcp),
    ids = Object.keys(servers),
    selected = query.get("id") || ids[0];
  return (
    <>
      <div className="collection-head">
        <p>外部工具和 NapCat 工具使用同一套披露与权限规则。</p>
        <button
          className="primary"
          onClick={async () => {
            const id = await ask(
              "新增 MCP 服务",
              "使用英文、数字、短横线或下划线命名服务。",
              { input: "", placeholder: "例如 search", confirm: "创建服务" },
            );
            if (typeof id !== "string") return;
            if (
              !/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(id) ||
              ["constructor", "prototype"].includes(id)
            ) {
              notify("服务名称格式无效");
              return;
            }
            if (ids.includes(id)) {
              notify("服务名称已存在");
              return;
            }
            change(["mcp", id], defaultMcp());
            navigate("/mcp?id=" + encodeURIComponent(id));
          }}
        >
          ＋ 新建 MCP
        </button>
      </div>
      <div className="entity-tabs">
        {ids.map((id) => (
          <button
            key={id}
            className={id === selected ? "active" : ""}
            onClick={() => navigate("/mcp?id=" + encodeURIComponent(id))}
          >
            <span
              className={"dot " + (object(servers[id]).enabled ? "ok" : "")}
            />
            {id}
          </button>
        ))}
      </div>
      {selected && servers[selected] ? (
        <>
          <Section
            definition={{ ...mcpSection, title: "MCP · " + selected }}
            base={["mcp", selected]}
            filter={filter}
          />
          <div className="danger-row">
            <span>
              删除服务后，其工具将不可用，群策略中的工具引用需同步调整。
            </span>
            <button
              className="danger"
              onClick={async () => {
                if (
                  (await ask(
                    "删除 MCP 服务",
                    "将删除 " +
                      selected +
                      " 的连接配置与密钥，相关工具会失效。群策略中保存的工具覆盖不会自动改写。",
                    { danger: true, confirm: "删除服务" },
                  )) !== true
                )
                  return;
                change(["mcp", selected], undefined);
                navigate("/mcp");
              }}
            >
              删除此服务
            </button>
          </div>
        </>
      ) : (
        <div className="empty">还没有外部 MCP 服务。</div>
      )}
    </>
  );
}
export function PersonaPage({ filter }: { filter: string }) {
  const state = useConfig(),
    persona = String(state.draft.persona_file || ""),
    knowledge = Array.isArray(state.draft.knowledge_files)
      ? state.draft.knowledge_files
      : [];
  return (
    <>
      <p className="notice">
        默认人格为耄耋。人格影响表达；安全规则、工具说明和群权限独立生效。
      </p>
      <Section definition={personaSection} filter={filter} />
      <section className="sec">
        <header className="sec-head">
          <div>
            <h2>编辑配置文件</h2>
            <p>仅编辑配置目录中的文本。Markdown 支持并排预览。</p>
          </div>
          <button onClick={() => navigate("/files")}>浏览文件</button>
        </header>
        <div className="file-links">
          {[persona, ...knowledge.map(String)]
            .filter(Boolean)
            .map((path, i) => (
              <button
                key={path + i}
                className="file-link"
                onClick={() =>
                  navigate("/files?path=" + encodeURIComponent(path))
                }
              >
                <span>{i === 0 ? "人格" : "知识"}</span>
                <code>{path}</code>
                <span>编辑 →</span>
              </button>
            ))}
        </div>
      </section>
    </>
  );
}
