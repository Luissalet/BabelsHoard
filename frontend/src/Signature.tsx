import type { ReactNode } from "react";

/**
 * Light syntax colouring for Python/TypeScript signatures such as
 * `get(self, url: URL | str, *, params: QueryParamTypes | None = None) -> Response`.
 * Tokenises once, tracks bracket depth and whether we are in a parameter
 * name, an annotation or a default value. Never uses innerHTML.
 */
type Tok = { text: string; cls: string };

const KEYWORDS = new Set(["class", "async", "def", "module", "lambda"]);
const CONSTANTS = new Set(["None", "True", "False", "null", "undefined", "true", "false"]);
const TOKEN_RE = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\d[\w.]*|[A-Za-z_$][\w$.]*|->|=>|\*\*|[()[\]{},:=|*<>?&]|\s+|.)/g;

function tokenize(sig: string): Tok[] {
  const raw = sig.match(TOKEN_RE) ?? [sig];
  const out: Tok[] = [];
  let depth = 0;
  let role: "head" | "name" | "type" | "default" | "ret" = "head";
  const isClass = /^\s*class\s/.test(sig);
  for (const text of raw) {
    let cls = "";
    if (/^\s+$/.test(text)) {
      out.push({ text, cls });
      continue;
    }
    if (text === "(" || text === "[" || text === "{" || text === "<") {
      depth += 1;
      if (depth === 1 && text === "(" && role === "head") role = isClass ? "type" : "name";
      cls = "sig-punct";
    } else if (text === ")" || text === "]" || text === "}" || text === ">") {
      depth = Math.max(0, depth - 1);
      cls = "sig-punct";
    } else if (text === "->" || text === "=>") {
      role = "ret";
      cls = "sig-punct";
    } else if (depth === 1 && text === ",") {
      role = isClass ? "type" : "name";
      cls = "sig-punct";
    } else if (depth === 0 && role === "head" && (text === ":" || text === "=")) {
      role = text === ":" ? "type" : "default"; // attribute: `name: Type = value`
      cls = "sig-punct";
    } else if (depth === 0 && role === "type" && text === "=") {
      role = "default";
      cls = "sig-punct";
    } else if (depth === 1 && text === ":" && role === "name") {
      role = "type";
      cls = "sig-punct";
    } else if (depth === 1 && text === "=" && (role === "name" || role === "type")) {
      role = "default";
      cls = "sig-punct";
    } else if (/^["']/.test(text)) {
      cls = "sig-string";
    } else if (/^\d/.test(text)) {
      cls = "sig-number";
    } else if (KEYWORDS.has(text) && role === "head") {
      cls = "sig-keyword";
    } else if (CONSTANTS.has(text)) {
      cls = "sig-constant";
    } else if (/^[A-Za-z_$]/.test(text)) {
      if (role === "head") cls = "sig-name";
      else if (role === "name" && depth === 1) cls = "sig-param";
      else if (role === "default") cls = "sig-default";
      else cls = "sig-type";
    } else {
      cls = "sig-punct";
    }
    out.push({ text, cls });
  }
  return out;
}

export function Signature({ text, className }: { text: string; className?: string }): ReactNode {
  return (
    <code className={`signature ${className ?? ""}`}>
      {tokenize(text).map((tok, i) =>
        tok.cls ? (
          <span key={i} className={tok.cls}>
            {tok.text}
          </span>
        ) : (
          tok.text
        )
      )}
    </code>
  );
}
