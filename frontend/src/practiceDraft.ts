import type { ImageAsset } from "./types";
import { practiceFilterQuery } from "./practiceFilters";

export interface PracticeDraft {
  problemId: string;
  problemVersion: number;
  progressRevision: number | null;
  text: string;
  options: string[];
  images: ImageAsset[];
  solutionText: string;
  solutionImages: ImageAsset[];
  editingVersion?: number;
}

// Keep drafts in memory too, so navigation still works if browser storage is disabled.
const memory = new Map<string, string>();

export function practiceDraftKey(
  userId: string | undefined,
  filters: string,
  mode = "random",
) {
  return (
    `mathonline:${mode}-draft:v1:` +
    JSON.stringify([userId || "guest", filters])
  );
}

export function readPracticeDraft(key: string): PracticeDraft | undefined {
  let raw = memory.get(key);
  try {
    raw = localStorage.getItem(key) || raw;
  } catch {
    // Use the in-memory copy when browser storage is unavailable.
  }
  if (!raw) return;
  try {
    const draft = JSON.parse(raw);
    const strings = (value: unknown) =>
      Array.isArray(value) && value.every((item) => typeof item === "string");
    const assets = (value: unknown) =>
      Array.isArray(value) &&
      value.length <= 5 &&
      value.every(
        (item) =>
          item && typeof item.id === "string" && typeof item.url === "string",
      );
    if (
      typeof draft.problemId === "string" &&
      Number.isInteger(draft.problemVersion) &&
      (draft.progressRevision === null ||
        Number.isInteger(draft.progressRevision)) &&
      typeof draft.text === "string" &&
      strings(draft.options) &&
      assets(draft.images) &&
      typeof draft.solutionText === "string" &&
      assets(draft.solutionImages)
    )
      return draft;
  } catch {
    // An invalid or older draft must not prevent opening practice.
  }
}

export function readRandomPracticeDraft(
  userId: string | undefined,
  filters: string,
) {
  const key = practiceDraftKey(userId, filters);
  const current = readPracticeDraft(key);
  if (current) return current;
  // Old dimensions now share a range; recover one draft belonging to this account.
  const prefix = "mathonline:random-draft:v1:";
  const candidates = new Set(memory.keys());
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const storedKey = localStorage.key(i);
      if (storedKey) candidates.add(storedKey);
    }
  } catch {
    // The in-memory drafts still work when local storage is unavailable.
  }
  for (const oldKey of [...candidates].reverse()) {
    if (!oldKey.startsWith(prefix)) continue;
    try {
      const [owner, oldFilters] = JSON.parse(oldKey.slice(prefix.length));
      if (owner !== (userId || "guest") || typeof oldFilters !== "string")
        continue;
      const normalized = practiceFilterQuery(
        Object.fromEntries(new URLSearchParams(oldFilters)),
      ).toString();
      if (normalized !== filters) continue;
      const draft = readPracticeDraft(oldKey);
      if (draft) {
        writePracticeDraft(key, draft);
        return draft;
      }
    } catch {
      // Ignore malformed browser data and keep looking for a valid draft.
    }
  }
}

export function writePracticeDraft(key: string, draft: PracticeDraft) {
  const raw = JSON.stringify(draft);
  memory.set(key, raw);
  try {
    localStorage.setItem(key, raw);
  } catch {
    // The in-memory copy still preserves drafts while navigating this tab.
  }
}
