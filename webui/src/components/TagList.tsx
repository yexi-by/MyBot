/** 列表字段：回车或逗号添加，退格删除最后一项；群号与数字列表支持一次粘贴多项。 */

import { X } from "lucide-react";
import { useRef, useState } from "react";

import type { FieldDef } from "@/schema/types";

interface TagListProps {
  id: string;
  field: FieldDef;
  value: unknown;
  onChange: (value: unknown[]) => void;
  disabled?: boolean;
  describedBy?: string;
}

function parseNumber(raw: string): number | null {
  const text = raw.trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(text)) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export function TagList({ id, field, value, onChange, disabled, describedBy }: TagListProps) {
  const items = Array.isArray(value) ? value : [];
  const [text, setText] = useState("");
  const [hint, setHint] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const multi = field.kind !== "tags";

  const add = (raw: string): boolean => {
    const parts = (multi ? raw.split(/[\s,，;；、]+/) : [raw]).map((part) => part.trim()).filter(Boolean);
    if (parts.length === 0) return false;
    const next: unknown[] = [...items];
    const rejected: string[] = [];
    for (const part of parts) {
      if (field.kind === "numtags") {
        const number = parseNumber(part);
        if (number === null) {
          rejected.push(part);
          continue;
        }
        next.push(number);
        continue;
      }
      const exists = next.some((item) =>
        field.kind === "ids" ? String(item) === part : String(item).toLowerCase() === part.toLowerCase(),
      );
      if ((field.kind === "ids" || field.unique) && exists) {
        rejected.push(part);
        continue;
      }
      next.push(part);
    }
    if (next.length !== items.length) onChange(next);
    if (rejected.length > 0) {
      setHint(field.kind === "numtags" ? `「${rejected.join("、")}」不是数字` : `「${rejected.join("、")}」已在列表里`);
      setText(rejected.join(" "));
      return false;
    }
    setHint("");
    setText("");
    return true;
  };

  const removeAt = (index: number) => {
    onChange(items.filter((_, i) => i !== index));
    input.current?.focus();
  };

  return (
    <>
      <div
        className="ctl ctl-tags"
        onClick={(event) => {
          if (event.target === event.currentTarget) input.current?.focus();
        }}
      >
        {items.map((item, index) => (
          <span className="tag" key={`${index}-${String(item)}`}>
            <span className="tag-text" title={String(item)}>
              {String(item)}
            </span>
            <button
              type="button"
              className="tag-x"
              aria-label={`移除 ${String(item)}`}
              disabled={disabled}
              onClick={() => removeAt(index)}
            >
              <X className="ic" aria-hidden />
            </button>
          </span>
        ))}
        <input
          ref={input}
          id={id}
          className="tag-input"
          type="text"
          value={text}
          disabled={disabled}
          spellCheck={false}
          autoComplete="off"
          inputMode={field.kind === "numtags" ? "decimal" : field.kind === "ids" ? "numeric" : undefined}
          aria-describedby={describedBy}
          placeholder={items.length === 0 ? (field.placeholder ? `添加，如 ${field.placeholder}` : (field.addLabel ?? "添加")) : ""}
          onChange={(event) => {
            setText(event.target.value);
            if (hint) setHint("");
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || (multi && (event.key === "," || event.key === "，"))) {
              event.preventDefault();
              add(text);
            } else if (event.key === "Backspace" && text === "" && items.length > 0) {
              event.preventDefault();
              onChange(items.slice(0, -1));
            }
          }}
          onPaste={(event) => {
            if (!multi) return;
            const pasted = event.clipboardData.getData("text");
            if (/[\s,，;；、]/.test(pasted.trim())) {
              event.preventDefault();
              add(text + pasted);
            }
          }}
          onBlur={() => {
            if (text.trim()) add(text);
          }}
        />
      </div>
      {hint ? <p className="fld-warn">{hint}</p> : null}
    </>
  );
}
