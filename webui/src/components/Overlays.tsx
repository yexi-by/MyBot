import { useEffect, useState } from "react";
import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { dismissToast, settleDialog, useUi } from "../lib/ui";
import { Dialog } from "./Dialog";
import { Button, IconButton } from "./primitives";

/** 由 lib/ui 的 confirm / prompt 驱动的全局对话框。 */
export function GlobalDialog() {
  const { dialog } = useUi();
  const [value, setValue] = useState("");
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    setValue(dialog?.kind === "prompt" ? dialog.initial : "");
    setTouched(false);
  }, [dialog]);
  const error = dialog?.kind === "prompt" ? dialog.validate(value.trim()) : "";
  const submit = () => {
    if (!dialog) return;
    if (dialog.kind === "confirm") settleDialog(true);
    else {
      setTouched(true);
      if (value.trim() && !error) settleDialog(value.trim());
    }
  };
  return (
    <Dialog
      open={!!dialog}
      size="sm"
      title={dialog?.title ?? ""}
      onClose={() => settleDialog(null)}
      footer={
        <>
          {/* 危险操作默认聚焦“取消”，避免回车误确认。 */}
          <Button
            onClick={() => settleDialog(null)}
            data-autofocus={dialog?.kind === "confirm" && dialog.danger ? true : undefined}
          >
            取消
          </Button>
          <Button
            variant={dialog?.kind === "confirm" && dialog.danger ? "danger" : "primary"}
            onClick={submit}
            data-autofocus={dialog?.kind === "confirm" && !dialog.danger ? true : undefined}
          >
            {dialog?.confirmLabel}
          </Button>
        </>
      }
    >
      {dialog?.message && <p className="dialog-message">{dialog.message}</p>}
      {dialog?.kind === "prompt" && (
        <label className="field">
          <span className="field-label">{dialog.label}</span>
          <input
            className="input"
            value={value}
            placeholder={dialog.placeholder}
            aria-invalid={touched && !!error}
            onChange={(event) => setValue(event.target.value)}
            onBlur={() => setTouched(true)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submit();
              }
            }}
          />
          {touched && error && <span className="field-error">{error}</span>}
        </label>
      )}
    </Dialog>
  );
}

const icons = { success: CircleCheck, danger: CircleAlert, neutral: Info };
export function Toasts() {
  const { toasts } = useUi();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((item) => {
        const Icon = icons[item.tone];
        return (
          <div key={item.id} className={"toast toast-" + item.tone} role="status">
            <Icon size={16} aria-hidden="true" />
            <span className="grow">{item.message}</span>
            <IconButton icon={X} size="sm" label="关闭通知" onClick={() => dismissToast(item.id)} />
          </div>
        );
      })}
    </div>
  );
}
