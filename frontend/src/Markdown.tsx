import type { ReactNode } from "react";

/**
 * A deliberately small renderer for docstrings and docset sections:
 * Markdown (headings, fences, lists, inline code, bold/italic, links) plus
 * the reStructuredText/numpydoc habits docstrings use (underlined headings,
 * `::` literal blocks, `>>>` examples, ``double backticks``).
 * Builds React elements only - no HTML from the index is ever injected.
 */
type Block =
  | { kind: "h"; text: string }
  | { kind: "code"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "p"; text: string };

const UNDERLINE = /^\s*([-=~^"'`#*+])\1{2,}\s*$/;

function parse(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: "p", text: para.join(" ") });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    const fence = trimmed.match(/^(```|~~~)/);
    if (fence) {
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    if (trimmed.startsWith(">>>")) {
      flush();
      const body: string[] = [];
      while (i < lines.length && lines[i].trim() !== "") body.push(lines[i++].trim());
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    if (trimmed && i + 1 < lines.length && UNDERLINE.test(lines[i + 1]) && lines[i + 1].trim().length >= trimmed.length) {
      flush();
      blocks.push({ kind: "h", text: trimmed });
      i++;
      continue;
    }
    const admonition = trimmed.match(/^(?:!!!|\?\?\?\+?)\s*(\w+)(?:\s+"([^"]*)")?/);
    if (admonition) {
      flush();
      blocks.push({ kind: "h", text: admonition[2] || admonition[1] });
      continue;
    }
    const atx = trimmed.match(/^#{1,6}\s+(.*)$/);
    if (atx) {
      flush();
      blocks.push({ kind: "h", text: atx[1] });
      continue;
    }
    if (/^[-*+]\s+/.test(trimmed)) {
      flush();
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].trim().replace(/^[-*+]\s+/, ""));
        i++;
      }
      i--;
      blocks.push({ kind: "list", items });
      continue;
    }
    if (trimmed === "") {
      flush();
      continue;
    }
    if (trimmed.endsWith("::") && i + 1 < lines.length) {
      para.push(trimmed.replace(/::$/, ":"));
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && lines[i].trim() === "") i++;
      while (i < lines.length && (lines[i].startsWith("    ") || lines[i].startsWith("\t") || lines[i].trim() === "")) {
        body.push(lines[i]);
        i++;
      }
      i--;
      if (body.length) blocks.push({ kind: "code", text: dedent(body.join("\n")).trimEnd() });
      continue;
    }
    para.push(trimmed);
  }
  flush();
  return blocks;
}

function dedent(text: string): string {
  const lines = text.split("\n");
  const indents = lines.filter((l) => l.trim()).map((l) => l.match(/^\s*/)![0].length);
  const min = indents.length ? Math.min(...indents) : 0;
  return lines.map((l) => l.slice(min)).join("\n");
}

const INLINE = /(``[^`]+``|`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)\s]+\))/g;

function inline(text: string): ReactNode[] {
  const parts = text.split(INLINE);
  return parts.map((part, i) => {
    if (!part) return null;
    if (part.startsWith("``") && part.endsWith("``")) return <code key={i}>{part.slice(2, -2)}</code>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <em key={i}>{part.slice(1, -1)}</em>;
    const link = part.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
    if (link) {
      return /^https?:\/\//.test(link[2]) ? (
        <a key={i} href={link[2]} target="_blank" rel="noreferrer noopener">
          {inline(link[1])}
        </a>
      ) : (
        <span key={i}>{inline(link[1])}</span>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

/** Inline-only rendering (for one-line summaries). */
export function InlineMarkdown({ text }: { text: string }) {
  return <>{inline(text)}</>;
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      {parse(text).map((b, i) => {
        if (b.kind === "h") return <h4 key={i}>{inline(b.text)}</h4>;
        if (b.kind === "code")
          return (
            <pre key={i}>
              <code>{b.text}</code>
            </pre>
          );
        if (b.kind === "list")
          return (
            <ul key={i}>
              {b.items.map((it, j) => (
                <li key={j}>{inline(it)}</li>
              ))}
            </ul>
          );
        return <p key={i}>{inline(b.text)}</p>;
      })}
    </div>
  );
}
