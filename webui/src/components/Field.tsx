/** 由字段表驱动的字段渲染：标签 + TOML 键 + 控件 + 说明 / 错误，改动后标签被荧光笔划过。 */

import { ArrowUpRight, ChevronDown, Eye, EyeOff, FilePlus, TriangleAlert } from "lucide-react";
import { memo, useCallback, useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";

import { config, useConfig, useFieldState } from "@/lib/config";
import { files, useFiles } from "@/lib/files";
import { cls } from "@/lib/pages";
import { getIn, isRecord, pathKey } from "@/lib/path";
import { navigate } from "@/lib/router";
import { shallowArrayEqual } from "@/lib/store";
import { toast } from "@/lib/toast";
import type { Path } from "@/lib/types";
import { numberHint } from "@/schema";
import { fieldPath, type FieldDef } from "@/schema/types";

import { KeyValue } from "./KeyValue";
import { ModelCombo } from "./ModelCombo";
import { TagList } from "./TagList";

/* ---------- 开关 ---------- */

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  id?: string;
  label?: string;
  labelledBy?: string;
  disabled?: boolean;
  title?: string;
}

export function Switch({ checked, onChange, id, label, labelledBy, disabled, title }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      id={id}
      className="sw"
      aria-checked={checked}
      aria-label={label}
      aria-labelledby={label ? undefined : labelledBy}
      disabled={disabled}
      title={title}
      onClick={() => onChange(!checked)}
    >
      <span className="sw-track">
        <span className="sw-thumb" />
      </span>
    </button>
  );
}

/* ---------- 控件 ---------- */

interface ControlProps {
  field: FieldDef;
  id: string;
  labelId: string;
  path: Path;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled: boolean;
  invalid: boolean;
  describedBy?: string;
  onOpenChange: (open: boolean) => void;
}

function textOf(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

function TextControl({ field, id, value, onChange, disabled, invalid, describedBy }: ControlProps) {
  return (
    <div className="ctl">
      <input
        id={id}
        className={cls("inp", field.mono && "is-mono")}
        type="text"
        value={textOf(value)}
        placeholder={field.placeholder}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value === "" && field.nullable ? null : event.target.value)}
      />
    </div>
  );
}

function SecretControl({ field, id, value, onChange, disabled, invalid, describedBy }: ControlProps) {
  const [shown, setShown] = useState(false);
  return (
    <div className="ctl has-btn">
      <input
        id={id}
        className="inp is-mono"
        type={shown ? "text" : "password"}
        value={textOf(value)}
        placeholder={field.placeholder ?? "留空表示不设置"}
        disabled={disabled}
        spellCheck={false}
        autoComplete="new-password"
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value === "" && field.nullable ? null : event.target.value)}
      />
      <div className="ctl-btns">
        <button
          type="button"
          className="ctl-btn"
          aria-label={shown ? "隐藏" : "显示"}
          title={shown ? "隐藏" : "显示"}
          onClick={() => setShown((current) => !current)}
        >
          {shown ? <EyeOff className="ic" aria-hidden /> : <Eye className="ic" aria-hidden />}
        </button>
      </div>
    </div>
  );
}

function AreaControl({ field, id, value, onChange, disabled, invalid, describedBy }: ControlProps) {
  return (
    <div className="ctl ctl-area">
      <textarea
        id={id}
        className="inp"
        rows={2}
        value={textOf(value)}
        placeholder={field.placeholder}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value === "" && field.nullable ? null : event.target.value)}
      />
    </div>
  );
}

function parseNumber(raw: string): number | null {
  const text = raw.trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(text)) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

function textMatches(text: string, value: unknown): boolean {
  if (typeof value === "number") return parseNumber(text) === value;
  if (typeof value === "string") return text === value;
  return text.trim() === "";
}

