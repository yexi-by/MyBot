import { useRef, useState } from "react";

import { FilterEmpty, IssueBanner, PageHeader } from "@/components/PageHeader";
import { SectionRows } from "@/components/Section";
import { useFieldFilter } from "@/lib/filter";
import { usePageToc } from "@/lib/toc";
import { SYSTEM_ROWS, SYSTEM_SECTIONS } from "@/schema/system";

import { tocFor } from "./common";

const TOC = tocFor(SYSTEM_SECTIONS);

export default function SystemPage() {
  const [filter, setFilter] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const empty = useFieldFilter(root, filter);
  usePageToc(TOC);
  return (
    <div className="page-inner" ref={root}>
      <PageHeader
        page="system"
        kicker="系统"
        table="app · server · napcat · database · storage · logging · plugin_execution"
        title="系统设置"
        desc="服务监听、NapCat 连接、数据库、媒体存储与日志等启动期参数。改动停止 0.8 秒后写入 mybot.toml。"
        notice="本页的改动写入后，要重启进程才生效"
        filter={filter}
        onFilter={setFilter}
      />
      <IssueBanner page="system" />
      <div className="sections">
        <SectionRows rows={SYSTEM_ROWS} />
      </div>
      {empty ? <FilterEmpty query={filter} /> : null}
    </div>
  );
}
