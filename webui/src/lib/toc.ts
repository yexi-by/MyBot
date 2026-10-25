/** 页面向右栏登记的本页目录；目录项的改动 / 错误标记按配置路径前缀计算。 */

import { useEffect } from "react";

import { Store } from "./store";
import type { Path } from "./types";

export interface TocItem {
  id: string;
  title: string;
  prefixes: Path[];
}

export const tocStore = new Store<{ items: TocItem[] }>({ items: [] });

export function usePageToc(items: TocItem[]): void {
  const signature = JSON.stringify(items);
  useEffect(() => {
    tocStore.set({ items });
    // 签名不变时不重复登记。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
}
