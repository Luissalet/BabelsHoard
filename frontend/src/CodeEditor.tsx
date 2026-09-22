import { useRef } from "react";
import type { Finding } from "./types";

/** A textarea with a line-number gutter; lines with findings get a marker. */
export function CodeEditor({
  value,
  onChange,
  findings,
  placeholder,
  onSubmit,
}: {
  value: string;
  onChange: (v: string) => void;
  findings: Finding[];
  placeholder?: string;
  onSubmit?: () => void;
}) {
  const gutter = useRef<HTMLDivElement>(null);
  const lines = Math.max(value.split("\n").length, 12);
  const severityByLine = new Map<number, "error" | "warning">();
  for (const f of findings) {
    if (severityByLine.get(f.line) !== "error") severityByLine.set(f.line, f.severity);
  }
  return (
    <div className="code-editor">
      <div className="code-gutter" ref={gutter} aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => {
          const sev = severityByLine.get(i + 1);
          return (
            <div key={i} className={`code-gutter-line${sev ? ` has-${sev}` : ""}`}>
              {i + 1}
            </div>
          );
        })}
      </div>
      <textarea
        className="code-input"
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        wrap="off"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop;
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && onSubmit) {
            e.preventDefault();
            onSubmit();
          }
        }}
      />
    </div>
  );
}
