import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Check, Dices, Eye, EyeOff, Plus, X } from "lucide-react";
import { ApiError, errorMessage, get } from "../lib/api";
import { change, useConfig } from "../lib/config";
import { formatNumber } from "../lib/format";
import { getAt, object } from "../lib/types";
import type { Json, Path } from "../lib/types";
import { confirm } from "../lib/ui";
import { fieldId } from "../schema/fields";
import type { FieldDef, Option, SectionDef } from "../schema/fields";
import { Combobox } from "./Combobox";
import { Button, IconButton, Segmented, Switch } from "./primitives";

/** 设置行：左侧名称与说明，右侧控件与错误；窄屏时上下排列。 */
export function FieldShell({
  id,
  label,
  help,
  error,
  dirty = false,
  restart = false,
  keyPath,
  children,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  error?: string;
  dirty?: boolean;
  restart?: boolean;
  keyPath?: string;
  children: ReactNode;
}) {
  return (
    <div className={"field" + (error ? " is-invalid" : "")}>
      <div className="field-text">
        <div className="field-head">
          <label className="field-label" htmlFor={id} title={keyPath}>
            {label}
          </label>
          {dirty && (
            <span className="field-dirty" title="尚未保存">
              <span className="sr-only">尚未保存</span>
            </span>
          )}
          {restart && <span className="field-tag">重启生效</span>}
        </div>
        {help && <p className="field-help">{help}</p>}
      </div>
      <div className="field-control">
        {children}
        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

export function TextInput({
  id,
  value,
  onChange,
  placeholder,
  nullable = false,
  invalid = false,
  mono = false,
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
  placeholder?: string;
  nullable?: boolean;
  invalid?: boolean;
  mono?: boolean;
}) {
  return (
    <input
      id={id}
      className={"input" + (mono ? " mono" : "")}
      value={typeof value === "string" ? value : ""}
      placeholder={placeholder}
      aria-invalid={invalid || undefined}
      autoComplete="off"
      spellCheck={false}
      onChange={(event) =>
        onChange(nullable && !event.target.value ? null : event.target.value)
      }
    />
  );
}

function randomSecret() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export function SecretInput({
  id,
  value,
  onChange,
  generate = false,
  invalid = false,
  label,
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
  generate?: boolean;
  invalid?: boolean;
  label: string;
}) {
  const [visible, setVisible] = useState(false);
  const current = typeof value === "string" ? value : "";
  return (
    <div className="input-group">
      <input
        id={id}
        className="input mono"
        type={visible ? "text" : "password"}
        value={current}
        aria-invalid={invalid || undefined}
        autoComplete="new-password"
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
      />
      <IconButton
        icon={visible ? EyeOff : Eye}
        label={visible ? "隐藏" + label : "显示" + label}
        onClick={() => setVisible(!visible)}
      />
      {generate && (
        <IconButton
          icon={Dices}
          label={"随机生成" + label}
          onClick={async () => {
            if (
              current &&
              !(await confirm({
                title: "生成新的" + label,
                message: "原有" + label + "会被替换，另一端也需要改成新值才能继续连接。",
                confirmLabel: "生成并替换",
                danger: true,
              }))
            )
              return;
            onChange(randomSecret());
            setVisible(true);
          }}
        />
      )}
    </div>
  );
}

const parseNumber = (text: string) => (text.trim() === "" ? null : Number(text.trim()));

export function NumberInput({
  id,
  value,
  onChange,
  unit,
  nullable = false,
  invalid = false,
  placeholder,
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
  unit?: string;
  nullable?: boolean;
  invalid?: boolean;
  placeholder?: string;
}) {
  const shown = typeof value === "number" ? String(value) : "";
  const [text, setText] = useState(shown);
  useEffect(() => {
    // 外部改动（丢弃、刷新）时同步；输入中途的“1.”之类不覆盖。
    setText((current) => (parseNumber(current) === (typeof value === "number" ? value : null) ? current : shown));
  }, [value, shown]);
  return (
    <div className={"input-group narrow" + (unit ? " has-suffix" : "")}>
      <input
        id={id}
        className="input num"
        inputMode="decimal"
        value={text}
        placeholder={placeholder}
        aria-invalid={invalid || undefined}
        autoComplete="off"
        onChange={(event) => {
          setText(event.target.value);
          const next = parseNumber(event.target.value);
          onChange(next === null ? (nullable ? null : null) : Number.isFinite(next) ? next : null);
        }}
      />
      {unit && <span className="input-suffix">{unit}</span>}
    </div>
  );
}

const byteUnits = {
  decimal: [
    { label: "KB", size: 1e3 },
    { label: "MB", size: 1e6 },
    { label: "GB", size: 1e9 },
    { label: "TB", size: 1e12 },
  ],
  binary: [
    { label: "KiB", size: 1024 },
    { label: "MiB", size: 1024 ** 2 },
    { label: "GiB", size: 1024 ** 3 },
    { label: "TiB", size: 1024 ** 4 },
  ],
};
function pickUnit(value: number, family: "decimal" | "binary") {
  const units = byteUnits[family];
  for (let i = units.length - 1; i > 0; i--) if (value >= units[i].size) return i;
  return 0;
}
const trimNumber = (value: number) => String(Number(value.toFixed(4)));

/** 字节值以 KB/MB/GB 等单位编辑，保存时仍为整数字节。 */
export function ByteInput({
  id,
  value,
  onChange,
  family = "decimal",
  invalid = false,
  label,
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
  family?: "decimal" | "binary";
  invalid?: boolean;
  label: string;
}) {
  const bytes = typeof value === "number" ? value : null;
  const units = byteUnits[family];
  const [unit, setUnit] = useState(() => pickUnit(bytes ?? 0, family));
  const [text, setText] = useState(bytes === null ? "" : trimNumber(bytes / units[unit].size));
  useEffect(() => {
    setText((current) => {
      const parsed = parseNumber(current);
      const same =
        parsed !== null && bytes !== null && Math.round(parsed * units[unit].size) === bytes;
      return same ? current : bytes === null ? "" : trimNumber(bytes / units[unit].size);
    });
  }, [bytes, unit, units]);
  const commit = (raw: string, index: number) => {
    const parsed = parseNumber(raw);
    onChange(parsed === null || !Number.isFinite(parsed) ? null : Math.round(parsed * units[index].size));
  };
  return (
    <div className="input-group narrow">
      <input
        id={id}
        className="input num"
        inputMode="decimal"
        value={text}
        aria-invalid={invalid || undefined}
        autoComplete="off"
        onChange={(event) => {
          setText(event.target.value);
          commit(event.target.value, unit);
        }}
      />
      <Combobox
        className="unit-select"
        label={label + "单位"}
        value={String(unit)}
        options={units.map((item, index) => ({ value: String(index), label: item.label }))}
        onChange={(next) => {
          const index = Number(next);
          setUnit(index);
          commit(text, index);
        }}
      />
    </div>
  );
}

function display(item: Json) {
  return typeof item === "string" ? item : JSON.stringify(item);
}

export function TagInput({
  id,
  value,
  onChange,
  placeholder = "输入后按回车添加",
  typed = false,
  suggestions = [],
  describe,
  invalid = false,
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
  placeholder?: string;
  typed?: boolean;
  suggestions?: Option[];
  describe?: (item: Json) => string | undefined;
  invalid?: boolean;
}) {
  const entries = Array.isArray(value) ? value : [];
  const [input, setInput] = useState("");
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const parse = (source: string): Json => {
    if (!typed) return source;
    try {
      return JSON.parse(source) as Json;
    } catch {
      return source;
    }
  };
  const add = (sources: string[]) => {
    const next = [...entries];
    for (const source of sources.map((item) => item.trim()).filter(Boolean)) {
      const item = parse(source);
      if (!next.some((entry) => JSON.stringify(entry) === JSON.stringify(item))) next.push(item);
    }
    if (next.length !== entries.length) onChange(next);
    setInput("");
  };
  const query = input.trim().toLowerCase();
  const matches = suggestions
    .filter((option) => !entries.some((entry) => display(entry) === option.value))
    .filter(
      (option) =>
        !query ||
        option.value.toLowerCase().includes(query) ||
        option.label.toLowerCase().includes(query),
    )
    .slice(0, 8);
  const open = focused && matches.length > 0;
  return (
    <div className={"tag-input" + (invalid ? " is-invalid" : "")}>
      {entries.map((item, index) => {
        const note = describe?.(item);
        return (
          <span className="tag" key={display(item) + index}>
            <span className="tag-text">{display(item)}</span>
            {note && <span className="tag-note">{note}</span>}
            <button
              type="button"
              aria-label={"移除 " + display(item)}
              onClick={() => onChange(entries.filter((_, i) => i !== index))}
            >
              <X size={12} aria-hidden="true" />
            </button>
          </span>
        );
      })}
      <span className="tag-entry">
        <input
          id={id}
          value={input}
          placeholder={entries.length ? "" : placeholder}
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-autocomplete="list"
          onFocus={() => setFocused(true)}
          onBlur={() => {
            setFocused(false);
            if (input.trim()) add([input]);
          }}
          onChange={(event) => {
            setActive(0);
            const text = event.target.value;
            if (/[\n,，]/.test(text)) add(text.split(/[\n,，]/));
            else setInput(text);
          }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Enter") {
              event.preventDefault();
              if (open && matches[active] && (!query || matches[active].value.toLowerCase().startsWith(query) || !input.trim()))
                add([matches[active].value]);
              else add([input]);
            } else if (event.key === "Backspace" && !input && entries.length)
              onChange(entries.slice(0, -1));
            else if (open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              setActive((current) => (current + step + matches.length) % matches.length);
            } else if (event.key === "Escape" && open) {
              event.stopPropagation();
              setFocused(false);
            }
          }}
        />
        {open && (
          <ul className="tag-suggestions" id={listId} role="listbox">
            {matches.map((option, index) => (
              <li
                key={option.value}
                role="option"
                aria-selected={index === active}
                className={index === active ? "active" : ""}
                onPointerDown={(event) => event.preventDefault()}
                onPointerMove={() => setActive(index)}
                onClick={() => add([option.value])}
              >
                <span className="mono">{option.value}</span>
                {option.label !== option.value && <span className="muted">{option.label}</span>}
              </li>
            ))}
          </ul>
        )}
      </span>
    </div>
  );
}

