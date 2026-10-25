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
import {
  defaultHighlightStyle,
  syntaxHighlighting,
} from "@codemirror/language";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";

export default function CodeEditor({
  value,
  onChange,
  markdownMode,
  onSave,
}: {
  value: string;
  onChange: (value: string) => void;
  markdownMode: boolean;
  onSave: () => void;
}) {
  const host = useRef<HTMLDivElement>(null),
    view = useRef<EditorView | null>(null),
    callbacks = useRef({ onChange, onSave }),
    language = useRef(new Compartment()),
    syncing = useRef(false);
  callbacks.current = { onChange, onSave };
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
          EditorState.lineSeparator.of(value.includes("\r\n") ? "\r\n" : "\n"),
          keymap.of([
            {
              key: "Mod-s",
              run: () => {
                callbacks.current.onSave();
                return true;
              },
            },
            ...defaultKeymap,
            ...historyKeymap,
            ...searchKeymap,
            indentWithTab,
          ]),
          language.current.of(
            markdownMode
              ? [markdown(), syntaxHighlighting(defaultHighlightStyle)]
              : [],
          ),
          EditorView.updateListener.of((update) => {
            if (update.docChanged && !syncing.current)
              callbacks.current.onChange(update.state.sliceDoc());
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
      editor.dispatch({
        changes: { from: 0, to: editor.state.doc.length, insert: value },
      });
      syncing.current = false;
    }
  }, [value]);
  useEffect(() => {
    view.current?.dispatch({
      effects: language.current.reconfigure(
        markdownMode
          ? [markdown(), syntaxHighlighting(defaultHighlightStyle)]
          : [],
      ),
    });
  }, [markdownMode]);
  return <div ref={host} className="code-editor" aria-label="文件编辑器" />;
}
