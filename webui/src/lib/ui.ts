import { useSyncExternalStore } from "react";
interface Prompt {
  title: string;
  message: string;
  input?: string;
  placeholder?: string;
  danger?: boolean;
  confirm: string;
  resolve: (value: string | boolean) => void;
}
export interface FileStatus {
  dirty: boolean;
  saving: boolean;
  error: string;
  conflict: boolean;
  save: () => void;
  resolve: () => void;
}
interface UiState {
  prompt: Prompt | null;
  notice: string;
  fileStatus: FileStatus | null;
}
let state: UiState = { prompt: null, notice: "", fileStatus: null };
const listeners = new Set<() => void>();
const update = (next: Partial<UiState>) => {
  state = { ...state, ...next };
  listeners.forEach((callback) => callback());
};
export function useUi() {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => state,
  );
}
export function notify(message: string) {
  update({ notice: message });
}
export function dismissNotice() {
  update({ notice: "" });
}
export function setFileStatus(fileStatus: FileStatus | null) {
  update({ fileStatus });
}
export function ask(
  title: string,
  message: string,
  options: {
    input?: string;
    placeholder?: string;
    danger?: boolean;
    confirm?: string;
  } = {},
): Promise<string | boolean> {
  return new Promise((resolve) => {
    if (state.prompt) state.prompt.resolve(false);
    update({
      prompt: {
        title,
        message,
        ...options,
        confirm: options.confirm || "确认",
        resolve,
      },
    });
  });
}
export function answer(value: string | boolean) {
  state.prompt?.resolve(value);
  update({ prompt: null });
}
