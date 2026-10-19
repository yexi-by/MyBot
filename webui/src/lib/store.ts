/** 极简外部状态容器：组件按选择器订阅，选择结果不变时不重渲染。 */

import { useRef, useSyncExternalStore } from "react";

export class Store<S extends object> {
  private state: S;
  private readonly listeners = new Set<() => void>();

  constructor(initial: S) {
    this.state = initial;
  }

  get = (): S => this.state;

  set = (patch: Partial<S> | ((state: S) => Partial<S>)): void => {
    const next = typeof patch === "function" ? patch(this.state) : patch;
    this.state = { ...this.state, ...next };
    for (const listener of this.listeners) listener();
  };

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
}

export function useStore<S extends object, T>(
  store: Store<S>,
  selector: (state: S) => T,
  isEqual: (a: T, b: T) => boolean = Object.is,
): T {
  const cache = useRef<{ state: S; selector: (state: S) => T; value: T } | null>(null);
  const getSnapshot = (): T => {
    const state = store.get();
    const cached = cache.current;
    if (cached && cached.state === state && cached.selector === selector) return cached.value;
    const value = selector(state);
    if (cached && isEqual(cached.value, value)) {
      cache.current = { state, selector, value: cached.value };
      return cached.value;
    }
    cache.current = { state, selector, value };
    return value;
  };
  return useSyncExternalStore(store.subscribe, getSnapshot, getSnapshot);
}

export function shallowArrayEqual<T>(a: readonly T[], b: readonly T[]): boolean {
  return a.length === b.length && a.every((item, index) => Object.is(item, b[index]));
}
