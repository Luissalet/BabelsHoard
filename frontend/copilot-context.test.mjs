import { test } from "node:test";
import assert from "node:assert/strict";
import {
  addRefreshHandler, buildContext, changedFields, clip, codeFields, collectEditors, entryFromLookup, entryFromSection, installCopilot, modelsFields,
  modelsPatch, parseRoute, rebaseForm,
} from "./src/copilot-context.ts";

const envs = [
  { id: "env_builtin", label: "Built-in", python_version: "3.12", is_builtin: 1 },
  { id: "env_1", label: "My project", project_path: "C:/p", python_version: "3.11", node_modules_path: "C:/p/node_modules", default_for: ["python"] },
];
const docsets = [{ slug: "python~3.12", name: "Python", version: "3.12", library_id: "lib-1", entry_count: 900 }];
const entry = { id: "lib-1:httpx.Client.get", qualname: "httpx.Client.get", kind: "method", library: "httpx", signature: "get(url)", summary: "Send a GET." };
const deps = {
  getEnvironments: async () => envs,
  getDocsets: async () => docsets,
  getEntry: async (ref) => {
    if (ref.qualname !== "httpx.Client.get") throw new Error("not found");
    return entry;
  },
};
const detail = { kind: "entry-detail", object_id: "httpx.Client.get", dirty: false, fields: {}, ref: { qualname: "httpx.Client.get", entry_kind: "symbol", library: null, env_id: null } };

test("changed fields and rebasing keep what the user typed", () => {
  assert.deepEqual(changedFields({ a: "1", b: "" }, { a: "1", b: "x", c: "" }), { b: "x", c: "" });
  const before = { faustusUrl: "a", llmUrl: "b" };
  assert.deepEqual(rebaseForm(before, { faustusUrl: "agent", llmUrl: "b2" }, { faustusUrl: "a", llmUrl: "typed" }), { faustusUrl: "agent", llmUrl: "typed" });
});

test("the settings patch has only the changed fields and clears with an empty string", () => {
  const loaded = { faustusUrl: "http://f", llmUrl: "http://l", llmModel: "qwen", embedUrl: "", embedModel: "" };
  assert.deepEqual(modelsPatch(loaded, loaded), {});
  assert.deepEqual(modelsPatch(loaded, { ...loaded, llmModel: "qwen2" }), { capabilities: { llm: { model: "qwen2" } } });
  assert.deepEqual(modelsPatch(loaded, { ...loaded, llmUrl: " ", embedModel: "nomic" }), { capabilities: { llm: { url: "" }, embeddings: { model: "nomic" } } });
  assert.deepEqual(modelsPatch(loaded, { ...loaded, faustusUrl: "http://g" }), { faustus: { url: "http://g" } });
});

test("a typed token is reported as hidden, never as its value", () => {
  const loaded = { faustusUrl: "", llmUrl: "", llmModel: "", embedUrl: "", embedModel: "" };
  const fields = modelsFields(loaded, loaded, true);
  assert.deepEqual(fields, { faustusToken: "(typed, hidden)" });
  assert.deepEqual(modelsFields(loaded, loaded, false), {});
});

test("the code box is previewed, not sent whole", () => {
  assert.deepEqual(codeFields("same", "same", "python", "env_1", "env_1"), {});
  const big = "x".repeat(5000);
  const f = codeFields(big, "sample", "typescript", "env_2", "env_1");
  assert.equal(f.code.length <= 2001, true);
  assert.equal(f.code_chars, 5000);
  assert.equal(f.language, "typescript");
  assert.equal(f.env, "env_2");
  assert.equal(clip("abc", 5), "abc");
});

test("route parsing", () => {
  assert.equal(parseRoute("#/libraries").page, "libraries");
  assert.equal(parseRoute("#/check?x=1").page, "check");
  assert.equal(parseRoute("").page, "search");
  assert.equal(parseRoute("#/nope").page, "search");
});

test("search view with an entry panel returns the resolved entry", async () => {
  const form = { kind: "search-form", object_id: null, dirty: true, fields: { query: "get" }, ref: { env_id: null } };
  const ctx = await buildContext({ ...deps, hash: "#/search", editors: [detail, form] });
  assert.deepEqual(ctx.active_objects.map((o) => `${o.kind}:${o.object_id}`), ["entry:lib-1:httpx.Client.get"]);
  assert.equal(ctx.active_objects[0].library, "httpx");
  assert.equal(ctx.view.page, "search");
  assert.equal(ctx.view.object_id, "lib-1:httpx.Client.get");
  assert.deepEqual(ctx.view.editor.fields, { query: "get" });
  assert.equal(ctx.view.editors, undefined);
});

