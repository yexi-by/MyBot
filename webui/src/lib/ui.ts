/** 跨组件的界面开关：命令面板、窄屏侧栏抽屉。 */

import { Store, useStore } from "./store";

export const uiStore = new Store<{ palette: boolean; menu: boolean }>({ palette: false, menu: false });

export function openPalette(): void {
  uiStore.set({ palette: true, menu: false });
}

export function closePalette(): void {
  uiStore.set({ palette: false });
}

export function togglePalette(): void {
  uiStore.set((state) => ({ palette: !state.palette, menu: false }));
}

export function setMenu(menu: boolean): void {
  uiStore.set({ menu });
}

export function useUi<T>(selector: (state: { palette: boolean; menu: boolean }) => T): T {
  return useStore(uiStore, selector);
}
