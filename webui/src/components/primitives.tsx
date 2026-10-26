import { useId } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { LoaderCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Tone } from "../lib/ui";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "danger-ghost";
export function Button({
  variant = "secondary",
  size = "md",
  icon: Icon,
  loading = false,
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  icon?: LucideIcon;
  loading?: boolean;
}) {
  return (
    <button
      type="button"
      {...props}
      className={["btn", "btn-" + variant, size === "sm" ? "btn-sm" : "", className]
        .filter(Boolean)
        .join(" ")}
      aria-busy={loading || undefined}
    >
      {loading ? (
        <LoaderCircle className="spin" size={16} aria-hidden="true" />
      ) : (
        Icon && <Icon size={16} aria-hidden="true" />
      )}
      {children}
    </button>
  );
}

export function IconButton({
  icon: Icon,
  label,
  size = "md",
  className = "",
  tone,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: LucideIcon;
  label: string;
  size?: "sm" | "md";
  tone?: "danger";
}) {
  return (
    <button
      type="button"
      {...props}
      aria-label={label}
      title={props.title ?? label}
      className={[
        "icon-btn",
        size === "sm" ? "icon-btn-sm" : "",
        tone ? "icon-btn-" + tone : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <Icon size={size === "sm" ? 15 : 17} aria-hidden="true" />
    </button>
  );
}

export function Badge({
  tone = "neutral",
  children,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={"badge badge-" + tone} title={title}>
      {children}
    </span>
  );
}

export function StatusDot({ tone = "neutral", label }: { tone?: Tone; label?: string }) {
  return (
    <span
      className={"dot dot-" + tone}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
  id,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className="switch"
      onClick={() => onChange(!checked)}
    >
      <span className="switch-thumb" />
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  size = "md",
  id,
}: {
  value: T;
  options: readonly { value: T; label: string; count?: number }[];
  onChange: (next: T) => void;
  label: string;
  size?: "sm" | "md";
  id?: string;
}) {
  return (
    <div
      id={id}
      className={"segmented" + (size === "sm" ? " segmented-sm" : "")}
      role="radiogroup"
      aria-label={label}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className={option.value === value ? "active" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.label}
          {option.count !== undefined && <span className="count">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

export interface TabItem<T extends string> {
  id: T;
  label: string;
  count?: string | number;
}
export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
  vertical = false,
}: {
  items: readonly TabItem<T>[];
  value: T;
  onChange: (next: T) => void;
  label: string;
  vertical?: boolean;
}) {
  const base = useId();
  return (
    <div
      className={"tabs" + (vertical ? " tabs-vertical" : "")}
      role="tablist"
      aria-label={label}
      aria-orientation={vertical ? "vertical" : "horizontal"}
      onKeyDown={(event) => {
        const keys = vertical ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
        const step = event.key === keys[0] ? -1 : event.key === keys[1] ? 1 : 0;
        if (!step) return;
        event.preventDefault();
        const index = items.findIndex((item) => item.id === value);
        const next = items[(index + step + items.length) % items.length];
        onChange(next.id);
        document.getElementById(base + next.id)?.focus();
      }}
    >
      {items.map((item) => (
        <button
          key={item.id}
          id={base + item.id}
          type="button"
          role="tab"
          aria-selected={item.id === value}
          tabIndex={item.id === value ? 0 : -1}
          className={item.id === value ? "active" : ""}
          onClick={() => onChange(item.id)}
        >
          {item.label}
          {item.count !== undefined && <span className="tab-count">{item.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  compact = false,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={"empty" + (compact ? " empty-compact" : "")}>
      {Icon && (
        <span className="empty-icon">
          <Icon size={20} aria-hidden="true" />
        </span>
      )}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function Meter({
  value,
  max,
  marker,
  tone = "accent",
  label,
}: {
  value: number;
  max: number;
  marker?: number;
  tone?: Tone;
  label: string;
}) {
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  return (
    <div
      className={"meter meter-" + tone}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.min(value, max)}
    >
      <span className="meter-fill" style={{ width: ratio * 100 + "%" }} />
      {marker !== undefined && max > 0 && (
        <span className="meter-marker" style={{ left: (marker / max) * 100 + "%" }} />
      )}
    </div>
  );
}

export function Banner({
  tone = "accent",
  icon: Icon,
  title,
  children,
  actions,
}: {
  tone?: Tone;
  icon?: LucideIcon;
  title?: string;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={"banner banner-" + tone} role={tone === "danger" ? "alert" : "status"}>
      {Icon && <Icon className="banner-icon" size={17} aria-hidden="true" />}
      <div className="banner-text">
        {title && <strong>{title}</strong>}
        {children && <span>{children}</span>}
      </div>
      {actions && <div className="banner-actions">{actions}</div>}
    </div>
  );
}

export function Spinner({ label = "正在加载" }: { label?: string }) {
  return (
    <span className="spinner" role="status">
      <LoaderCircle className="spin" size={16} aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd>{children}</kbd>;
}
