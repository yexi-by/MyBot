import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { IconButton } from "./primitives";

/** 基于原生 dialog 的模态框；variant="drawer" 时从右侧滑出。 */
export function Dialog({
  open,
  title,
  description,
  children,
  footer,
  onClose,
  size = "md",
  variant = "modal",
  className = "",
  initialFocus = true,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  size?: "sm" | "md" | "lg";
  variant?: "modal" | "drawer";
  className?: string;
  initialFocus?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      if (initialFocus)
        requestAnimationFrame(() =>
          dialog
            .querySelector<HTMLElement>(
              "[data-autofocus], input:not([type=checkbox]):not([disabled]), textarea",
            )
            ?.focus(),
        );
    }
    if (!open && dialog.open) dialog.close();
  }, [open, initialFocus]);
  return (
    <dialog
      ref={ref}
      className={["dialog", "dialog-" + variant, "dialog-" + size, className].join(" ")}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onMouseDown={(event) => {
        // 只在遮罩上按下时关闭，避免在输入框里拖选文字时误关。
        if (event.target === event.currentTarget) {
          const rect = event.currentTarget.getBoundingClientRect();
          if (
            event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom
          )
            onClose();
        }
      }}
    >
      {open && (
        <>
          <header className="dialog-header">
            <div className="grow">
              <h2>{title}</h2>
              {description && <p className="dialog-description">{description}</p>}
            </div>
            <IconButton icon={X} label="关闭" onClick={onClose} />
          </header>
          {children !== undefined && <div className="dialog-body">{children}</div>}
          {footer && <footer className="dialog-footer">{footer}</footer>}
        </>
      )}
    </dialog>
  );
}
