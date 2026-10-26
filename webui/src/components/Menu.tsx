import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { Ellipsis } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface MenuItem {
  label: string;
  icon?: LucideIcon;
  danger?: boolean;
  disabled?: boolean;
  onSelect: () => void;
}

/** 溢出菜单：方向键移动，回车选择，Esc 或点击外部关闭。 */
export function Menu({
  items,
  label = "更多操作",
  trigger,
}: {
  items: MenuItem[];
  label?: string;
  trigger?: (props: { onClick: () => void; "aria-expanded": boolean }) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const anchor = useRef<HTMLSpanElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const enabled = items.map((item, index) => (item.disabled ? -1 : index)).filter((i) => i >= 0);

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    const place = () => {
      const rect = anchor.current?.getBoundingClientRect();
      const width = panel.current?.offsetWidth ?? 200;
      const height = panel.current?.offsetHeight ?? 0;
      if (!rect) return;
      const below = rect.bottom + 4 + height <= window.innerHeight - 8;
      setPosition({
        top: below ? rect.bottom + 4 : Math.max(8, rect.top - 4 - height),
        left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
      });
    };
    place();
    window.addEventListener("resize", place);
    document.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      document.removeEventListener("scroll", place, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      if (
        event.target instanceof Node &&
        !anchor.current?.contains(event.target) &&
        !panel.current?.contains(event.target)
      )
        setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    requestAnimationFrame(() => panel.current?.focus());
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  const close = () => {
    setOpen(false);
    anchor.current?.querySelector("button")?.focus();
  };
  const toggle = () => {
    setActive(enabled[0] ?? 0);
    setOpen((value) => !value);
  };
  return (
    <span ref={anchor} className="menu-anchor">
      {trigger ? (
        trigger({ onClick: toggle, "aria-expanded": open })
      ) : (
        <button
          type="button"
          className="icon-btn"
          aria-label={label}
          title={label}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? id : undefined}
          onClick={toggle}
        >
          <Ellipsis size={17} aria-hidden="true" />
        </button>
      )}
      {open &&
        createPortal(
          <div
            ref={panel}
            id={id}
            role="menu"
            tabIndex={-1}
            aria-label={label}
            className={"menu" + (position ? "" : " menu-positioning")}
            style={position ? { top: position.top, left: position.left } : undefined}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                close();
              } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const at = enabled.indexOf(active);
                const step = event.key === "ArrowDown" ? 1 : -1;
                setActive(enabled[(at + step + enabled.length) % enabled.length] ?? 0);
              } else if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                const item = items[active];
                if (item && !item.disabled) {
                  close();
                  item.onSelect();
                }
              } else if (event.key === "Tab") setOpen(false);
            }}
          >
            {items.map((item, index) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.label}
                  type="button"
                  role="menuitem"
                  tabIndex={-1}
                  disabled={item.disabled}
                  className={
                    "menu-item" +
                    (index === active ? " active" : "") +
                    (item.danger ? " danger" : "")
                  }
                  onPointerMove={() => !item.disabled && setActive(index)}
                  onClick={() => {
                    close();
                    item.onSelect();
                  }}
                >
                  {Icon && <Icon size={15} aria-hidden="true" />}
                  {item.label}
                </button>
              );
            })}
          </div>,
          anchor.current?.closest("dialog") ?? document.body,
        )}
    </span>
  );
}
