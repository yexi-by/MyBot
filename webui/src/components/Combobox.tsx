import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CSSProperties, KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, RefreshCw } from "lucide-react";

interface Option {
  value: string;
  label: string;
}
interface ComboboxProps {
  id?: string;
  label: string;
  value: string;
  options: readonly Option[];
  onChange: (value: string) => void;
  disabled?: boolean;
  editable?: boolean;
  placeholder?: string;
  className?: string;
  loading?: boolean;
  error?: string;
  emptyText?: string;
  /** 可编辑模式下输入内容没有匹配候选时的提示。 */
  freeTextHint?: string;
  listLabel?: string;
  onRefresh?: () => void;
  refreshLabel?: string;
  invalid?: boolean;
}
interface Position {
  left: number;
  top: number;
  width: number;
  height: number;
}

function highlighted(label: string, query: string) {
  const start = label.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (!query || start < 0) return label;
  return (
    <>
      {label.slice(0, start)}
      <mark>{label.slice(start, start + query.length)}</mark>
      {label.slice(start + query.length)}
    </>
  );
}

/** 固定选项与自由输入共用主题弹层，保留调用方的输入和选择写回语义。 */
export function Combobox({
  id,
  label,
  value,
  options,
  onChange,
  disabled = false,
  editable = false,
  placeholder = "请选择",
  className = "",
  loading = false,
  error = "",
  emptyText = "没有可选项",
  freeTextHint = "没有匹配项，将使用输入的内容",
  listLabel,
  onRefresh,
  refreshLabel = "刷新列表",
  invalid = false,
}: ComboboxProps) {
  const generatedId = useId(),
    controlId = id || generatedId,
    listId = generatedId + "-list",
    statusId = generatedId + "-status";
  const root = useRef<HTMLSpanElement>(null),
    control = useRef<HTMLInputElement | HTMLButtonElement>(null),
    popup = useRef<HTMLDivElement>(null),
    list = useRef<HTMLDivElement>(null),
    search = useRef({ text: "", at: 0 });
  const [open, setOpen] = useState(false),
    [filter, setFilter] = useState(""),
    [active, setActive] = useState<string | null>(null),
    [position, setPosition] = useState<Position | null>(null);
  const query = filter.trim(),
    visible = useMemo(
      () =>
        editable && query
          ? options.filter((option) =>
              option.label
                .toLocaleLowerCase()
                .includes(query.toLocaleLowerCase()),
            )
          : options,
      [editable, options, query],
    ),
    activeIndex = visible.findIndex((option) => option.value === active),
    selected = options.find((option) => option.value === value),
    expanded = open && !disabled;
  const status = loading
    ? "正在读取…"
    : error ||
      (!visible.length
        ? query && options.length
          ? freeTextHint
          : emptyText
        : "");

  const close = () => {
    setOpen(false);
    setFilter("");
    search.current = { text: "", at: 0 };
  };
  const show = (edge?: "first" | "last") => {
    if (disabled || control.current?.matches(":disabled")) return;
    setFilter("");
    setActive(
      selected?.value ??
        (editable && !edge
          ? null
          : (options.at(edge === "last" ? -1 : 0)?.value ?? null)),
    );
    setOpen(true);
  };
  const choose = (next: string) => {
    if (disabled || control.current?.matches(":disabled")) return;
    close();
    control.current?.focus({ preventScroll: true });
    if (next !== value) onChange(next);
  };

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  useLayoutEffect(() => {
    if (!expanded) {
      setPosition(null);
      return;
    }
    let frame = 0;
    const place = () => {
      const anchor = root.current,
        panel = popup.current;
      if (!anchor || !panel) return;
      const rect = anchor.getBoundingClientRect(),
        viewport = window.visualViewport;
      const viewLeft = viewport?.offsetLeft ?? 0,
        viewTop = viewport?.offsetTop ?? 0;
      const viewWidth = viewport?.width ?? document.documentElement.clientWidth;
      const viewHeight = viewport?.height ?? window.innerHeight;
      if (
        rect.bottom <= viewTop ||
        rect.top >= viewTop + viewHeight ||
        rect.right <= viewLeft ||
        rect.left >= viewLeft + viewWidth
      ) {
        setOpen(false);
        return;
      }
      const margin = 10,
        gap = 6;
      const width = Math.min(
        Math.max(rect.width, editable ? 280 : 180),
        viewWidth - margin * 2,
      );
      const above = Math.max(0, rect.top - viewTop - margin - gap);
      const below = Math.max(
        0,
        viewTop + viewHeight - rect.bottom - margin - gap,
      );
      const headHeight =
        panel.querySelector<HTMLElement>(".choice-head")?.offsetHeight ?? 0;
      const statusHeight =
        panel.querySelector<HTMLElement>(".choice-status")?.offsetHeight ?? 0;
      const desired = Math.min(
        360,
        (list.current?.scrollHeight ?? 0) + headHeight + statusHeight + 2,
      );
      const upwards = below < desired && above > below;
      const height = Math.min(360, upwards ? above : below);
      const next = {
        left: Math.max(
          viewLeft + margin,
          Math.min(rect.left, viewLeft + viewWidth - width - margin),
        ),
        top: upwards
          ? rect.top - gap - Math.min(desired, height)
          : rect.bottom + gap,
        width,
        height,
      };
      setPosition((current) =>
        current &&
        Object.keys(next).every(
          (key) =>
            current[key as keyof Position] === next[key as keyof Position],
        )
          ? current
          : next,
      );
    };
    const schedule = (event?: Event) => {
      if (
        event?.target instanceof Node &&
        popup.current?.contains(event.target)
      )
        return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(place);
    };
    const outside = (event: Event) => {
      if (
        event.target instanceof Node &&
        !root.current?.contains(event.target) &&
        !popup.current?.contains(event.target)
      )
        setOpen(false);
    };
    place();
    const observer = new ResizeObserver(() => schedule());
    if (root.current) observer.observe(root.current);
    if (popup.current) observer.observe(popup.current);
    window.addEventListener("resize", schedule);
    document.addEventListener("scroll", schedule, true);
    window.visualViewport?.addEventListener("resize", schedule);
    window.visualViewport?.addEventListener("scroll", schedule);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", schedule);
      document.removeEventListener("scroll", schedule, true);
      window.visualViewport?.removeEventListener("resize", schedule);
      window.visualViewport?.removeEventListener("scroll", schedule);
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
    };
  }, [expanded, editable, visible.length, status]);

  useLayoutEffect(() => {
    if (!expanded || activeIndex < 0) return;
    const item = list.current?.children[activeIndex] as HTMLElement | undefined;
    const container = list.current;
    if (!item || !container) return;
    // 只滚动候选列表，避免 scrollIntoView 同时带动配置页。
    const itemRect = item.getBoundingClientRect(),
      listRect = container.getBoundingClientRect();
    if (itemRect.top < listRect.top)
      container.scrollTop -= listRect.top - itemRect.top;
    else if (itemRect.bottom > listRect.bottom)
      container.scrollTop += itemRect.bottom - listRect.bottom;
  }, [expanded, activeIndex, position]);

  const keyDown = (
    event: KeyboardEvent<HTMLInputElement | HTMLButtonElement>,
  ) => {
    if (disabled || event.nativeEvent.isComposing) return;
    if (event.key === "Tab") {
      close();
      return;
    }
    if (event.key === "Escape" && expanded) {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      event.stopPropagation();
      if (!expanded) show(event.key === "ArrowUp" ? "last" : "first");
      else {
        const next =
          activeIndex < 0
            ? event.key === "ArrowUp"
              ? visible.length - 1
              : 0
            : Math.max(
                0,
                Math.min(
                  visible.length - 1,
                  activeIndex + (event.key === "ArrowDown" ? 1 : -1),
                ),
              );
        setActive(visible[next]?.value ?? null);
      }
      return;
    }
    if (!editable && (event.key === "Home" || event.key === "End")) {
      event.preventDefault();
      setOpen(true);
      setActive(options.at(event.key === "End" ? -1 : 0)?.value ?? null);
      return;
    }
    if (event.key === "Enter" || (!editable && event.key === " ")) {
      event.preventDefault();
      event.stopPropagation();
      if (!expanded) show();
      else if (activeIndex >= 0) choose(visible[activeIndex].value);
      else close();
      return;
    }
    if (!editable && event.key.length === 1) {
      event.preventDefault();
      event.stopPropagation();
      const now = Date.now(),
        char = event.key.toLocaleLowerCase();
      const previous = now - search.current.at < 750 ? search.current.text : "";
      const text =
        previous && [...previous].every((item) => item === char)
          ? char
          : previous + char;
      search.current = { text, at: now };
      const currentIndex =
        expanded && activeIndex >= 0
          ? activeIndex
          : options.findIndex((option) => option.value === value);
      const start = text.length === 1 ? currentIndex + 1 : 0;
      const ordered = [...options.slice(start), ...options.slice(0, start)];
      const match = ordered.find((option) =>
        option.label.toLocaleLowerCase().startsWith(text),
      );
      if (match) {
        setActive(match.value);
        setOpen(true);
      }
    }
  };
  const aria = {
    id: controlId,
    role: "combobox",
    "aria-label": label,
    "aria-expanded": expanded,
    "aria-controls": expanded ? listId : undefined,
    "aria-haspopup": "listbox" as const,
    "aria-activedescendant":
      expanded && activeIndex >= 0 ? listId + "-" + activeIndex : undefined,
    "aria-describedby": expanded && status ? statusId : undefined,
    "aria-invalid": invalid || undefined,
    disabled,
    onKeyDown: keyDown,
  };
  const popupStyle = position
    ? ({
        "--choice-left": position.left + "px",
        "--choice-top": position.top + "px",
        "--choice-width": position.width + "px",
        "--choice-height": position.height + "px",
      } as CSSProperties)
    : undefined;

  return (
    <span
      ref={root}
      className={"choice " + (editable ? "choice-editable " : "") + className}
    >
      {editable ? (
        <>
          <input
            {...aria}
            ref={(element) => {
              control.current = element;
            }}
            className={"choice-input" + (onRefresh ? " has-refresh" : "")}
            value={value}
            placeholder={placeholder}
            autoComplete="off"
            spellCheck={false}
            aria-autocomplete="list"
            onFocus={() => {
              if (!expanded) show();
            }}
            onClick={() => {
              if (!expanded) show();
            }}
            onChange={(event) => {
              onChange(event.target.value);
              setFilter(event.target.value);
              setActive(null);
              setOpen(true);
            }}
          />
          <span className="choice-actions">
            {onRefresh && (
              <button
                type="button"
                className={"choice-icon" + (loading ? " is-loading" : "")}
                aria-label={refreshLabel}
                title={refreshLabel}
                disabled={disabled || loading}
                onClick={() => {
                  show();
                  onRefresh();
                  control.current?.focus({ preventScroll: true });
                }}
              >
                <RefreshCw size={15} aria-hidden="true" />
              </button>
            )}
            <button
              type="button"
              className="choice-icon"
              tabIndex={-1}
              aria-label={expanded ? "收起候选列表" : "展开候选列表"}
              disabled={disabled}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => {
                if (expanded) close();
                else {
                  control.current?.focus({ preventScroll: true });
                  show();
                }
              }}
            >
              <ChevronDown
                size={16}
                className={"choice-chevron" + (expanded ? " open" : "")}
                aria-hidden="true"
              />
            </button>
          </span>
        </>
      ) : (
        <button
          {...aria}
          type="button"
          className="choice-trigger"
          ref={(element) => {
            control.current = element;
          }}
          onClick={() => (expanded ? close() : show())}
        >
          <span
            className={
              "choice-value" + (!selected && !value ? " placeholder" : "")
            }
          >
            {selected?.label ?? (value || placeholder)}
          </span>
          <ChevronDown
            size={16}
            className={"choice-chevron" + (expanded ? " open" : "")}
            aria-hidden="true"
          />
        </button>
      )}
      {expanded &&
        createPortal(
          <div
            ref={popup}
            className={
              "choice-popup" +
              (editable ? " choice-models" : "") +
              (!position ? " positioning" : "")
            }
            style={popupStyle}
          >
            {(listLabel || editable) && (
              <div className="choice-head">
                <span>{listLabel || label}</span>
                <span>
                  {query
                    ? visible.length + " / " + options.length
                    : options.length + " 项"}
                </span>
              </div>
            )}
            <div
              ref={list}
              className="choice-list"
              role="listbox"
              id={listId}
              aria-label={label + "候选"}
              aria-busy={loading}
            >
              {visible.map((option, index) => (
                <div
                  role="option"
                  id={listId + "-" + index}
                  key={option.value}
                  aria-selected={option.value === value}
                  className={
                    "choice-option" +
                    (index === activeIndex ? " active" : "") +
                    (option.value === value ? " selected" : "")
                  }
                  onPointerDown={(event) => event.preventDefault()}
                  onPointerMove={() => setActive(option.value)}
                  onClick={() => choose(option.value)}
                >
                  <span>
                    {highlighted(option.label, editable ? query : "")}
                  </span>
                  <span className="choice-check" aria-hidden="true">
                    {option.value === value && <Check size={15} />}
                  </span>
                </div>
              ))}
            </div>
            {status && (
              <p
                id={statusId}
                role="status"
                className={
                  "choice-status" + (error && !loading ? " error" : "")
                }
              >
                {status}
              </p>
            )}
          </div>,
          root.current?.closest("dialog") ?? document.body,
        )}
    </span>
  );
}
