/** GFM 预览；按需懒加载，输入时用 useDeferredValue 让编辑器保持跟手。 */

import { useDeferredValue } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function MarkdownPreview({ content }: { content: string }) {
  const deferred = useDeferredValue(content);
  return (
    <article className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{deferred}</ReactMarkdown>
    </article>
  );
}
