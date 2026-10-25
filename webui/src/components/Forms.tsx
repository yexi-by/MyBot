import { useEffect, useRef, useState } from "react";
import { request, errorMessage } from "../lib/api";
import { change, useConfig } from "../lib/config";
import { fieldId } from "../lib/navigation";
import { equal, getAt, object } from "../lib/types";
import type { Json, ObjectValue, Path } from "../lib/types";
import type { FieldDef, SectionDef } from "../schema/fields";
import { ask } from "../lib/ui";
import { Combobox } from "./Combobox";

function Lines({
  id,
  value,
  onChange,
  options,
  typed = false,
  disabled = false,
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
  options?: FieldDef["options"];
  typed?: boolean;
  disabled?: boolean;
}) {
  const entries = Array.isArray(value) ? value : [];
  const [input, setInput] = useState("");
  const add = () => {
    const source = input.trim();
    if (!source) return;
    let item: Json = source;
    if (typed) {
      try {
        item = JSON.parse(source) as Json;
      } catch {
        /* 普通文字作为文本值。 */
      }
    }
    if (!entries.some((entry) => equal(entry, item)))
      onChange([...entries, item]);
    setInput("");
  };
  return (
    <div className="list-control">
      <div className="tags">
        {entries.map((item, index) => (
          <span className="tag" key={index}>
            <span>
              {typeof item === "string" ? item : JSON.stringify(item)}
            </span>
            <button
              aria-label={"移除 " + String(item)}
              onClick={() => onChange(entries.filter((_, i) => i !== index))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="inline-input">
        {options ? (
          <Combobox
            id={id}
            label="选择模态"
            value={input}
            onChange={setInput}
            disabled={disabled}
            options={[{ value: "", label: "选择模态" }, ...options]}
          />
        ) : (
          <input
            id={id}
            aria-label="新增一项"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
            placeholder="输入后按 Enter"
          />
        )}
        <button className="small" onClick={add} disabled={!input.trim()}>
          添加
        </button>
      </div>
    </div>
  );
}
function MapInput({
  id,
  value,
  onChange,
}: {
  id: string;
  value: Json | undefined;
  onChange: (value: Json) => void;
}) {
  const data = object(value),
    [key, setKey] = useState(""),
    [text, setText] = useState("");
  return (
    <div className="map-control">
      {Object.entries(data).map(([name, item]) => (
        <div className="map-row" key={name}>
          <code>{name}</code>
          <input
            aria-label={name + " 的值"}
            value={String(item ?? "")}
            onChange={(event) =>
              onChange({ ...data, [name]: event.target.value })
            }
          />
          <button
            className="icon-button"
            aria-label={"删除 " + name}
            onClick={() => {
              const next = { ...data };
              delete next[name];
              onChange(next);
            }}
          >
            ×
          </button>
        </div>
      ))}
      <div className="map-row">
        <input
          id={id}
          aria-label="新字段名"
          placeholder="名称"
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
        <input
          aria-label="新字段值"
          placeholder="值"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <button
          className="small"
          disabled={
            !key.trim() ||
            ["__proto__", "constructor", "prototype"].includes(key.trim())
          }
          onClick={() => {
            onChange({ ...data, [key.trim()]: text });
            setKey("");
            setText("");
          }}
        >
          添加
        </button>
      </div>
    </div>
  );
}
function ModelInput({
  id,
  value,
  onChange,
  providerId,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (value: Json) => void;
  providerId: string;
  disabled: boolean;
}) {
  const [models, setModels] = useState<string[]>([]),
    [hint, setHint] = useState(""),
    [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const load = async (refresh = false) => {
    const ticket = ++generation.current;
    setLoading(true);
    setHint("");
    try {
      const result = await request<{ models: string[] }>(
        "/api/providers/" +
          encodeURIComponent(providerId) +
          "/models" +
          (refresh ? "?refresh=true" : ""),
      );
      if (ticket === generation.current) {
        setModels(
          [...new Set(result.models)].sort((a, b) => a.localeCompare(b)),
        );
      }
    } catch (error) {
      if (ticket === generation.current)
        setHint(errorMessage(error) + "；可手动输入");
    } finally {
      if (ticket === generation.current) setLoading(false);
    }
  };
  useEffect(() => {
    setModels([]);
    setHint("");
    void load();
    return () => {
      generation.current++;
    };
  }, [providerId]);
  return (
    <Combobox
      id={id}
      label="模型名称"
      value={value}
      onChange={onChange}
      options={models.map((model) => ({ value: model, label: model }))}
      editable
      disabled={disabled}
      placeholder="选择或输入模型名"
      listLabel={providerId}
      loading={loading}
      error={hint}
      emptyText="该 Provider 未返回模型，可直接输入模型名"
      onRefresh={() => void load(true)}
    />
  );
}
export function Field({
  definition,
  base,
  valueOverride,
  onOverride,
  filter = "",
}: {
  definition: FieldDef;
  base: Path;
  valueOverride?: Json;
  onOverride?: (value: Json) => void;
  filter?: string;
}) {
  const state = useConfig(),
    path = [...base, definition.key],
    id = fieldId(path),
    value =
      valueOverride !== undefined ? valueOverride : getAt(state.draft, path);
  const dirty = !equal(getAt(state.base, path), value),
    error = state.issues.find((issue) => equal(issue.path, path));
  const disabled = definition.disabled?.(state.draft, base) || false,
    [visible, setVisible] = useState(false);
  const update = (next: Json) => {
    const apply = () => (onOverride ? onOverride(next) : change(path, next));
    if (definition.key === "enabled" && value === true && next === false) {
      void ask(
        "停用配置",
        "停用后将不再接收对应会话或服务的新调用。已保存的配置内容仍然保留。",
        { danger: true, confirm: "停用" },
      ).then((confirmed) => {
        if (confirmed === true) apply();
      });
    } else apply();
  };
  const options = definition.choices?.(state.draft) || definition.options || [];
  const matches =
    !filter ||
    (definition.label + " " + path.join(".") + " " + definition.description)
      .toLowerCase()
      .includes(filter.toLowerCase());
  if (!matches) return null;
  let control;
  switch (definition.type) {
    case "toggle":
      control = (
        <label className="switch-label">
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            onChange={(event) => update(event.target.checked)}
            disabled={!!disabled}
          />
          <span className="switch-track" />
          <span>{value ? "已启用" : "已关闭"}</span>
        </label>
      );
      break;
    case "number":
      control = (
        <div className="number-input">
          <input
            id={id}
            type="number"
            value={typeof value === "number" ? value : ""}
            min={definition.min}
            max={definition.max}
            step={definition.step ?? "any"}
            disabled={!!disabled}
            onChange={(event) =>
              update(
                event.target.value === "" ? null : Number(event.target.value),
              )
            }
          />
          {definition.unit && <span>{definition.unit}</span>}
        </div>
      );
      break;
    case "select":
      control = (
        <Combobox
          id={id}
          label={definition.label}
          value={String(value ?? "")}
          disabled={!!disabled}
          onChange={(next) =>
            update(definition.nullable && next === "" ? null : next)
          }
          placeholder={definition.placeholder}
          options={[
            {
              value: "",
              label:
                definition.placeholder ??
                options.find((option) => option.value === "")?.label ??
                "请选择",
            },
            ...options.filter((option) => option.value !== ""),
          ]}
        />
      );
      break;
    case "list":
      control = (
        <Lines
          id={id}
          value={value}
          onChange={update}
          options={definition.options}
          typed={definition.key === "allowed"}
          disabled={!!disabled}
        />
      );
      break;
    case "map":
      control = <MapInput id={id} value={value} onChange={update} />;
      break;
    case "model":
      control = (
        <ModelInput
          key={String(base[1] || "")}
          id={id}
          value={String(value ?? "")}
          onChange={update}
          providerId={String(base[1] || "")}
          disabled={!!disabled}
        />
      );
      break;
    default:
      control = (
        <div className="inline-input">
          <input
            id={id}
            type={
              definition.type === "secret" && !visible ? "password" : "text"
            }
            autoComplete={definition.type === "secret" ? "off" : undefined}
            value={String(value ?? "")}
            disabled={!!disabled}
            onChange={(event) =>
              update(
                definition.nullable && !event.target.value
                  ? null
                  : event.target.value,
              )
            }
          />
          {definition.type === "secret" && (
            <button
              className="small"
              aria-label={visible ? "隐藏密钥" : "显示密钥"}
              onClick={() => setVisible(!visible)}
            >
              {visible ? "隐藏" : "显示"}
            </button>
          )}
        </div>
      );
  }
  return (
    <div
      className={
        "field span-" +
        (definition.span || 1) +
        (dirty ? " dirty" : "") +
        (error ? " invalid" : "")
      }
      data-field={path.join(".")}
    >
      <div className="field-title">
        <label htmlFor={id}>{definition.label}</label>
        <code>{definition.key}</code>
      </div>
      <fieldset className="field-control" disabled={!!disabled}>
        {control}
      </fieldset>
      <p className="field-description">{disabled || definition.description}</p>
      {error && <p className="field-error">{error.message}</p>}
    </div>
  );
}
export function Section({
  definition,
  base = [],
  filter = "",
}: {
  definition: SectionDef;
  base?: Path;
  filter?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const fields = definition.fields.filter(
    (field) =>
      !filter ||
      (
        field.label +
        " " +
        [...base, field.key].join(".") +
        " " +
        field.description
      )
        .toLowerCase()
        .includes(filter.toLowerCase()),
  );
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fill = () => {
      const children = [...el.children] as HTMLElement[];
      children.forEach((child) => child.style.removeProperty("--fill-span"));
      const rows = new Map<number, HTMLElement[]>();
      children.forEach((child) => {
        const top = child.offsetTop;
        rows.set(top, [...(rows.get(top) || []), child]);
      });
      const style = getComputedStyle(el),
        columns = style.gridTemplateColumns.split(" ").length;
      const gap = Number.parseFloat(style.columnGap) || 0;
      const left = Number.parseFloat(style.paddingLeft) || 0,
        right = Number.parseFloat(style.paddingRight) || 0;
      const cell =
        (el.clientWidth - left - right - gap * (columns - 1)) / columns;
      for (const row of rows.values()) {
        const last = row.at(-1)!;
        const start = Math.round(
          (last.getBoundingClientRect().left -
            el.getBoundingClientRect().left -
            left) /
            (cell + gap),
        );
        const span = Math.max(1, columns - start);
        last.style.setProperty("--fill-span", String(span));
      }
    };
    let frame = 0;
    let lastWidth = -1;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (Math.abs(width - lastWidth) < 0.5) return;
      lastWidth = width;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(fill);
    });
    observer.observe(el);
    frame = requestAnimationFrame(fill);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [fields.map((field) => field.key).join("|")]);
  if (!fields.length) return null;
  return (
    <section className="sec" id={"section-" + definition.id}>
      <header className="sec-head">
        <div>
          <h2>{definition.title}</h2>
          <p>{definition.description}</p>
        </div>
        <code>[{base.join(".") || definition.id}]</code>
      </header>
      <div
        ref={ref}
        className={"sec-body" + (definition.cols === 2 ? " cols-2" : "")}
      >
        {fields.map((field) => (
          <Field key={field.key} definition={field} base={base} />
        ))}
      </div>
    </section>
  );
}
export function FieldGrid({
  fields,
  base,
  values,
  onChange,
}: {
  fields: FieldDef[];
  base: Path;
  values: ObjectValue;
  onChange: (key: string, value: Json) => void;
}) {
  return (
    <div className="sec-body">
      {fields.map((field) => (
        <Field
          key={field.key}
          definition={field}
          base={base}
          valueOverride={values[field.key] ?? null}
          onOverride={(value) => onChange(field.key, value)}
        />
      ))}
    </div>
  );
}