const reservedKeys = ["__proto__", "constructor", "prototype"];
export function KeyValueInput({
  id,
  value,
  onChange,
  keyPlaceholder = "名称",
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
  keyPlaceholder?: string;
}) {
  const data = object(value);
  const [key, setKey] = useState("");
  const [text, setText] = useState("");
  const [visible, setVisible] = useState(false);
  const name = key.trim();
  const problem = !name
    ? ""
    : reservedKeys.includes(name)
      ? "不能使用这个名称"
      : Object.hasOwn(data, name)
        ? "名称已存在"
        : "";
  const add = () => {
    if (!name || problem) return;
    const next = { ...data };
    Object.defineProperty(next, name, { value: text, enumerable: true, writable: true, configurable: true });
    onChange(next);
    setKey("");
    setText("");
  };
  const entries = Object.entries(data);
  return (
    <div className="kv">
      {entries.length > 0 && (
        <div className="kv-head">
          <span className="muted tiny">已添加 {entries.length} 项</span>
          <Button size="sm" variant="ghost" icon={visible ? EyeOff : Eye} onClick={() => setVisible(!visible)}>
            {visible ? "隐藏值" : "显示值"}
          </Button>
        </div>
      )}
      {entries.map(([entry, item]) => (
        <div className="kv-row" key={entry}>
          <code className="kv-key" title={entry}>
            {entry}
          </code>
          <input
            className="input mono"
            type={visible ? "text" : "password"}
            aria-label={entry + " 的值"}
            value={typeof item === "string" ? item : JSON.stringify(item)}
            autoComplete="new-password"
            onChange={(event) => onChange({ ...data, [entry]: event.target.value })}
          />
          <IconButton
            icon={X}
            label={"删除 " + entry}
            onClick={() => {
              const next = { ...data };
              delete next[entry];
              onChange(next);
            }}
          />
        </div>
      ))}
      <div className="kv-row kv-new">
        <input
          id={id}
          className="input mono"
          placeholder={keyPlaceholder}
          value={key}
          aria-label="新增项名称"
          aria-invalid={!!problem || undefined}
          autoComplete="off"
          onChange={(event) => setKey(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && add()}
        />
        <input
          className="input mono"
          placeholder="值"
          type={visible ? "text" : "password"}
          value={text}
          aria-label="新增项的值"
          autoComplete="new-password"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && add()}
        />
        <IconButton icon={Plus} label="添加" onClick={add} disabled={!name || !!problem} />
      </div>
      {problem && <p className="field-error">{problem}</p>}
    </div>
  );
}

