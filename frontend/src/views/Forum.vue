<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowRight, Clock3, MessagesSquare } from "lucide-vue-next";
import { api, typeName } from "../api";
import type { ForumPost } from "../types";
import ImageGallery from "../components/ImageGallery.vue";

const route = useRoute(),
  router = useRouter();
const items = ref<ForumPost[]>([]),
  total = ref(0),
  loading = ref(true),
  error = ref("");
const expanded = ref(new Set<string>());
const page = computed(() => {
  const n = Number(route.query.page || 1);
  return Number.isSafeInteger(n) && n > 0 ? n : 1;
});
const pages = computed(() => Math.max(1, Math.ceil(total.value / 12)));
const dates = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const dateLabel = (value: string) => dates.format(new Date(value));
const hasLongBody = (body: string) =>
  body.length > 240 || body.split("\n").length > 6;
const target = (s: ForumPost) => ({
  path: "/problems/" + s.problem.id,
  query: { solution: s.id },
});
let sequence = 0;
onBeforeUnmount(() => ++sequence);
async function load() {
  const seq = ++sequence;
  loading.value = true;
  error.value = "";
  expanded.value.clear();
  try {
    const data = await api<{ items: ForumPost[]; total: number }>(
      "/forum?page=" + page.value,
    );
    if (seq !== sequence) return;
    items.value = data.items;
    total.value = data.total;
    if (page.value > pages.value) {
      await router.replace({
        path: "/forum",
        query: pages.value > 1 ? { page: pages.value } : {},
      });
    }
  } catch (e) {
    if (seq === sequence) error.value = (e as Error).message;
  } finally {
    if (seq === sequence) loading.value = false;
  }
}
watch(page, load, { immediate: true });
function turn(n: number) {
  router.push({ path: "/forum", query: n > 1 ? { page: n } : {} });
}
</script>
<template>
  <div class="forum-page">
    <div class="page-heading">
      <div>
        <h1>论坛</h1>
        <p class="muted">已发布题解，最新在前。</p>
      </div>
      <span class="chip"><MessagesSquare :size="16" />题解交流</span>
    </div>
    <div v-if="loading" class="loading-panel" role="status">加载中…</div>
    <div v-else-if="error" class="empty-state" role="alert">
      <p>{{ error }}</p>
      <button class="button" @click="load">重试</button>
    </div>
    <template v-else>
      <div class="forum-summary">
        <span>共 {{ total }} 篇题解</span
        ><span class="inline"><Clock3 :size="15" />按发布时间排序</span>
      </div>
      <div v-if="!items.length" class="empty-state">
        <MessagesSquare :size="36" />
        <h2>暂无已发布题解</h2>
        <p>做对题目后可分享题解，审核通过后展示。</p>
        <RouterLink to="/" class="button primary"
          >去做题<ArrowRight :size="16"
        /></RouterLink>
      </div>
      <article
        v-for="s in items"
        :key="s.id"
        class="paper-card forum-post"
        :aria-labelledby="'post-' + s.id"
      >
        <div class="forum-post-meta">
          <div class="inline">
            <span class="avatar small-avatar" aria-hidden="true">{{
              s.author.slice(0, 1)
            }}</span
            ><strong>{{ s.author }}</strong>
          </div>
          <time :datetime="s.created_at">{{ dateLabel(s.created_at) }}</time>
        </div>
        <h2 :id="'post-' + s.id">
          <RouterLink :to="target(s)">{{ s.problem.title }}</RouterLink>
        </h2>
        <div class="forum-tags">
          <span class="chip neutral">{{ typeName(s.problem.type) }}</span
          ><span>{{ s.problem.year }} 年</span
          ><span v-for="tag in s.problem.tags" :key="tag">{{ tag }}</span>
        </div>
        <p
          v-if="s.body"
          class="preserve-text forum-body"
          :class="{
            'body-preview': hasLongBody(s.body) && !expanded.has(s.id),
          }"
        >
          {{
            expanded.has(s.id) || !hasLongBody(s.body)
              ? s.body
              : s.body.slice(0, 240) + "…"
          }}
        </p>
        <button
          v-if="hasLongBody(s.body)"
          class="text-button"
          :aria-expanded="expanded.has(s.id)"
          @click="
            expanded.has(s.id) ? expanded.delete(s.id) : expanded.add(s.id)
          "
        >
          {{ expanded.has(s.id) ? "收起全文" : "展开全文" }}
        </button>
        <ImageGallery
          v-if="s.images.length"
          :images="s.images"
          label="题解"
          compact
        />
        <div class="forum-post-footer">
          <span>题解</span
          ><RouterLink :to="target(s)" class="button small"
            >查看与讨论<ArrowRight :size="16"
          /></RouterLink>
        </div>
      </article>
      <nav v-if="total > 12" class="pagination" aria-label="论坛分页">
        <button
          class="button small"
          :disabled="page === 1"
          @click="turn(page - 1)"
        >
          上一页
        </button>
        <span aria-live="polite">{{ page }} / {{ pages }}</span>
        <button
          class="button small"
          :disabled="page >= pages"
          @click="turn(page + 1)"
        >
          下一页
        </button>
      </nav>
    </template>
  </div>
</template>
<style scoped>
.forum-page {
  max-width: 960px;
  margin: auto;
}
.page-heading p {
  margin: 8px 0 0;
  font-size: 14px;
}
.forum-summary,
.forum-post-meta,
.forum-post-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.forum-summary {
  margin-bottom: 20px;
  color: var(--muted);
  font-size: 13px;
}
.forum-post-meta time {
  font-size: 12px;
  color: var(--muted);
}
.forum-post h2 {
  margin: 18px 0 10px;
  overflow-wrap: anywhere;
}
.forum-post h2 a:hover {
  color: var(--green);
  text-decoration: underline;
}
.forum-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  color: var(--muted);
  font-size: 12px;
}
.forum-body {
  overflow-wrap: anywhere;
  margin-bottom: 16px;
}
.forum-body.body-preview {
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.forum-post-footer {
  padding-top: 16px;
  margin-top: 20px;
  border-top: 1px solid var(--line);
}
.forum-post-footer > span {
  color: var(--muted);
  font-size: 12px;
}
.forum-post .button,
.pagination .button,
.forum-post .text-button {
  min-height: 44px;
}
@media (max-width: 640px) {
  .forum-post-meta {
    align-items: flex-start;
  }
  .forum-post-meta time {
    font-size: 11px;
  }
  .forum-summary {
    font-size: 12px;
  }
}
</style>
