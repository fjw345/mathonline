<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  ArrowRight,
  Shuffle,
  Search,
  ChevronLeft,
  ChevronRight,
  BookOpen,
} from "lucide-vue-next";
import { api, typeName } from "../api";
import type { Problem } from "../types";
import ProblemCard from "../components/ProblemCard.vue";
const router = useRouter();
const catalog = ref({
  total: 0,
  years: [] as number[],
  tags: [] as string[],
});
const items = ref<Problem[]>([]);
const total = ref(0);
const loading = ref(true);
const error = ref("");
const page = ref(1);
const tag = ref("");
const year = ref("");
const type = ref("");
const search = ref("");
let requestId = 0;
let controller: AbortController | undefined;
function params() {
  const p = new URLSearchParams({ page: String(page.value) });
  if (year.value) p.set("year", year.value);
  if (type.value) p.set("type", type.value);
  if (search.value) p.set("q", search.value);
  if (tag.value) p.set("tag", tag.value);
  return p;
}
async function load() {
  const id = ++requestId;
  controller?.abort();
  controller = new AbortController();
  const signal = AbortSignal.any([
    controller.signal,
    AbortSignal.timeout(20000),
  ]);
  loading.value = true;
  error.value = "";
  try {
    const data = await api("/problems?" + params(), { signal });
    if (id === requestId) {
      items.value = data.items;
      total.value = data.total;
    }
  } catch (e) {
    if (id === requestId) error.value = (e as Error).message;
  } finally {
    if (id === requestId) loading.value = false;
  }
}
onMounted(async () => {
  const initialLoad = load();
  try {
    catalog.value = await api("/catalog");
  } catch (e) {
    error.value = (e as Error).message;
  }
  await initialLoad;
});
watch([year, type, tag], () => {
  if (page.value !== 1) page.value = 1;
  else void load();
});
watch(page, load);
function doSearch() {
  if (page.value !== 1) page.value = 1;
  else void load();
}
function reset() {
  const needsLoad =
    !tag.value && !year.value && !type.value && page.value === 1;
  tag.value = "";
  year.value = "";
  type.value = "";
  search.value = "";
  page.value = 1;
  if (needsLoad) void load();
}
onBeforeUnmount(() => {
  requestId++;
  controller?.abort();
});
function random() {
  const p = params();
  p.delete("page");
  router.push("/practice?" + p);
}
</script>
<template>
  <section class="library-hero">
    <div class="hero-copy">
      <h1>别样的数学♂练习大战</h1>
      <p>选择题目，提交作答，核对答案。</p>
      <div class="hero-actions">
        <RouterLink :to="'/daily?' + params()" class="button primary"
          >每日一题 <ArrowRight :size="17" /></RouterLink
        ><button class="button ghost" @click="random">
          <Shuffle :size="17" />随机练习
        </button>
      </div>
    </div>
    <div class="math-art" aria-hidden="true">
      <svg viewBox="0 0 370 250">
        <defs>
          <pattern
            id="grid"
            width="24"
            height="24"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="1" cy="1" r="1" fill="#c6d6cc" />
          </pattern>
        </defs>
        <rect width="370" height="250" fill="url(#grid)" />
        <path d="M40 200 H330 M80 220 V25" stroke="#a7bdb0" fill="none" />
        <path
          d="M45 187 C90 190 121 191 150 167 C179 143 210 100 240 89 S296 79 321 45"
          stroke="#316959"
          stroke-width="2.5"
          fill="none"
        />
        <path
          d="M150 167 V200 M240 89 V200"
          stroke="#86a897"
          stroke-dasharray="4 5"
        />
        <path
          d="M150 200 V167 C179 143 210 100 240 89 V200Z"
          fill="#316959"
          opacity=".1"
        />
        <circle cx="240" cy="89" r="5" fill="#316959" />
        <text
          x="176"
          y="53"
          fill="#315c4f"
          font-family="Georgia"
          font-size="38"
          font-style="italic"
        >
          ∫
        </text>
        <g fill="#315c4f" font-family="Georgia" font-style="italic">
          <text x="197" y="26" font-size="15">b</text>
          <text x="184" y="68" font-size="15">a</text>
          <text x="211" y="49" font-size="27">f(x) dx</text>
        </g>
        <g
          fill="#7f968a"
          font-family="Georgia"
          font-size="16"
          font-style="italic"
          text-anchor="middle"
        >
          <text x="150" y="220">a</text>
          <text x="240" y="220">b</text>
        </g>
        <text
          x="300"
          y="230"
          fill="#7f968a"
          font-family="Georgia"
          font-size="15"
        >
          x
        </text>
      </svg>
    </div>
  </section>
  <section class="library-section">
    <div class="section-heading">
      <div>
        <h2>
          题库 <span class="count">{{ catalog.total }}</span>
        </h2>
      </div>
      <span class="collection-note">
        历史北交期末/CMC非数/考研数学真题集合
      </span>
    </div>
    <form class="filter-bar" @submit.prevent="doSearch">
      <label class="library-tag-filter">
        <span class="sr-only">标签</span>
        <select v-model="tag">
          <option value="">全部标签</option>
          <option v-for="value in catalog.tags" :key="value" :value="value">
            {{ value }}
          </option>
        </select> </label
      ><label
        ><span class="sr-only">年份</span
        ><select v-model="year">
          <option value="">全部年份</option>
          <option v-for="y in catalog.years" :key="y" :value="String(y)">
            {{ y }} 年
          </option>
        </select></label
      ><label
        ><span class="sr-only">题型</span
        ><select v-model="type">
          <option value="">全部题型</option>
          <option
            v-for="t in ['blank', 'solution', 'choice']"
            :key="t"
            :value="t"
          >
            {{ typeName(t) }}
          </option>
        </select></label
      >
      <div class="search-field">
        <Search :size="17" /><input
          v-model="search"
          aria-label="搜索题号、年份或标签"
          placeholder="搜索题号、年份或标签"
        /><button type="submit" class="icon-button" aria-label="搜索">
          <ArrowRight :size="16" />
        </button>
      </div>
    </form>
    <div class="results-caption">
      <span
        >共 {{ total }} 道题目<span class="separator">·</span
        >填空题、解答题需自检</span
      >
      <div class="inline">
        <RouterLink :to="'/daily?' + params()" class="text-button"
          >每日一题</RouterLink
        ><button class="text-button" @click="random">随机练习</button
        ><button class="text-button" @click="reset">重置</button>
      </div>
    </div>
    <div v-if="error" class="empty-state">
      <p>{{ error }}</p>
      <button class="button" @click="load">重新加载</button>
    </div>
    <div v-else-if="loading" class="card-grid" aria-label="加载题库中">
      <div v-for="n in 6" :key="n" class="skeleton-card"></div>
    </div>
    <div v-else-if="!items.length" class="empty-state">
      <BookOpen :size="32" />
      <h3>暂无匹配题目</h3>
      <p>请调整筛选条件。</p>
      <button class="button" @click="reset">查看全部题目</button>
    </div>
    <div v-else class="card-grid">
      <ProblemCard v-for="p in items" :key="p.id" :problem="p" />
    </div>
    <div v-if="total > 12" class="pagination">
      <button
        class="button small"
        :disabled="page === 1 || loading"
        @click="page--"
      >
        <ChevronLeft :size="16" />上一页</button
      ><span>{{ page }} / {{ Math.ceil(total / 12) }}</span
      ><button
        class="button small"
        :disabled="page >= Math.ceil(total / 12) || loading"
        @click="page++"
      >
        下一页<ChevronRight :size="16" />
      </button>
    </div>
  </section>
</template>
