/** Markdown 编辑器：CodeMirror 6 + markdown 语法高亮，跟随亮暗主题。 */

import CodeMirror from "@uiw/react-codemirror";
import {
  defineLanguageFacet,
  languageDataProp,
  Language,
  LanguageSupport,
} from "@codemirror/language";
import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView } from "@codemirror/view";
import { GFM, parser } from "@lezer/markdown";
import { useTheme } from "next-themes";

interface MarkdownEditorProps {
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
}

const markdownData = defineLanguageFacet({
  commentTokens: { block: { open: "<!--", close: "-->" } },
});
const markdownParser = parser.configure([
  GFM,
  { props: [languageDataProp.add({ Document: markdownData })] },
]);
const markdownLanguage = new Language(
  markdownData,
  markdownParser,
  [],
  "markdown",
);
const MARKDOWN_EXTENSIONS = [
  new LanguageSupport(markdownLanguage),
  EditorView.lineWrapping,
];

export function MarkdownEditor({
  value,
  onChange,
  ariaLabel,
}: MarkdownEditorProps) {
  const { resolvedTheme } = useTheme();
  return (
    <CodeMirror
      aria-label={ariaLabel}
      value={value}
      onChange={onChange}
      height="100%"
      theme={resolvedTheme === "dark" ? oneDark : "light"}
      extensions={MARKDOWN_EXTENSIONS}
      basicSetup={{
        lineNumbers: false,
        foldGutter: false,
        highlightActiveLine: false,
        highlightActiveLineGutter: false,
      }}
      style={{ height: "100%", fontSize: "0.875rem" }}
    />
  );
}
