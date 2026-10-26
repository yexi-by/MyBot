import { useEffect, useRef } from "react";
import { EditorState, Compartment } from "@codemirror/state";
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLineGutter,
  drawSelection,
} from "@codemirror/view";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { tags } from "@lezer/highlight";

// 颜色全部取自主题变量，亮暗切换无需重建编辑器。
const highlight = HighlightStyle.define([
  { tag: tags.heading, color: "var(--cm-heading)", fontWeight: "600" },
  { tag: tags.strong, color: "var(--cm-strong)", fontWeight: "600" },
  { tag: tags.emphasis, color: "var(--cm-emphasis)", fontStyle: "italic" },
  { tag: tags.strikethrough, textDecoration: "line-through" },
  { tag: [tags.link, tags.url], color: "var(--cm-link)" },
  { tag: tags.monospace, color: "var(--cm-code)" },
  {
    tag: [tags.meta, tags.processingInstruction, tags.contentSeparator, tags.quote],
    color: "var(--cm-meta)",
  },
]);

const theme = EditorView.theme({
  "&": {
    height: "100%",
    color: "var(--text)",
    backgroundColor: "var(--surface)",
    fontSize: "14px",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.65" },
  ".cm-content": { caretColor: "var(--accent)", padding: "12px 0" },
  ".cm-line": { padding: "0 16px 0 8px" },
  ".cm-gutters": {
    backgroundColor: "var(--surface)",
    color: "var(--text-3)",
    border: "none",
    paddingLeft: "6px",
  },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "var(--accent-soft)" },
  ".cm-selectionMatch": { backgroundColor: "var(--mark)" },
  ".cm-searchMatch": { backgroundColor: "var(--mark)", outline: "1px solid var(--warning)" },
  ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "var(--accent-soft)" },
  ".cm-panels": {
    backgroundColor: "var(--surface-2)",
    color: "var(--text)",
    fontFamily: "var(--font-sans)",
  },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
  ".cm-panel.cm-search": { padding: "8px 12px", fontSize: "13px" },
  ".cm-panel.cm-search label": { fontSize: "13px" },
  ".cm-textfield": {
    height: "28px",
    padding: "0 8px",
    border: "1px solid var(--border-strong)",
    borderRadius: "var(--r)",
    backgroundColor: "var(--surface)",
    color: "var(--text)",
    fontSize: "13px",
  },
  ".cm-button": {
    height: "28px",
    padding: "0 10px",
    border: "1px solid var(--border-strong)",
    borderRadius: "var(--r)",
    backgroundImage: "none",
    backgroundColor: "var(--surface)",
    color: "var(--text)",
    fontSize: "13px",
  },
  ".cm-panel.cm-search [name=close]": { color: "var(--text-3)", fontSize: "18px" },
});

// 搜索面板等内置文案的中文翻译。
const phrases = EditorState.phrases.of({
  Find: "查找",
  Replace: "替换",
  next: "下一个",
  previous: "上一个",
  all: "全部",
  "match case": "区分大小写",
  regexp: "正则",
  "by word": "全词",
  replace: "替换",
  "replace all": "全部替换",
  close: "关闭",
  "current match": "当前匹配",
  "on line": "行",
  "replaced $ matches": "已替换 $ 处",
  "replaced match on line $": "已替换第 $ 行的匹配",
  "Go to line": "跳到行",
  go: "跳转",
});

const languageFor = (markdownMode: boolean) =>
  markdownMode ? [markdown(), syntaxHighlighting(highlight)] : [];

export default function CodeEditor({
  value,
  onChange,
  markdownMode,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  markdownMode: boolean;
  label: string;
}) {
  const host = useRef<HTMLDivElement>(null),
    view = useRef<EditorView | null>(null),
    callback = useRef(onChange),
    language = useRef(new Compartment()),
    syncing = useRef(false);
  callback.current = onChange;
  useEffect(() => {
    if (!host.current) return;
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          drawSelection(),
          history(),
          highlightSelectionMatches(),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorState.lineSeparator.of(value.includes("\r\n") ? "\r\n" : "\n"),
          theme,
          phrases,
          keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
          language.current.of(languageFor(markdownMode)),
          EditorView.updateListener.of((update) => {
            if (update.docChanged && !syncing.current) callback.current(update.state.sliceDoc());
          }),
        ],
      }),
    });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
  }, []);
  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.sliceDoc() !== value) {
      syncing.current = true;
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
      syncing.current = false;
    }
  }, [value]);
  useEffect(() => {
    view.current?.dispatch({ effects: language.current.reconfigure(languageFor(markdownMode)) });
  }, [markdownMode]);
  return <div ref={host} className="code-editor" />;
}
