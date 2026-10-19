/** 页面共用：从分节定义生成右栏目录项。 */

import type { TocItem } from "@/lib/toc";
import type { Path } from "@/lib/types";
import { fieldPath, type SectionDef } from "@/schema/types";

export function tocFor(sections: SectionDef[]): TocItem[] {
  return sections.map((section) => {
    const prefixes: Path[] = section.fields.map((field) => fieldPath(section.base, field));
    // 可选分节整段开关时，改动落在分节根路径上。
    if (section.optional) prefixes.push(section.base);
    return { id: section.id, title: section.title, prefixes };
  });
}
