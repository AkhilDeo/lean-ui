'use client';

import { useRef, useCallback } from 'react';
import Editor, { OnMount } from '@monaco-editor/react';
import type { editor } from 'monaco-editor';

interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
}

export function CodeEditor({ value, onChange, readOnly = false }: CodeEditorProps) {
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);

  const handleEditorDidMount: OnMount = useCallback((editor) => {
    editorRef.current = editor;
  }, []);

  const handleChange = useCallback(
    (value: string | undefined) => {
      onChange(value || '');
    },
    [onChange]
  );

  return (
    <div className="h-full w-full rounded-lg overflow-hidden border border-border">
      <Editor
        height="100%"
        defaultLanguage="lean4"
        language="lean4"
        value={value}
        onChange={handleChange}
        onMount={handleEditorDidMount}
        theme="vs-dark"
        options={{
          minimap: { enabled: false },
          fontSize: 14,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 2,
          wordWrap: 'on',
          readOnly,
          padding: { top: 16, bottom: 16 },
          fontFamily: 'JetBrains Mono, Menlo, Monaco, Consolas, monospace',
          fontLigatures: true,
        }}
      />
    </div>
  );
}
