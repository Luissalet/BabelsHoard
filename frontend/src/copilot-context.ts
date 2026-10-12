// What the shared copilot launcher asks this app for: the real objects open in the current view and the unsaved form
// fields, plus a way to reload the open view in place when the assistant finishes a turn. Nothing here renders anything.
// This file has no runtime imports on purpose: `node --test` loads it directly (see ../copilot-context.test.mjs).

export interface EditorSnapshot {
  kind: string;
  object_id: string | null;
  dirty: boolean;
  fields: Record<string, unknown>;
  /** Read-only pointers the form adds so the context can name what it belongs to (never typed content). */
  ref?: Record<string, string | null | undefined>;
}

export interface ActiveObject {
  kind: string;
  object_id: string;
  title: string;
  [key: string]: unknown;
}

export interface CopilotContext {
  active_objects: ActiveObject[];
  view: { page: string; object_id?: string; editor?: EditorSnapshot; editors?: EditorSnapshot[] };
}

/** An entry as the detail panel resolved it (symbol lookup or documentation section). */
export interface EntryInfo {
  id: string;
  qualname: string;
  kind: string;
  library?: string | null;
  signature?: string | null;
  summary?: string | null;
  deprecated?: boolean;
  preview?: string | null;
}

export interface EntryRef {
  id: string;
  qualname: string;
  entry_kind: string;
  library: string | null;
  env_id: string | null;
}

interface EnvLike {
  id: string;
  label: string;
  project_path?: string | null;
  python_version?: string | null;
  node_version?: string | null;
  node_modules_path?: string | null;
  is_default?: boolean;
  default_for?: string[];
  is_builtin?: number;
}

interface DocsetLike {
  slug: string;
  name: string;
  version?: string | null;
  library_id?: string;
  entry_count?: number;
}

export interface ContextDeps {
  hash?: string;
  editors?: EditorSnapshot[];
  getEnvironments: () => Promise<EnvLike[]>;
  getDocsets: () => Promise<DocsetLike[]>;
  getEntry: (ref: EntryRef) => Promise<EntryInfo | null>;
}

const PAGES = ["search", "libraries", "check", "docsets", "activity", "settings"];
const MAX_OBJECTS = 8;
const MAX_CODE_CHARS = 2000;
const EDITOR_ORDER: Record<string, number> = {
  "library-index-form": 0,
  "environment-new": 0,
  "folder-form": 0,
  "docset-search": 1,
  "models-form": 1,
  "check-form": 1,
  "search-form": 2,
};

export function clip(text: unknown, max: number): string {
  const s = String(text ?? "");
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

// Fields of `draft` whose value differs from `initial` (compared as JSON, so "" clears count).
export function changedFields(initial: Record<string, unknown> | null | undefined, draft: Record<string, unknown> | null | undefined) {
  const base = initial || {};
  return Object.fromEntries(Object.entries(draft || {}).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(base[key])));
}

// A refreshed baseline arrives while the user is editing: fields the user did not touch follow the new baseline, touched ones stay.
export function rebaseForm<T extends Record<string, unknown>>(previousBase: T | null | undefined, nextBase: T, form: T): T {
  const out = { ...nextBase };
  for (const key of Object.keys(form) as (keyof T)[]) {
    if (JSON.stringify(form[key]) !== JSON.stringify(previousBase?.[key])) out[key] = form[key];
  }
  return out;
}

// The backend.json patch for the Settings form: only fields that differ from what was loaded, so a value changed elsewhere
// meanwhile is not overwritten by a stale copy. An emptied field is sent as "" (the server drops that override).
export type ModelsForm = {
  faustusUrl: string;
  llmUrl: string;
  llmModel: string;
  embedUrl: string;
  embedModel: string;
};

export interface ModelsPatch {
  faustus?: { url?: string; token?: string };
  capabilities?: Record<string, { url?: string; model?: string }>;
}