test("unknown entries, creation forms and plain views yield no object and never throw", async () => {
  const missing = { ...detail, object_id: "nope.x", ref: { ...detail.ref, qualname: "nope.x" } };
  assert.deepEqual((await buildContext({ ...deps, hash: "#/search", editors: [missing] })).active_objects, []);
  for (const hash of ["", "#/activity", "#/settings", "#/search"]) {
    assert.deepEqual((await buildContext({ ...deps, hash, editors: [] })).active_objects, [], hash);
  }
  const broken = { getEnvironments: async () => { throw new Error("down"); }, getDocsets: async () => { throw new Error("down"); }, getEntry: async () => { throw new Error("down"); } };
  for (const hash of ["#/libraries", "#/docsets", "#/search"]) assert.deepEqual((await buildContext({ ...broken, hash, editors: [detail] })).active_objects, []);
  const creating = await buildContext({ ...deps, hash: "#/docsets", editors: [{ kind: "folder-form", object_id: null, dirty: true, fields: { path: "C:/docs" } }] });
  assert.deepEqual(creating.view.editor.fields, { path: "C:/docs" });
});

test("libraries lists the environments, the one with an open form first", async () => {
  const ctx = await buildContext({ ...deps, hash: "#/libraries", editors: [{ kind: "library-index-form", object_id: "env_builtin", dirty: true, fields: { import_name: "yaml" } }] });
  assert.deepEqual(ctx.active_objects.map((o) => o.object_id), ["env_builtin", "env_1"]);
  assert.equal(ctx.active_objects[1].has_node_modules, true);
  assert.equal(ctx.view.editor.fields.import_name, "yaml");
});

test("check names the selected environment; docsets lists the installed ones", async () => {
  const check = await buildContext({ ...deps, hash: "#/check", editors: [{ kind: "check-form", object_id: null, dirty: false, fields: {}, ref: { env_id: "env_1" } }] });
  assert.deepEqual(check.active_objects.map((o) => `${o.kind}:${o.object_id}`), ["environment:env_1"]);
  const ds = await buildContext({ ...deps, hash: "#/docsets", editors: [] });
  assert.deepEqual(ds.active_objects.map((o) => `${o.kind}:${o.object_id}`), ["docset:python~3.12"]);
});

test("entry shapes are normalised and long text is cut", () => {
  const ref = { id: "lib-1:s", qualname: "Intro", entry_kind: "section", library: "docs", env_id: null };
  assert.equal(entryFromLookup(ref, { found: false }), null);
  assert.equal(entryFromSection(ref, null), null);
  assert.equal(entryFromSection(ref, { found: true, text: "t" }).kind, "section");
  assert.equal(entryFromLookup({ ...ref, entry_kind: "symbol" }, { found: true, id: "lib-1:x", qualname: "x", kind: "function" }).id, "lib-1:x");
});

test("forms answer the context request and modals come first", () => {
  const target = new EventTarget();
  target.addEventListener("babel:context-request", (e) => e.detail.provide({ kind: "search-form", object_id: null }));
  target.addEventListener("babel:context-request", (e) => e.detail.provide({ kind: "library-index-form", object_id: "env_1" }));
  assert.deepEqual(collectEditors(target).map((e) => e.kind), ["library-index-form", "search-form"]);
});

test("turn end (event and callback) refreshes every mounted view once, and install is idempotent", async () => {
  const target = new EventTarget();
  const context = async () => ({ active_objects: [], view: { page: "search" } });
  installCopilot(target, context);
  const first = target.hoardCopilot;
  installCopilot(target, async () => null);
  assert.equal(target.hoardCopilot, first);
  assert.equal(typeof first.context, "function");
  let a = 0;
  let b = 0;
  const offA = addRefreshHandler(() => { a += 1; });
  addRefreshHandler(() => { b += 1; });
  target.dispatchEvent(new Event("hoard:copilot-turn-end"));
  target.hoardCopilot.onTurnEnd();
  await new Promise((r) => setTimeout(r, 400));
  assert.deepEqual([a, b], [1, 1]);
  offA();
  target.hoardCopilot.onTurnEnd();
  await new Promise((r) => setTimeout(r, 400));
  assert.deepEqual([a, b], [1, 2]);
});
