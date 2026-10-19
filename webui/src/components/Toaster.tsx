import { CircleAlert, CircleCheck, Info, X } from "lucide-react";

import { useStore } from "@/lib/store";
import { dismissToast, toastStore } from "@/lib/toast";

const ICONS = { info: Info, success: CircleCheck, error: CircleAlert } as const;

export function Toaster() {
  const items = useStore(toastStore, (state) => state.items);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((item) => {
        const Icon = ICONS[item.tone];
        return (
          <div key={item.id} className={`toast toast--${item.tone}`}>
            <Icon className="ic" aria-hidden />
            <span>{item.message}</span>
            {item.action ? (
              <button
                type="button"
                className="toast-action"
                onClick={() => {
                  item.action?.run();
                  dismissToast(item.id);
                }}
              >
                {item.action.label}
              </button>
            ) : null}
            <button type="button" className="toast-x" aria-label="关闭提示" onClick={() => dismissToast(item.id)}>
              <X className="ic" aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
