import { useEffect, useRef, useState } from "react";
import { CircleAlert, CopyPlus, Plus, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { ConfigSection } from "../components/form";
import { Menu } from "../components/Menu";
import { Badge, Button, EmptyState } from "../components/primitives";
import { DetailHeader, MasterDetail, PageHeader } from "../components/shell";
import { ApiError, errorMessage, get } from "../lib/api";
import { change, useConfig } from "../lib/config";
import { formatNumber } from "../lib/format";
import { providerUsage } from "../lib/models";
import type { ProviderUse } from "../lib/models";
import { linkHandler, navigate, useLocation } from "../lib/navigation";
import { useGroupNames } from "../lib/resource";
import { getAt, object } from "../lib/types";
import type { Json } from "../lib/types";
import { confirm, prompt, toast, useMediaQuery } from "../lib/ui";
import { defaultProvider, providerApiSection, providerRequestSection } from "../schema/fields";

const idPattern = /^[A-Za-z0-9_-]{1,64}$/;
const enc = encodeURIComponent;

function validateId(existing: Record<string, unknown>) {
  return (value: string) =>
    !idPattern.test(value)
      ? "只能使用字母、数字、短横线和下划线，最多 64 个字符"
      : Object.hasOwn(existing, value)
        ? "已有同名的服务商"
        : "";
}

async function addProvider(existing: Record<string, unknown>) {
  const id = await prompt({
    title: "添加服务商",
    message: "名称用于在会话中选择服务商，创建后不能修改。",
    label: "服务商名称",
    placeholder: "例如 openai 或 deepseek",
    confirmLabel: "添加",
    validate: validateId(existing),
  });
  if (!id) return;
  change(["providers", id], defaultProvider());
  void navigate("/providers/" + enc(id));
}

/** 接口地址的主机名，用作列表副标题。 */
function host(url: Json | undefined) {
  const text = typeof url === "string" ? url : "";
  try {
    return new URL(text).host;
  } catch {
    return text;
  }
}

/** 读取服务商的模型列表，顺带确认地址和密钥可用；结果只在页面上显示。 */
function ModelCatalog({ id }: { id: string }) {
  const saved = useConfig().base;
  const exists = getAt(saved, ["providers", id]) !== undefined;
  const savedKey = JSON.stringify(
    ["protocol", "base_url", "api_key", "proxy", "timeout"].map((key) => getAt(saved, ["providers", id, key])),
  );
  const [models, setModels] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const ticket = useRef(0);
  const load = async (refresh: boolean) => {
    const current = ++ticket.current;
    setLoading(true);
    try {
      const result = await get<{ models: string[] }>(
        "/api/providers/" + enc(id) + "/models" + (refresh ? "?refresh=true" : ""),
      );
      if (current !== ticket.current) return;
      setModels([...new Set(result.models)].sort((a, b) => a.localeCompare(b)));
      setError("");
    } catch (failure) {
      if (current !== ticket.current) return;
      setModels(null);
      setError(
        failure instanceof ApiError && failure.status === 404
          ? "服务商保存后才能读取模型列表"
          : "无法读取模型列表：" + errorMessage(failure),
      );
    } finally {
      if (current === ticket.current) setLoading(false);
    }
  };
  // 连接设置保存后跳过服务端缓存重新读取。
  const loaded = useRef(false);
  useEffect(() => {
    if (exists) void load(loaded.current);
    loaded.current = true;
    return () => {
      ticket.current++;
    };
  }, [savedKey, exists]);
  return (
    <section className="card">
      <header className="card-header">
        <h2 className="grow">可用模型</h2>
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} disabled={!exists} onClick={() => void load(true)}>
          重新读取
        </Button>
      </header>
      <div className="card-body stack">
        {error ? (
          <p className="field-status is-error">{error}</p>
        ) : models === null ? (
          <p className="muted small">{loading ? "正在读取模型列表…" : "服务商保存后读取模型列表"}</p>
        ) : models.length ? (
          <>
            <p className="muted small">
              接口返回 {formatNumber(models.length)} 个模型，可在会话的「模型」页中选择。
            </p>
            <div className="chips catalog-chips">
              {models.map((model) => (
                <span className="chip mono" key={model} title={model}>
                  {model}
                </span>
              ))}
            </div>
          </>
        ) : (
          <p className="muted small">接口没有返回模型列表；在会话中可以直接输入模型名称。</p>
        )}
      </div>
    </section>
  );
}

