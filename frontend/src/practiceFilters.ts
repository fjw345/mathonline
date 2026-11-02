import type { LocationQuery } from "vue-router";

export type PracticeFilters = Record<string, string>;

const keys = ["year", "type", "tag", "q"] as const;
const legacyTags = new Set(["全国大学生数学竞赛", "非数学类", "高等数学"]);

export function readPracticeFilters(query: LocationQuery | PracticeFilters) {
  const filters: PracticeFilters = {};
  for (const key of keys) {
    const value = query[key];
    if (value) {
      const text = String(value);
      if (key === "tag" && /^20\d{2}$/.test(text) && !filters.year)
        filters.year = text;
      filters[key] =
        key === "tag" && (legacyTags.has(text) || /^20\d{2}$/.test(text))
          ? "全国大学生数学竞赛·非数"
          : text;
    }
  }
  return filters;
}

export function practiceFilterQuery(filters: PracticeFilters) {
  const params = new URLSearchParams(readPracticeFilters(filters));
  params.sort();
  return params;
}

const remembered = new Map<string, PracticeFilters>();
function preferenceKey(userId: string | undefined) {
  return "mathonline:random-filters:v1:" + (userId || "guest");
}

export function rememberPracticeFilters(
  userId: string | undefined,
  filters: PracticeFilters,
) {
  const key = preferenceKey(userId);
  const value = readPracticeFilters(filters);
  remembered.set(key, value);
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Navigation can still restore the in-memory selection.
  }
}

export function rememberedPracticeRoute(userId: string | undefined) {
  const key = preferenceKey(userId);
  let value = remembered.get(key) || {};
  try {
    const raw = localStorage.getItem(key);
    const stored = raw ? JSON.parse(raw) : null;
    if (
      stored &&
      !Array.isArray(stored) &&
      typeof stored === "object" &&
      Object.values(stored).every((item) => typeof item === "string")
    )
      value = readPracticeFilters(stored);
  } catch {
    // Invalid or unavailable browser storage should not block navigation.
  }
  const query = practiceFilterQuery(value).toString();
  return "/practice" + (query ? "?" + query : "");
}