export function ChecksInput({
  id,
  value,
  onChange,
  options,
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
  options: Option[];
}) {
  const selected = new Set(Array.isArray(value) ? value.map(String) : []);
  return (
    <div className="checks" id={id} role="group">
      {options.map((option) => {
        const on = selected.has(option.value);
        return (
          <button
            key={option.value}
            type="button"
            className={"check-chip" + (on ? " on" : "")}
            aria-pressed={on}
            onClick={() => {
              const next = new Set(selected);
              if (on) next.delete(option.value);
              else next.add(option.value);
              onChange(options.map((item) => item.value).filter((item) => next.has(item)));
            }}
          >
            {on && <Check size={14} aria-hidden="true" />}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function ModelPicker({
  id,
  providerId,
  value,
  onChange,
  invalid = false,
}: {
  id: string;
  providerId: string;
  value: string;
  onChange: (value: Json) => void;
  invalid?: boolean;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [status, setStatus] = useState<{ tone: "ok" | "error" | "idle"; text: string }>({
    tone: "idle",
    text: "",
  });
  const [loading, setLoading] = useState(false);
  const ticket = useRef(0);
  const load = async (refresh = false) => {
    const current = ++ticket.current;
    if (!providerId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const result = await get<{ models: string[] }>(
        "/api/providers/" + encodeURIComponent(providerId) + "/models" + (refresh ? "?refresh=true" : ""),
      );
      if (current !== ticket.current) return;
      const sorted = [...new Set(result.models)].sort((a, b) => a.localeCompare(b));
      setModels(sorted);
      setStatus({
        tone: "ok",
        text: sorted.length ? "已读取 " + formatNumber(sorted.length) + " 个模型" : "接口没有返回模型，可直接输入",
      });
    } catch (error) {
      if (current !== ticket.current) return;
      setStatus({
        tone: "error",
        text:
          error instanceof ApiError && error.status === 404
            ? "服务商保存后才能读取模型列表"
            : "无法读取模型列表：" + errorMessage(error) + "。仍可直接输入模型名称",
      });
    } finally {
      if (current === ticket.current) setLoading(false);
    }
  };
  // 接口地址、密钥等保存后重新读取（跳过服务端缓存），新建的服务商保存后也会自动读取。
  const saved = useConfig().base;
  const savedKey = JSON.stringify(
    ["protocol", "base_url", "api_key", "proxy", "timeout"].map((key) =>
      getAt(saved, ["providers", providerId, key]),
    ),
  );
  const lastProvider = useRef<string | null>(null);
  useEffect(() => {
    const sameProvider = lastProvider.current === providerId;
    lastProvider.current = providerId;
    if (!sameProvider) {
      setModels([]);
      setStatus({ tone: "idle", text: "" });
    }
    void load(sameProvider);
    return () => {
      ticket.current++;
    };
  }, [providerId, savedKey]);
  return (
    <>
      <Combobox
        id={id}
        label="模型名称"
        value={value}
        onChange={onChange}
        options={models.map((model) => ({ value: model, label: model }))}
        editable
        invalid={invalid}
        placeholder="选择或输入模型名称"
        listLabel="接口返回的模型"
        loading={loading}
        emptyText="没有可选模型，可直接输入"
        freeTextHint="没有匹配的模型，将使用输入的名称"
        onRefresh={providerId ? () => void load(true) : undefined}
        refreshLabel="重新读取模型列表"
      />
      {!providerId ? (
        <p className="field-status">先选择服务商，再从它的模型列表中选择</p>
      ) : (
        status.text && (
          <p className={"field-status" + (status.tone === "error" ? " is-error" : "")}>{status.text}</p>
        )
      )}
    </>
  );
}

/** 服务商选择：选项来自配置中的服务商；已删除的旧值保留显示，由校验提示重新选择。 */
function ProviderSelect({
  id,
  def,
  value,
  onChange,
  invalid,
}: {
  id: string;
  def: FieldDef;
  value: string;
  onChange: (value: Json) => void;
  invalid: boolean;
}) {
  const providers = Object.keys(object(useConfig().draft.providers));
  const options = [
    ...(def.emptyLabel !== undefined ? [{ value: "", label: def.emptyLabel }] : []),
    ...providers.map((name) => ({ value: name, label: name })),
    ...(value && !providers.includes(value) ? [{ value, label: value + "（已不存在）" }] : []),
  ];
  return (
    <Combobox
      id={id}
      label={def.label}
      value={value}
      invalid={invalid}
      placeholder={def.placeholder}
      options={options}
      emptyText="还没有服务商，请先在服务商页添加"
      onChange={onChange}
    />
  );
}

const startsWith = (path: Path, prefix: Path) =>
  prefix.length <= path.length && prefix.every((part, index) => String(path[index]) === String(part));

/** 绑定到配置草稿的字段；改动后由自动保存写回。 */
export function ConfigField({
  def,
  base,
  suggestions,
  describe,
  help,
}: {
  def: FieldDef;
  base: Path;
  suggestions?: Option[];
  describe?: (item: Json) => string | undefined;
  help?: ReactNode;
}) {
  const state = useConfig();
  if (def.hidden?.(state.draft, base)) return null;
  const path = [...base, def.key];
  const id = fieldId(path);
  const value = getAt(state.draft, path);
  const dirty = state.changed.some((item) => startsWith(item, path) || startsWith(path, item));
  const error = state.issues.find((issue) => startsWith(issue.path, path))?.message;
  const update = (next: Json) => change(path, next);
  const shell = (control: ReactNode) => (
    <FieldShell
      id={id}
      label={def.label}
      help={help ?? def.help}
      error={error}
      dirty={dirty}
      restart={def.restart}
      keyPath={path.join(".")}
    >
      {control}
    </FieldShell>
  );
  switch (def.type) {
    case "toggle":
      return shell(<Switch id={id} checked={value === true} label={def.label} onChange={update} />);
    case "number":
      return shell(
        <NumberInput
          id={id}
          value={value}
          onChange={update}
          unit={def.unit}
          nullable={def.nullable}
          invalid={!!error}
          placeholder={def.placeholder}
        />,
      );
    case "bytes":
      return shell(
        <ByteInput id={id} value={value} onChange={update} family={def.bytes} invalid={!!error} label={def.label} />,
      );
    case "secret":
      return shell(
        <SecretInput id={id} value={value} onChange={update} generate={def.generate} invalid={!!error} label={def.label} />,
      );
    case "select": {
      const options = def.options ?? [];
      return shell(
        <Combobox
          id={id}
          label={def.label}
          value={typeof value === "string" ? value : ""}
          invalid={!!error}
          placeholder={def.placeholder}
          options={def.nullable ? [{ value: "", label: def.placeholder ?? "不设置" }, ...options] : options}
          onChange={(next) => update(def.nullable && next === "" ? null : next)}
        />,
      );
    }
    case "segmented":
      return shell(
        <Segmented
          id={id}
          label={def.label}
          value={typeof value === "string" ? value : ""}
          options={def.options ?? []}
          onChange={update}
        />,
      );
    case "tags":
      return shell(
        <TagInput
          id={id}
          value={value}
          onChange={update}
          placeholder={def.placeholder}
          typed={def.typed}
          suggestions={suggestions}
          describe={describe}
          invalid={!!error}
        />,
      );
    case "checks":
      return shell(<ChecksInput id={id} value={value} onChange={update} options={def.options ?? []} />);
    case "map":
      return shell(<KeyValueInput id={id} value={value} onChange={update} />);
    case "provider":
      return shell(
        <ProviderSelect
          id={id}
          def={def}
          value={typeof value === "string" ? value : ""}
          onChange={update}
          invalid={!!error}
        />,
      );
    case "model": {
      // 模型列表来自同一组设置里选中的服务商。
      const provider = getAt(state.draft, [...base, "provider"]);
      return shell(
        <ModelPicker
          id={id}
          providerId={typeof provider === "string" ? provider : ""}
          value={typeof value === "string" ? value : ""}
          onChange={update}
          invalid={!!error}
        />,
      );
    }
    default:
      return shell(
        <TextInput
          id={id}
          value={value}
          onChange={update}
          placeholder={def.placeholder}
          nullable={def.nullable}
          invalid={!!error}
        />,
      );
  }
}

export function ConfigSection({
  def,
  base,
  actions,
  children,
  suggestions,
  describe,
  helpFor,
  plain = false,
}: {
  def: SectionDef;
  base: Path;
  actions?: ReactNode;
  children?: ReactNode;
  suggestions?: Record<string, Option[]>;
  describe?: Record<string, (item: Json) => string | undefined>;
  helpFor?: Record<string, ReactNode>;
  plain?: boolean;
}) {
  const state = useConfig();
  const visible = def.fields.filter((field) => !field.hidden?.(state.draft, base));
  if (!visible.length && !children) return null;
  return (
    <section className={plain ? "form-section plain" : "card form-section"} id={"section-" + def.id}>
      <header className="section-header">
        <div className="grow">
          <h2>{def.title}</h2>
          {def.description && <p>{def.description}</p>}
        </div>
        {actions}
      </header>
      <div className="form-grid">
        {visible.map((field) => (
          <ConfigField
            key={field.key}
            def={field}
            base={base}
            suggestions={suggestions?.[field.key]}
            describe={describe?.[field.key]}
            help={helpFor?.[field.key]}
          />
        ))}
        {children}
      </div>
    </section>
  );
}
