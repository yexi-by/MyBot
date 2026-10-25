/** CodeMirror 6 编辑器：.md 用 Markdown 高亮并自动换行，其余（含 toml）是纯文本；颜色全部取主题变量。 */

import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { EditorState, type Extension } from "@codemirror/state";
import {
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  placeholder,
} from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { useEffect, useRef } from "react";

const markdownHighlight = HighlightStyle.define([
  { tag: [tags.heading, tags.heading1, tags.heading2, tags.heading3, tags.heading4, tags.heading5, tags.heading6], class: "tok-heading" },
  { tag: tags.processingInstruction, class: "tok-mark" },
  { tag: tags.strong, class: "tok-strong" },
  { tag: tags.emphasis, class: "tok-em" },
  { tag: [tags.link, tags.url], class: "tok-link" },
  { tag: tags.monospace, class: "tok-code" },
  { tag: tags.quote, class: "tok-quote" },
]);

interface CodeEditorProps {
  /** 变化时重建编辑器（换文件），撤销历史随之清空。 */
  docKey: string;
  value: string;
  isMarkdown: boolean;
  onChange: (value: string) => void;
  label: string;
}

export function CodeEditor({ docKey, value, isMarkdown, onChange, label }: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    if (!host.current) return;
    const extensions: Extension[] = [
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightActiveLine(),
      history(),
      drawSelection(),
      search({ top: true }),
      highlightSelectionMatches(),
      keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
      EditorView.contentAttributes.of({ "aria-label": label, spellcheck: "false" }),
      placeholder(isMarkdown ? "写点什么…（Markdown）" : "空文件"),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) onChangeRef.current(update.state.doc.toString());
      }),
    ];
    if (isMarkdown) extensions.push(markdown(), syntaxHighlighting(markdownHighlight), EditorView.lineWrapping);
    const editor = new EditorView({ state: EditorState.create({ doc: value, extensions }), parent: host.current });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
    // value 只在创建时读取，之后的外部变化走下面的同步分支。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docKey, isMarkdown, label]);

  useEffect(() => {
    const editor = view.current;
    if (!editor) return;
    const current = editor.state.doc.toString();
    if (current !== value) editor.dispatch({ changes: { from: 0, to: current.length, insert: value } });
  }, [value]);

  return <div className="fedit-cm" ref={host} />;
}
