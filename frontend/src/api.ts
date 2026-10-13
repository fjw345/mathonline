import { reactive } from "vue";
import type { User } from "./types";
export const state = reactive({
  user: null as User | null,
  sessionReady: false,
  stats: { practiced: 0, correct: 0, mistakes: 0, checkins: 0 },
  health: { mode: "local", draft_preview: false, date: "" },
  toast: "",
  toastError: false,
});
let timer: ReturnType<typeof setTimeout>;
export function toast(message: string, error = false) {
  state.toast = message;
  state.toastError = error;
  clearTimeout(timer);
  timer = setTimeout(() => (state.toast = ""), error ? 9000 : 4500);
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
  retry = true,
): Promise<T> {
  const signal = options.signal ?? AbortSignal.timeout(20000);
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, {
      credentials: "include",
      ...options,
      signal,
      headers: {
        ...(options.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...options.headers,
      },
    });
  } catch {
    if (signal.aborted) throw new Error("请求超时，请重试。");
    throw new Error("连接失败，请检查服务后重试。");
  }
  if (
    response.status === 401 &&
    retry &&
    state.health.mode === "supabase" &&
    !path.startsWith("/auth/")
  ) {
    const refresh = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
      signal: AbortSignal.timeout(20000),
    });
    if (refresh.ok) return api(path, options, false);
  }
  const data = await response.json().catch(() => {
    if (signal.aborted) throw new Error("请求超时，请重试。");
    return { detail: "服务响应异常，请重试。" };
  });
  if (!response.ok) {
    if (response.status === 401) state.user = null;
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "输入内容无效，请检查后重试。",
    );
  }
  return data;
}
export const write = <T = any>(path: string, body: unknown, method = "POST") =>
  api<T>(path, { method, body: JSON.stringify(body) });
export async function loadMe() {
  const data = await api("/me");
  state.user = data.user;
  state.sessionReady = true;
  if (data.stats) state.stats = data.stats;
  return data;
}
export async function upload(file: File, purpose: string) {
  const form = new FormData();
  form.append("file", file);
  form.append("purpose", purpose);
  return api("/uploads", {
    method: "POST",
    body: form,
    signal: AbortSignal.timeout(60000),
  });
}
export const typeName = (type: string) =>
  ({ blank: "填空题", solution: "解答题", choice: "选择题" })[type] || type;
export const feedbackKindName = (kind: string) =>
  ({
    incomplete: "题目不完整",
    answer_mismatch: "答案不匹配",
    other: "其他问题",
  })[kind] || kind;
export const feedbackStatusName = (status: string) =>
  ({ pending: "待处理", resolved: "已处理", dismissed: "无需修改" })[status] ||
  status;
export const statusName = (status: string) =>
  ({
    pending: "待自检",
    correct: "正确",
    incorrect: "错误",
    approved: "已发布",
    hidden: "已隐藏",
    published: "已发布",
    draft: "草稿",
    archived: "已下架",
  })[status] || status;
