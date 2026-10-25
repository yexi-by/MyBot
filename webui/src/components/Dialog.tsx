/** 原生 <dialog> 包装：模态、焦点圈定、Esc 与点击遮罩关闭；关闭后保留内容直到退场动画结束。 */

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { cls } from "@/lib/pages";
import { Store, useStore } from "@/lib/store";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  className?: string;
  labelledBy?: string;
  /** false 时 Esc 和点击遮罩都不关闭（电源等待等不可取消的状态）。 */
  dismissible?: boolean;
  children: ReactNode;
}

export function Dialog({ open, onClose, className, labelledBy, dismissible = true, children }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={cls("dlg", className)}
      aria-labelledby={labelledBy}
      onCancel={(event) => {
        event.preventDefault();
        if (dismissible) onClose();
      }}
      onClick={(event) => {
        if (!dismissible || event.target !== ref.current) return;
        const rect = ref.current.getBoundingClientRect();
        const inside =
          event.clientX >= rect.left &&
          event.clientX <= rect.right &&
          event.clientY >= rect.top &&
          event.clientY <= rect.bottom;
        if (!inside) onClose();
      }}
    >
      {children}
    </dialog>
  );
}

/** 关闭后继续展示最后一次的内容，避免退场动画期间内容闪空。 */
export function useLatest<T>(value: T | null): T | null {
  const [latest, setLatest] = useState(value);
  if (value !== null && value !== latest) setLatest(value);
  return value ?? latest;
}

/* ---------- 二次确认 ---------- */

export interface ConfirmRequest {
  title: string;
  body?: ReactNode;
  confirmLabel: string;
  tone?: "danger" | "primary";
  onConfirm: () => void | Promise<void>;
}

const confirmStore = new Store<{ request: ConfirmRequest | null; busy: boolean }>({ request: null, busy: false });

export function confirm(request: ConfirmRequest): void {
  confirmStore.set({ request, busy: false });
}

export function ConfirmHost() {
  const { request, busy } = useStore(confirmStore, (state) => state);
  const shown = useLatest(request);
  const close = () => {
    if (!busy) confirmStore.set({ request: null });
  };
  const run = async () => {
    if (!request) return;
    confirmStore.set({ busy: true });
    try {
      await request.onConfirm();
    } finally {
      confirmStore.set({ request: null, busy: false });
    }
  };
  const danger = shown?.tone !== "primary";
  return (
    <Dialog open={request !== null} onClose={close} className={danger ? "dlg--danger" : undefined} labelledBy="confirm-title">
      {shown ? (
        <>
          <h2 className="dlg-title" id="confirm-title">
            {shown.title}
          </h2>
          {shown.body ? <div className="dlg-body">{shown.body}</div> : null}
          <div className="dlg-actions">
            <button type="button" className="btn" onClick={close} disabled={busy} autoFocus>
              取消
            </button>
            <button type="button" className={cls("btn", danger ? "btn--danger" : "btn--primary")} onClick={() => void run()} disabled={busy}>
              {shown.confirmLabel}
            </button>
          </div>
        </>
      ) : null}
    </Dialog>
  );
}
