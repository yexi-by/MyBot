/**
 * 模型名输入：选中 provider 后拉取它的模型列表作为候选，可筛选、可手输、可刷新；
 * 拉取失败只降级成提示，不阻断手输。
 */

import { Check, RefreshCw } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import { api, ApiError, errorMessage } from "@/lib/api";
import { useConfig } from "@/lib/config";
import { deepEqual, getIn } from "@/lib/path";
import { cls } from "@/lib/pages";
import { Store, useStore } from "@/lib/store";
import type { Path } from "@/lib/types";

interface ModelList {
  status: "loading" | "ready" | "error";
  models: string[];
  error: string;
  /** 404：provider 只存在于未保存的草稿里。 */
  unsaved: boolean;
}

const cache = new Store<Record<string, ModelList>>({});

async function fetchModels(provider: string): Promise<void> {
  const current = cache.get()[provider];
  cache.set({ [provider]: { status: "loading", models: current?.models ?? [], error: "", unsaved: false } });
  try {
    const { models } = await api.listModels(provider);
    cache.set({ [provider]: { status: "ready", models: [...models].sort((a, b) => a.localeCompare(b)), error: "", unsaved: false } });
  } catch (error) {
    const unsaved = error instanceof ApiError && error.status === 404;
    cache.set({
      [provider]: { status: "error", models: current?.models ?? [], error: errorMessage(error, "拉取失败"), unsaved },
    });
  }
}

function highlight(text: string, query: string): ReactNode {
  if (!query) return text;
  const index = text.toLowerCase().indexOf(query.toLowerCase());
  if (index < 0) return text;
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + query.length)}</mark>
      {text.slice(index + query.length)}
    </>
  );
}

interface ModelComboProps {
  id: string;
  providerPath: Path;
  value: unknown;
  onChange: (value: string) => void;
  onOpenChange?: (open: boolean) => void;
  describedBy?: string;
  invalid?: boolean;
}

export function ModelCombo({ id, providerPath, value, onChange, onOpenChange, describedBy, invalid }: ModelComboProps) {
  const provider = useConfig((state) => {
    const raw = getIn(state.draft, providerPath);
    return typeof raw === "string" ? raw.trim() : "";
  });
  const providerSaved = useConfig((state) =>
    deepEqual(getIn(state.draft, ["llm", "providers", provider]), getIn(state.saved, ["llm", "providers", provider])),
  );
  const list = useStore(cache, (state) => (provider ? state[provider] : undefined));
  const text = typeof value === "string" ? value : "";
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(-1);
  const listId = useId();
  const root = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const setOpenState = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };

  // provider 切换或首次打开时拉取一次；之后走模块级缓存，手动刷新可强制重拉。
  useEffect(() => {
    if (open && provider && !cache.get()[provider]) void fetchModels(provider);
  }, [open, provider]);

  const options = useMemo(() => {
    const models = list?.models ?? [];
    const query = filter.trim().toLowerCase();
    return query ? models.filter((model) => model.toLowerCase().includes(query)) : models;
  }, [list, filter]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpenState(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (active < 0) return;
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const choose = (model: string) => {
    onChange(model);
    setOpenState(false);
    setFilter("");
    setActive(-1);
  };

  const openList = () => {
    if (open) return;
    setFilter("");
    setActive(Math.max(0, (list?.models ?? []).indexOf(text)));
    setOpenState(true);
  };

  let head: ReactNode = null;
  let empty: ReactNode = null;
  if (!provider) {
    empty = "先选择 Provider，才能拉取模型列表";
  } else if (!list || list.status === "loading") {
    head = <span>{provider} · 正在拉取模型列表…</span>;
  } else if (list.status === "error") {
    empty = list.unsaved || !providerSaved
      ? "Provider 尚未保存到 mybot.toml，写入后才能拉取模型列表；可以先手输模型名"
      : `${/失败/.test(list.error) ? "" : "拉取失败："}${list.error.replace(/[。.\s]+$/, "")}。可以直接手输模型名`;
  } else {
    head = (
      <>
        <span>{provider}</span>
        <span>{filter ? `${options.length} / ${list.models.length}` : `${list.models.length} 个模型`}</span>
      </>
    );
    if (list.models.length === 0) empty = "这个 Provider 没有返回模型；可以直接手输";
    else if (options.length === 0) empty = `没有包含「${filter}」的模型；回车直接使用手输的名字`;
  }

  return (
    <div className="ctl has-btn" ref={root}>
      <input
        id={id}
        className={cls("inp is-mono", invalid && "is-error")}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-describedby={describedBy}
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        value={text}
        placeholder={provider ? "选择或输入模型名" : "先选择 Provider"}
        onFocus={openList}
        onClick={openList}
        onChange={(event) => {
          onChange(event.target.value);
          setFilter(event.target.value);
          setActive(0);
          if (!open) setOpenState(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            if (!open) openList();
            else setActive((index) => Math.min(options.length - 1, index + 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((index) => Math.max(0, index - 1));
          } else if (event.key === "Enter") {
            if (open && active >= 0 && options[active]) {
              event.preventDefault();
              choose(options[active]);
            } else if (open) {
              event.preventDefault();
              setOpenState(false);
            }
          } else if (event.key === "Escape") {
            if (open) {
              event.preventDefault();
              event.stopPropagation();
              setOpenState(false);
            }
          } else if (event.key === "Tab") {
            setOpenState(false);
          }
        }}
      />
      <div className="ctl-btns">
        <button
          type="button"
          className="ctl-btn"
          title="重新拉取模型列表"
          aria-label="重新拉取模型列表"
          disabled={!provider}
          onClick={() => {
            if (!provider) return;
            void fetchModels(provider);
            setFilter("");
            if (!open) setOpenState(true);
          }}
        >
          <RefreshCw className={cls("ic", list?.status === "loading" && "spin")} aria-hidden />
        </button>
      </div>
      {open ? (
        <div className="combo-pop" role="listbox" id={listId} ref={listRef} aria-label="模型候选">
          {head ? <div className="combo-head">{head}</div> : null}
          {options.map((model, index) => (
            <button
              type="button"
              tabIndex={-1}
              key={model}
              id={`${listId}-${index}`}
              data-index={index}
              role="option"
              aria-selected={model === text}
              className={cls("combo-opt", index === active && "is-active", model === text && "is-selected")}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => choose(model)}
              onMouseEnter={() => setActive(index)}
            >
              <span>{highlight(model, filter.trim())}</span>
              {model === text ? <Check className="ic" aria-hidden /> : null}
            </button>
          ))}
          {empty ? <p className="combo-empty">{empty}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