export function modelsPatch(initial: ModelsForm, draft: ModelsForm): ModelsPatch {
  const patch: ModelsPatch = {};
  if (draft.faustusUrl.trim() !== initial.faustusUrl.trim()) patch.faustus = { url: draft.faustusUrl.trim() };
  const cap = (urlKey: keyof ModelsForm, modelKey: keyof ModelsForm, name: string) => {
    const entry: { url?: string; model?: string } = {};
    if (draft[urlKey].trim() !== initial[urlKey].trim()) entry.url = draft[urlKey].trim();
    if (draft[modelKey].trim() !== initial[modelKey].trim()) entry.model = draft[modelKey].trim();
    if (Object.keys(entry).length > 0) (patch.capabilities ??= {})[name] = entry;
  };
  cap("llmUrl", "llmModel", "llm");
  cap("embedUrl", "embedModel", "embeddings");
  return patch;
}

// The Settings form as a context snapshot. The Faustus token is a secret: only "a token was typed" is reported.
export function modelsFields(initial: ModelsForm, draft: ModelsForm, tokenTyped: boolean): Record<string, unknown> {
  const fields: Record<string, unknown> = changedFields(initial, draft);
  if (tokenTyped) fields.faustusToken = "(typed, hidden)";
  return fields;
}

// The code box can be large: send a short preview and its length.
export function codeFields(code: string, loadedCode: string, language: string, env: string, defaultEnv: string): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  if (code !== loadedCode) {
    fields.code = clip(code, MAX_CODE_CHARS);
    fields.code_chars = code.length;
  }
  if (language !== "python") fields.language = language;
  if (env !== defaultEnv) fields.env = env;
  return fields;
}

