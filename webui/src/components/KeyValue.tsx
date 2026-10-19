/**
 * 环境变量与请求头的键值编辑器：本地维护带稳定 id 的行，允许空名和重名行暂时共存；
 * 写回配置时才合并成表（重名时后者覆盖前者），没有任何行时删除整个键。
 */

import { Eye, EyeOff, Plus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";

import { isRecord } from "@/lib/path";

interface Entry {
  id: number;
  key: string;
  value: string;
}

interface KeyValueProps {
  id: string;
  label: string;
  value: unknown;
  onChange: (value: Record<string, string> | null) => void;
  secretValues?: boolean;
  addLabel?: string;
  keyPlaceholder?: string;
}

function toRecord(entries: Entry[]): Record<string, string> | null {
  return entries.length > 0 ? Object.fromEntries(entries.map((entry) => [entry.key, entry.value])) : null;
}

function serialize(value: unknown): string {
  return JSON.stringify(isRecord(value) && Object.keys(value).length > 0 ? value : null);
}

export function KeyValue({ id, label, value, onChange, secretValues, addLabel, keyPlaceholder }: KeyValueProps) {
  const seq = useRef(0);
  const fromValue = (record: unknown): Entry[] =>
    isRecord(record)
      ? Object.entries(record).map(([key, item]) => ({ id: seq.current++, key, value: String(item ?? "") }))
      : [];
  const [entries, setEntries] = useState<Entry[]>(() => fromValue(value));
  const [revealed, setRevealed] = useState<Set<number>>(() => new Set());
  const [synced, setSynced] = useState(() => serialize(value));

  // 外部变化（丢弃修改、保存后规范化）时重新同步；本地写回产生的变化不会自我覆盖。
  const incoming = serialize(value);
  if (incoming !== synced) {
    setSynced(incoming);
    if (serialize(toRecord(entries)) !== incoming) setEntries(fromValue(value));
  }

  const commit = (next: Entry[]) => {
    setEntries(next);
    const record = toRecord(next);
    setSynced(serialize(record));
    onChange(record);
  };

  const duplicates = new Set(
    entries.map((entry) => entry.key).filter((key, index, all) => key !== "" && all.indexOf(key) !== index),
  );
  const emptyKeys = entries.some((entry) => entry.key.trim() === "");

  return (
    <div className="kv" id={id}>
      {entries.length === 0 ? <p className="kv-empty">还没有{label}。</p> : null}
      {entries.map((entry, index) => {
        const masked = secretValues && !revealed.has(entry.id);
        return (
          <div className="kv-row" key={entry.id}>
            <input
              className="inp is-mono"
              aria-label={`${label}名称 ${index + 1}`}
              value={entry.key}
              placeholder={keyPlaceholder ?? "名称"}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) =>
                commit(entries.map((item) => (item.id === entry.id ? { ...item, key: event.target.value } : item)))
              }
            />
            <div className={secretValues ? "ctl has-btn" : "ctl"}>
              <input
                className="inp is-mono"
                type={masked ? "password" : "text"}
                aria-label={`${label}值 ${index + 1}`}
                value={entry.value}
                placeholder="值"
                spellCheck={false}
                autoComplete="off"
                onChange={(event) =>
                  commit(entries.map((item) => (item.id === entry.id ? { ...item, value: event.target.value } : item)))
                }
              />
              {secretValues ? (
                <div className="ctl-btns">
                  <button
                    type="button"
                    className="ctl-btn"
                    aria-label={masked ? "显示值" : "隐藏值"}
                    title={masked ? "显示值" : "隐藏值"}
                    onClick={() =>
                      setRevealed((current) => {
                        const next = new Set(current);
                        if (next.has(entry.id)) next.delete(entry.id);
                        else next.add(entry.id);
                        return next;
                      })
                    }
                  >
                    {masked ? <Eye className="ic" aria-hidden /> : <EyeOff className="ic" aria-hidden />}
                  </button>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              className="icon-btn icon-btn--danger icon-btn--small"
              aria-label={`删除${label} ${entry.key || index + 1}`}
              title="删除这一行"
              onClick={() => commit(entries.filter((item) => item.id !== entry.id))}
            >
              <Trash2 className="ic" aria-hidden />
            </button>
          </div>
        );
      })}
      <div className="kv-foot">
        <button
          type="button"
          className="btn btn--small"
          onClick={() => commit([...entries, { id: seq.current++, key: "", value: "" }])}
        >
          <Plus className="ic" aria-hidden />
          {addLabel ?? `添加${label}`}
        </button>
        {duplicates.size > 0 ? (
          <span className="fld-warn">重名的{label}（{[...duplicates].join("、")}）保存时后者覆盖前者</span>
        ) : emptyKeys ? (
          <span className="fld-warn">名称为空的行也会写入，用不到请删除</span>
        ) : null}
      </div>
    </div>
  );
}
