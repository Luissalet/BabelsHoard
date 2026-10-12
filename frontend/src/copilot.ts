import { api } from "./api";
import { buildContext, collectEditors, entryFromLookup, entryFromSection, installCopilot } from "./copilot-context";
import type { CopilotContext, EntryRef } from "./copilot-context";

// Read-only calls through the endpoints the UI already uses (they are not recorded as assistant activity).
function context(): Promise<CopilotContext> {
  return buildContext({
    hash: window.location.hash,
    editors: collectEditors(),
    getEnvironments: () => api.environments(),
    getDocsets: () => api.docsets(),
    getEntry: async (ref: EntryRef) =>
      ref.entry_kind === "section"
        ? entryFromSection(ref, await api.readEntry(ref.id, 0, 500))
        : entryFromLookup(ref, await api.lookup(ref.qualname, ref.env_id ?? undefined)),
  });
}

export function startCopilot() {
  installCopilot(window, context);
}