function NumberControl({ field, id, value, onChange, disabled, invalid, describedBy }: ControlProps) {
  const [text, setText] = useState(() => textOf(value));
  const [seen, setSeen] = useState(value);
  // 外部改动（丢弃、保存后规范化、联动）时同步显示文本；自己输入的中间态（如「1.」）保持不动。
  if (!Object.is(seen, value)) {
    setSeen(value);
    if (!textMatches(text, value)) setText(textOf(value));
  }
  const commit = (raw: string) => {
    setText(raw);
    const parsed = parseNumber(raw);
    onChange(parsed === null ? raw : parsed);
  };
  const step = (event: KeyboardEvent<HTMLInputElement>, direction: 1 | -1) => {
    if (typeof value !== "number") return;
    event.preventDefault();
    const fraction = !Number.isInteger(value) || (!field.integer && Math.abs(value) < 1 && value !== 0);
    const size = (fraction ? 0.1 : 1) * (event.shiftKey ? 10 : 1);
    let next = Math.round((value + direction * size) * 1e6) / 1e6;
    if (field.min !== undefined && next < field.min) next = field.min;
    if (field.max !== undefined && next > field.max) next = field.max;
    commit(String(next));
  };
  const hint = numberHint(field, value);
  return (
    <div className="ctl ctl-num">
      <input
        id={id}
        className="inp"
        type="text"
        inputMode={field.integer ? "numeric" : "decimal"}
        value={text}
        placeholder={field.placeholder}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        onChange={(event) => commit(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowUp") step(event, 1);
          else if (event.key === "ArrowDown") step(event, -1);
        }}
        onBlur={() => {
          if (typeof value === "number" && text !== String(value)) setText(String(value));
        }}
      />
      {field.unit ? <span className="unit">{field.unit}</span> : null}
      {hint ? (
        <span className="num-hint" key={hint}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}

function SwitchControl({ id, labelId, value, onChange, disabled }: ControlProps) {
  return (
    <div className="ctl ctl-switch">
      <Switch id={id} checked={value === true} onChange={onChange} labelledBy={labelId} disabled={disabled} />
    </div>
  );
}

function SelectControl({ field, id, value, onChange, disabled, invalid, describedBy }: ControlProps) {
  const options = field.options ?? [];
  const current = textOf(value);
  const known = options.some(([option]) => option === current);
  return (
    <div className="ctl ctl-select">
      <select
        id={id}
        className={cls("inp", field.mono && "is-mono")}
        value={current}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      >
        {!known ? <option value={current}>{current === "" ? "请选择" : `${current}（不支持）`}</option> : null}
        {options.map(([option, label]) => (
          <option key={option} value={option}>
            {label}
          </option>
        ))}
      </select>
      <ChevronDown className="ic sel-chev" aria-hidden />
    </div>
  );
}

function SegmentedControl({ field, labelId, value, onChange, disabled }: ControlProps) {
  const options = field.options ?? [];
  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const delta = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (delta === 0) return;
    event.preventDefault();
    const next = options[(index + delta + options.length) % options.length];
    if (!next) return;
    onChange(next[0]);
    const group = event.currentTarget.parentElement;
    requestAnimationFrame(() => group?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus());
  };
  return (
    <div className="ctl ctl-seg" role="radiogroup" aria-labelledby={labelId}>
      {options.map(([option, label], index) => (
        <button
          type="button"
          role="radio"
          key={option}
          className="seg-opt"
          aria-checked={option === value}
          tabIndex={option === value || (index === 0 && !options.some(([item]) => item === value)) ? 0 : -1}
          disabled={disabled}
          onClick={() => onChange(option)}
          onKeyDown={(event) => move(event, index)}
        >
          <code>{option}</code>
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}

function FileControl({ field, id, value, onChange, disabled, invalid, describedBy }: ControlProps) {
  const list = useFiles((state) => state.list);
  useEffect(() => {
    if (files.state.list === null) void files.refreshList();
  }, []);
  const text = textOf(value).trim();
  const missing = list !== null && text !== "" && !list.includes(text);
  const [creating, setCreating] = useState(false);
  const create = async () => {
    setCreating(true);
    const error = await files.create(text);
    setCreating(false);
    if (error === null) navigate({ page: "files", file: text });
    else toast.error(error);
  };
  return (
    <>
      <div className="ctl has-btn">
        <input
          id={id}
          className={cls("inp is-mono", invalid && "is-error")}
          type="text"
          list="config-files"
          value={textOf(value)}
          placeholder={field.placeholder ?? "相对 config/ 的路径"}
          disabled={disabled}
          spellCheck={false}
          autoComplete="off"
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value === "" && field.nullable ? null : event.target.value)}
        />
        <div className="ctl-btns">
          {missing ? (
            <button
              type="button"
              className="ctl-btn"
              title="在 config/ 里新建这个文件"
              aria-label="在 config/ 里新建这个文件"
              disabled={creating}
              onClick={() => void create()}
            >
              <FilePlus className="ic" aria-hidden />
            </button>
          ) : (
            <button
              type="button"
              className="ctl-btn"
              title="在配置文本里打开"
              aria-label="在配置文本里打开"
              disabled={text === ""}
              onClick={() => navigate({ page: "files", file: text })}
            >
              <ArrowUpRight className="ic" aria-hidden />
            </button>
          )}
        </div>
      </div>
      {/* 服务端已经报错时只显示错误，避免同一件事说两遍。 */}
      {missing && !invalid ? <p className="fld-warn">config/ 里还没有这个文件，写入会被拒绝；点右侧按钮新建</p> : null}
    </>
  );
}

function ProviderControl({ id, value, onChange, disabled, invalid, describedBy }: ControlProps) {
  const providers = useConfig(
    (state) => {
      const record = getIn(state.draft, ["llm", "providers"]);
      return isRecord(record) ? Object.keys(record) : [];
    },
    shallowArrayEqual,
  );
  const current = textOf(value);
  const known = providers.includes(current);
  return (
    <div className="ctl ctl-select">
      <select
        id={id}
        className="inp is-mono"
        value={current}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      >
        {!known ? (
          <option value={current} disabled={current === ""}>
            {current === "" ? (providers.length ? "选择 Provider" : "还没有 Provider") : `${current}（未定义）`}
          </option>
        ) : null}
        {providers.map((provider) => (
          <option key={provider} value={provider}>
            {provider}
          </option>
        ))}
      </select>
      <ChevronDown className="ic sel-chev" aria-hidden />
    </div>
  );
}

function Control(props: ControlProps) {
  const { field } = props;
  switch (field.kind) {
    case "text":
      return <TextControl {...props} />;
    case "secret":
      return <SecretControl {...props} />;
    case "textarea":
      return <AreaControl {...props} />;
    case "number":
      return <NumberControl {...props} />;
    case "switch":
      return <SwitchControl {...props} />;
    case "select":
      return <SelectControl {...props} />;
    case "segmented":
      return <SegmentedControl {...props} />;
    case "tags":
    case "numtags":
    case "ids":
      return (
        <TagList
          id={props.id}
          field={field}
          value={props.value}
          onChange={props.onChange}
          disabled={props.disabled}
          describedBy={props.describedBy}
        />
      );
    case "file":
      return <FileControl {...props} />;
    case "provider":
      return <ProviderControl {...props} />;
    case "model": {
      const base = props.path.slice(0, props.path.length - field.key.split(".").length);
      const providerPath = [...base, ...(field.providerKey ?? "provider").split(".")];
      return (
        <ModelCombo
          id={props.id}
          providerPath={providerPath}
          value={props.value}
          onChange={props.onChange}
          onOpenChange={props.onOpenChange}
          describedBy={props.describedBy}
          invalid={props.invalid}
        />
      );
    }
    case "keyvalue":
      return (
        <KeyValue
          id={props.id}
          label={field.label}
          value={props.value}
          onChange={props.onChange}
          secretValues={field.key === "headers"}
          addLabel={field.addLabel}
          keyPlaceholder={field.key === "headers" ? "Authorization" : "API_KEY"}
        />
      );
  }
}

/* ---------- 字段 ---------- */

interface FieldProps {
  field: FieldDef;
  /** 分节根路径；必须是稳定引用，字段才能跳过无关重渲染。 */
  base: Path;
}

export const Field = memo(function Field({ field, base }: FieldProps) {
  const path = useMemo(() => fieldPath(base, field), [base, field]);
  const { value, dirty, issue } = useFieldState(path);
  const disabledReason = useConfig((state) => (field.disabled ? field.disabled(state.draft) : false));
  const [open, setOpen] = useState(false);
  const id = useId();
  const onChange = useCallback(
    (next: unknown) => {
      const apply = field.apply;
      if (apply) config.update((draft) => apply(draft, next), path);
      else config.set(path, next);
    },
    [field, path],
  );
  const disabled = disabledReason !== false;
  const desc = disabled ? disabledReason : field.desc;
  const describedBy = [desc ? `${id}-desc` : "", issue ? `${id}-err` : ""].filter(Boolean).join(" ") || undefined;
  return (
    <div
      className={cls(
        "fld",
        field.span === 2 && "fld--span2",
        field.span === 3 && "fld--span3",
        field.wide && "fld--wide",
        dirty && "is-dirty",
        issue && "is-error",
        disabled && "is-disabled",
        open && "is-open",
      )}
      data-field=""
      data-path={pathKey(path)}
      data-search={`${field.label} ${field.key} ${field.desc ?? ""}`.toLowerCase()}
    >
      <span className="fld-mark" aria-hidden />
      <div className="fld-head">
        <label className="fld-label" htmlFor={id} id={`${id}-label`}>
          {field.label}
        </label>
        <code className="fld-key" title={field.key}>
          {field.key}
        </code>
      </div>
      <Control
        field={field}
        id={id}
        labelId={`${id}-label`}
        path={path}
        value={value}
        onChange={onChange}
        disabled={disabled}
        invalid={Boolean(issue)}
        describedBy={describedBy}
        onOpenChange={setOpen}
      />
      {desc ? (
        <p className="fld-desc" id={`${id}-desc`}>
          {desc}
        </p>
      ) : null}
      {issue ? (
        <p className="fld-err" id={`${id}-err`} role="alert">
          <TriangleAlert className="ic" aria-hidden />
          {issue}
        </p>
      ) : null}
    </div>
  );
});