export function parseRoute(hash: string | undefined | null): { page: string } {
  const first = String(hash || "").replace(/^#\/?/, "").split(/[/?]/)[0];
  return { page: PAGES.includes(first) ? first : "search" };
}

// Every open form answers this event with a snapshot of what the user typed: { kind, object_id, dirty, fields }.
export function collectEditors(target: EventTarget | undefined = (globalThis as { window?: EventTarget }).window): EditorSnapshot[] {
  const found: EditorSnapshot[] = [];
  if (!target?.dispatchEvent) return found;
  target.dispatchEvent(
    new CustomEvent("babel:context-request", {
      detail: { provide: (snapshot: EditorSnapshot | null | undefined) => { if (snapshot) found.push(snapshot); } },
    })
  );
  return found.sort((a, b) => (EDITOR_ORDER[a.kind] ?? 9) - (EDITOR_ORDER[b.kind] ?? 9));
}

export function environmentObject(e: EnvLike): ActiveObject {
  const out: ActiveObject = { kind: "environment", object_id: e.id, title: e.label || e.id };
  if (e.python_version) out.python_version = e.python_version;
  if (e.node_version) out.node_version = e.node_version;
  if (e.node_modules_path) out.has_node_modules = true;
  if (e.project_path) out.project_path = e.project_path;
  if (e.default_for?.length) out.default_for = e.default_for;
  if (e.is_builtin) out.builtin = true;
  return out;
}

export function docsetObject(d: DocsetLike): ActiveObject {
  const out: ActiveObject = { kind: "docset", object_id: d.slug, title: d.name || d.slug };
  if (d.version) out.version = d.version;
  if (d.library_id) out.library_id = d.library_id;
  if (typeof d.entry_count === "number") out.entries = d.entry_count;
  return out;
}

export function entryObject(info: EntryInfo): ActiveObject {
  const out: ActiveObject = { kind: "entry", object_id: info.id, title: info.qualname || info.id, entry_kind: info.kind };
  if (info.library) out.library = info.library;
  if (info.signature) out.signature = clip(info.signature, 200);
  if (info.summary) out.summary = clip(info.summary, 240);
  if (info.deprecated) out.deprecated = true;
  if (info.preview) out.preview = clip(info.preview, 400);
  return out;
}

// Normalise the two shapes the detail panel loads into one EntryInfo (kept pure so it can be tested).
export function entryFromLookup(ref: EntryRef, r: { found: boolean; id?: string; qualname?: string; kind?: string; library?: string; signature?: string | null; summary?: string | null; deprecated?: boolean } | null): EntryInfo | null {
  if (!r?.found) return null;
  return {
    id: r.id ?? ref.id,
    qualname: r.qualname ?? ref.qualname,
    kind: r.kind ?? ref.entry_kind,
    library: r.library ?? ref.library,
    signature: r.signature,
    summary: r.summary,
    deprecated: r.deprecated,
  };
}

export function entryFromSection(ref: EntryRef, r: { found: boolean; text?: string; signature?: string | null } | null): EntryInfo | null {
  if (!r?.found) return null;
  return { id: ref.id, qualname: ref.qualname, kind: "section", library: ref.library, signature: r.signature, preview: r.text };
}

async function safe<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

// Pure core (dependencies injected so it can be tested): never throws; unknown ids and creation forms yield no object.
export async function buildContext({ hash, editors = [], getEnvironments, getDocsets, getEntry }: ContextDeps): Promise<CopilotContext> {
  const page = parseRoute(hash).page;
  const active: ActiveObject[] = [];
  const seen = new Set<string>();
  const add = (o: ActiveObject) => {
    const key = `${o.kind}:${o.object_id}`;
    if (!seen.has(key) && active.length < MAX_OBJECTS) {
      seen.add(key);
      active.push(o);
    }
  };

  let viewObject: string | undefined;
  const detail = editors.find((e) => e.kind === "entry-detail" && e.object_id);
  if (detail && page === "search") {
    const ref = detail.ref || {};
    const entry = await safe(() =>
      getEntry({ id: String(detail.object_id), qualname: String(ref.qualname ?? detail.object_id), entry_kind: String(ref.entry_kind ?? "symbol"), library: ref.library ?? null, env_id: ref.env_id ?? null })
    );
    if (entry?.id) {
      viewObject = entry.id;
      add(entryObject(entry));
    }
  }

  const wantedEnvs: string[] = [];
  for (const e of editors) {
    if (e.kind === "library-index-form" && e.object_id) wantedEnvs.push(e.object_id);
  }
  const refEnv = editors.find((e) => (e.kind === "check-form" || e.kind === "search-form") && e.ref?.env_id)?.ref?.env_id;
  if (refEnv && (page === "check" || page === "search")) wantedEnvs.push(refEnv);

  if (page === "libraries" || wantedEnvs.length > 0) {
    const envs = (await safe(getEnvironments)) || [];
    const byId = new Map(envs.map((e) => [e.id, e] as const));
    for (const id of wantedEnvs) if (byId.has(id)) add(environmentObject(byId.get(id)!));
    if (page === "libraries") for (const e of [...envs].reverse()) add(environmentObject(e));
  }
  if (page === "docsets") {
    for (const d of (await safe(getDocsets)) || []) add(docsetObject(d));
  }

  const view: CopilotContext["view"] = { page };
  if (viewObject) view.object_id = viewObject;
  const formEditors = editors.filter((e) => e.kind !== "entry-detail");
  const editor = formEditors.find((e) => e.dirty) || formEditors[0];
  if (editor) view.editor = editor;
  if (formEditors.length > 1) view.editors = formEditors;
  return { active_objects: active, view };
}

// ---- reload what is on screen when the assistant finishes a turn --------------------------------------------------------
const refreshHandlers = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;

export function scheduleRefresh() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    for (const fn of [...refreshHandlers]) {
      try {
        fn();
      } catch {
        /* a refresh must never break the page */
      }
    }
  }, 250);
}

// Each mounted view registers what "reload my data in place" means (it must keep the forms the user is editing untouched).
export function addRefreshHandler(fn: () => void): () => void {
  refreshHandlers.add(fn);
  return () => {
    refreshHandlers.delete(fn);
  };
}

interface CopilotTarget extends EventTarget {
  hoardCopilot?: { context: () => Promise<CopilotContext>; onTurnEnd: () => void };
}

// Once at start: the object the launcher looks for, and the window event it dispatches on turn end (the debounce merges the two).
export function installCopilot(target: CopilotTarget | undefined, context: () => Promise<CopilotContext>) {
  if (!target || target.hoardCopilot) return;
  target.hoardCopilot = { context, onTurnEnd: scheduleRefresh };
  target.addEventListener("hoard:copilot-turn-end", scheduleRefresh);
}
