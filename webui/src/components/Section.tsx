/** 分节卡片：标题 + TOML 表头 + 重启标记 / 可选开关 + 字段网格。 */

import { RotateCw } from "lucide-react";
import { useRef, type ReactNode } from "react";

import { config, useConfig } from "@/lib/config";
import { useGridFill } from "@/lib/gridFill";
import { cls } from "@/lib/pages";
import { getIn, pathKey, tomlPath } from "@/lib/path";
import type { Path } from "@/lib/types";
import type { FieldDef, SectionDef } from "@/schema/types";

import { Field, Switch } from "./Field";

interface SectionProps {
  section: SectionDef;
  /** 动态条目（provider、MCP 服务）的实际路径；必须是稳定引用。 */
  base?: Path;
  fields?: FieldDef[];
  title?: string;
  titleMono?: boolean;
  table?: string;
  /** DOM id 后缀，动态条目用来区分。 */
  domId?: string;
  aside?: ReactNode;
  className?: string;
  children?: ReactNode;
  footer?: ReactNode;
}

export function tableHeader(base: Path): string {
  return `[${tomlPath(base)}]`;
}

export function Section({
  section,
  base = section.base,
  fields = section.fields,
  title = section.title,
  titleMono,
  table,
  domId = section.id,
  aside,
  className,
  children,
  footer,
}: SectionProps) {
  const optional = section.optional;
  const on = useConfig((state) => (optional ? getIn(state.draft, base) != null : true));
  const lockReason = useConfig((state) => (optional?.disabled ? optional.disabled(state.draft) : false));
  const header = table ?? section.table ?? tableHeader(base);
  const bodyRef = useRef<HTMLDivElement>(null);
  useGridFill(bodyRef, on);
  return (
    <section
      className={cls("sec", section.cols === 2 && "sec--cols2", optional && !on && "is-off", className)}
      id={`sec-${domId}`}
      data-section={domId}
      data-section-path={pathKey(base)}
      data-search={`${title} ${section.desc ?? ""} ${header}`.toLowerCase()}
      aria-labelledby={`sec-${domId}-title`}
    >
      <header className="sec-head">
        <h2 className={cls("sec-title", titleMono && "is-mono")} id={`sec-${domId}-title`} title={title}>
          {title}
        </h2>
        <code className="sec-path" title={header}>
          {header}
        </code>
        {section.restart ? (
          <span className="sec-badge">
            <RotateCw className="ic" aria-hidden />
            重启后生效
          </span>
        ) : null}
        {optional ? (
          <span className="sec-aside">
            <span>{on ? (lockReason ? "必须保留" : "已开启") : "已关闭"}</span>
            <Switch
              checked={on}
              label={optional.switchLabel}
              disabled={lockReason !== false}
              title={lockReason || undefined}
              onChange={(checked) => config.update(checked ? optional.enable : optional.disable, base)}
            />
          </span>
        ) : aside ? (
          <span className="sec-aside">{aside}</span>
        ) : null}
        {section.desc ? <p className="sec-desc">{section.desc}</p> : null}
      </header>
      {on ? (
        <div className="sec-body" ref={bodyRef}>
          {fields.map((field) => (
            <Field key={field.key} field={field} base={base} />
          ))}
          {children}
        </div>
      ) : (
        <p className="sec-off">{optional?.offText}</p>
      )}
      {footer}
    </section>
  );
}

export function SectionRows({ rows }: { rows: SectionDef[][] }) {
  return (
    <>
      {rows.map((row) => (
        <div key={row.map((section) => section.id).join("+")} className={cls("sec-row", row.length > 1 && "sec-row--pair")}>
          {row.map((section) => (
            <Section key={section.id} section={section} />
          ))}
        </div>
      ))}
    </>
  );
}