function UsageCard({ uses }: { uses: ProviderUse[] }) {
  return (
    <section className="card">
      <header className="card-header">
        <h2 className="grow">使用中的会话</h2>
        <span className="muted small num">{uses.length} 个</span>
      </header>
      {uses.length ? (
        <dl className="info-list">
          {uses.map((use) => (
            <div key={use.base.join(":")}>
              <dt>
                <a href={use.route} onClick={linkHandler(use.route)}>
                  {use.label}
                </a>
              </dt>
              <dd>{use.parts.join("、")}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="card-body muted small">还没有会话使用这个服务商。在会话的「模型」页中选择它。</p>
      )}
    </section>
  );
}

function ProviderDetail({ id }: { id: string }) {
  const state = useConfig();
  const groups = useGroupNames();
  const providers = object(state.draft.providers);
  const base = ["providers", id];
  const raw = getAt(state.draft, base);
  if (raw === undefined)
    return (
      <EmptyState
        icon={CircleAlert}
        title="服务商不存在"
        action={<Button onClick={() => void navigate("/providers")}>返回列表</Button>}
      >
        “{id}”可能已被删除或重命名。
      </EmptyState>
    );
  const provider = object(raw);
  const uses = providerUsage(state.draft, id, groups);
  return (
    <div className="detail">
      <DetailHeader
        back={{ to: "/providers", label: "全部服务商" }}
        title={id}
        badges={uses.length > 0 && <Badge tone="accent">{uses.length} 个会话在用</Badge>}
        subtitle={host(provider.base_url) || "未填写接口地址"}
        actions={
          <Menu
            items={[
              {
                label: "复制为新服务商",
                icon: CopyPlus,
                onSelect: async () => {
                  const next = await prompt({
                    title: "复制服务商",
                    message: "复制当前服务商的全部设置，包括 API Key。",
                    label: "新服务商名称",
                    initial: id + "-copy",
                    confirmLabel: "复制",
                    validate: validateId(providers),
                  });
                  if (!next) return;
                  change(["providers", next], structuredClone(provider));
                  void navigate("/providers/" + enc(next));
                },
              },
              {
                label: "删除",
                icon: Trash2,
                danger: true,
                onSelect: async () => {
                  if (uses.length) {
                    toast("还有 " + uses.length + " 个会话在用这个服务商，请先在这些会话中改选其他服务商", "danger");
                    return;
                  }
                  const accepted = await confirm({
                    title: "删除服务商 " + id + "？",
                    message: "服务商的地址、密钥和请求设置会一并删除。",
                    confirmLabel: "删除",
                    danger: true,
                  });
                  if (!accepted) return;
                  change(base, undefined);
                  void navigate("/providers", { replace: true });
                  toast("已删除服务商 " + id);
                },
              },
            ]}
          />
        }
      />
      <ConfigSection def={providerApiSection} base={base} />
      <ConfigSection def={providerRequestSection} base={base} />
      <ModelCatalog id={id} />
      <UsageCard uses={uses} />
    </div>
  );
}

export function ProvidersPage({ id }: { id: string }) {
  const state = useConfig();
  const groups = useGroupNames();
  const location = useLocation();
  const wide = useMediaQuery("(min-width: 768px)");
  const providers = object(state.draft.providers);
  const ids = Object.keys(providers);
  // 宽屏下未指定时直接显示第一个服务商。
  const shown = id || (wide ? (ids[0] ?? "") : "");
  useEffect(() => {
    if (new URL(location, window.location.origin).searchParams.get("add") === "1") {
      void navigate("/providers", { replace: true });
      void addProvider(providers);
    }
  }, [location]);
  const add = () => void addProvider(providers);
  return (
    <div className="page">
      <PageHeader
        title="服务商"
        description="服务商只负责连接；每个会话在「会话」页选择自己的会话模型和压缩模型。"
        actions={
          ids.length > 0 && (
            <Button icon={Plus} onClick={add}>
              添加服务商
            </Button>
          )
        }
      />
      {!ids.length ? (
        <EmptyState
          icon={Sparkles}
          title="还没有服务商"
          action={
            <Button variant="primary" icon={Plus} onClick={add}>
              添加服务商
            </Button>
          }
        >
          添加一个 OpenAI 兼容接口，再到会话中选择模型，Agent 才会回复消息。
        </EmptyState>
      ) : (
        <MasterDetail
          label="服务商"
          hasDetail={!!id}
          list={
            <ul className="md-items">
              {ids.map((name) => {
                const provider = object(providers[name]);
                const count = providerUsage(state.draft, name, groups).length;
                const to = "/providers/" + enc(name);
                return (
                  <li key={name}>
                    <a
                      href={to}
                      className={"md-item" + (shown === name ? " active" : "")}
                      aria-current={shown === name ? "page" : undefined}
                      onClick={linkHandler(to)}
                    >
                      <Sparkles size={16} className="md-item-icon" aria-hidden="true" />
                      <span className="md-item-text">
                        <span className="md-item-title">{name}</span>
                        <span className="md-item-sub">{host(provider.base_url) || "未填写接口地址"}</span>
                      </span>
                      {count > 0 && (
                        <span className="muted small num" title={count + " 个会话在用"}>
                          {count}
                        </span>
                      )}
                    </a>
                  </li>
                );
              })}
            </ul>
          }
        >
          {shown ? <ProviderDetail key={shown} id={shown} /> : null}
        </MasterDetail>
      )}
    </div>
  );
}
