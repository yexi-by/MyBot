import { useEffect } from "react";
import { CircleAlert, CopyPlus, Plus, Sparkles, Trash2 } from "lucide-react";
import { ConfigSection } from "../components/form";
import { Menu } from "../components/Menu";
import { Badge, Banner, Button, EmptyState, StatusDot } from "../components/primitives";
import { DetailHeader, MasterDetail, PageHeader } from "../components/shell";
import { change, useConfig } from "../lib/config";
import { formatNumber } from "../lib/format";
import { linkHandler, navigate, useLocation } from "../lib/navigation";
import { getAt, object } from "../lib/types";
import { confirm, prompt, toast, useMediaQuery } from "../lib/ui";
import {
  defaultProvider,
  providerApiSection,
  providerModelSection,
  providerRequestSection,
} from "../schema/fields";

const idPattern = /^[A-Za-z0-9_-]{1,64}$/;
const enc = encodeURIComponent;

function validateId(existing: Record<string, unknown>) {
  return (value: string) =>
    !idPattern.test(value)
      ? "只能使用字母、数字、短横线和下划线，最多 64 个字符"
      : Object.hasOwn(existing, value)
        ? "已有同名的模型连接"
        : "";
}

async function addProvider(existing: Record<string, unknown>, active: string) {
  const id = await prompt({
    title: "添加模型连接",
    message: "名称用于在配置中区分不同的接口，创建后不能修改。",
    label: "连接名称",
    placeholder: "例如 openai 或 deepseek",
    confirmLabel: "添加",
    validate: validateId(existing),
  });
  if (!id) return;
  change(["providers", id], defaultProvider());
  if (!active) change(["active_provider"], id);
  void navigate("/models/" + enc(id));
}

function ProviderDetail({ id }: { id: string }) {
  const state = useConfig();
  const providers = object(state.draft.providers);
  const active = String(state.draft.active_provider || "");
  const base = ["providers", id];
  const raw = getAt(state.draft, base);
  if (raw === undefined)
    return (
      <EmptyState
        icon={CircleAlert}
        title="模型连接不存在"
        action={<Button onClick={() => void navigate("/models")}>返回列表</Button>}
      >
        “{id}”可能已被删除或重命名。
      </EmptyState>
    );
  const provider = object(raw);
  const isActive = active === id;
  const context = typeof provider.max_context_tokens === "number" ? provider.max_context_tokens : 0;
  return (
    <div className="detail">
      <DetailHeader
        back={{ to: "/models", label: "全部模型连接" }}
        title={id}
        badges={isActive && <Badge tone="accent">当前使用</Badge>}
        subtitle={String(provider.model || "未填写模型名称")}
        actions={
          <>
            {!isActive && (
              <Button variant="primary" onClick={() => change(["active_provider"], id)}>
                设为当前使用
              </Button>
            )}
            <Menu
              items={[
                {
                  label: "复制为新连接",
                  icon: CopyPlus,
                  onSelect: async () => {
                    const next = await prompt({
                      title: "复制模型连接",
                      message: "复制当前连接的全部设置，包括 API Key。",
                      label: "新连接名称",
                      initial: id + "-copy",
                      confirmLabel: "复制",
                      validate: validateId(providers),
                    });
                    if (!next) return;
                    change(["providers", next], structuredClone(provider));
                    void navigate("/models/" + enc(next));
                  },
                },
                {
                  label: "删除",
                  icon: Trash2,
                  danger: true,
                  onSelect: async () => {
                    const accepted = await confirm({
                      title: "删除模型连接 " + id + "？",
                      message: isActive
                        ? "这是当前使用的连接。删除后 Agent 将不再回复，直到选择其他连接。"
                        : "连接的地址、密钥和模型设置会一并删除。",
                      confirmLabel: "删除",
                      danger: true,
                    });
                    if (!accepted) return;
                    if (isActive) change(["active_provider"], "");
                    change(base, undefined);
                    void navigate("/models", { replace: true });
                    toast("已删除模型连接 " + id);
                  },
                },
              ]}
            />
          </>
        }
      />
      {!isActive && !active && (
        <Banner tone="danger" icon={CircleAlert}>
          还没有选择当前使用的模型连接，Agent 不会回复。
        </Banner>
      )}
      {isActive && !provider.model && (
        <Banner tone="danger" icon={CircleAlert}>
          还没有填写模型名称，Agent 不会回复。
        </Banner>
      )}
      <ConfigSection def={providerApiSection} base={base} />
      <ConfigSection
        def={providerModelSection}
        base={base}
        helpFor={{
          max_context_tokens: context
            ? "模型的总上下文长度，不是回复长度上限。用量达到 " +
              formatNumber((context * 80) / 100) +
              " token 时自动压缩到约 " +
              formatNumber((context * 50) / 100) +
              " token。"
            : undefined,
        }}
      />
      <ConfigSection def={providerRequestSection} base={base} />
    </div>
  );
}

export function ModelsPage({ id }: { id: string }) {
  const state = useConfig();
  const location = useLocation();
  const wide = useMediaQuery("(min-width: 768px)");
  const providers = object(state.draft.providers);
  const ids = Object.keys(providers);
  const active = String(state.draft.active_provider || "");
  // 宽屏下未指定时直接显示当前使用的连接。
  const shown = id || (wide ? (ids.includes(active) ? active : (ids[0] ?? "")) : "");
  useEffect(() => {
    if (new URL(location, window.location.origin).searchParams.get("add") === "1") {
      void navigate("/models", { replace: true });
      void addProvider(providers, active);
    }
  }, [location]);
  const add = () => void addProvider(providers, active);
  return (
    <div className="page">
      <PageHeader
        title="模型"
        description="Agent 通过 OpenAI 兼容接口调用模型"
        actions={
          ids.length > 0 && (
            <Button icon={Plus} onClick={add}>
              添加模型连接
            </Button>
          )
        }
      />
      {!ids.length ? (
        <EmptyState
          icon={Sparkles}
          title="还没有模型连接"
          action={
            <Button variant="primary" icon={Plus} onClick={add}>
              添加模型连接
            </Button>
          }
        >
          添加一个 OpenAI 兼容接口，并设为当前使用后，Agent 才会回复消息。
        </EmptyState>
      ) : (
        <MasterDetail
          label="模型连接"
          hasDetail={!!id}
          list={
            <ul className="md-items">
              {ids.map((name) => {
                const provider = object(providers[name]);
                const to = "/models/" + enc(name);
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
                        <span className="md-item-sub">{String(provider.model || "未填写模型")}</span>
                      </span>
                      {active === name && <StatusDot tone="success" label="当前使用" />}
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
