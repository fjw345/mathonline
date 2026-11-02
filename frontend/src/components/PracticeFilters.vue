<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ArrowRight, ChevronDown } from "lucide-vue-next";
import { api, typeName } from "../api";
import {
  practiceFilterQuery,
  readPracticeFilters,
  type PracticeFilters,
} from "../practiceFilters";

const props = defineProps<{
  daily: boolean;
  busy: boolean;
  locked: boolean;
  opened: boolean;
  pending: boolean;
}>();
const filters = defineModel<PracticeFilters>({ required: true });
const emit = defineEmits<{ apply: [filters: PracticeFilters] }>();
const catalog = ref({
  tags: [] as string[],
  years: [] as number[],
});
const catalogError = ref("");
const count = ref<number>();
const countError = ref("");
const counting = ref(false);
const advanced = ref(false);
let sequence = 0;
let timer: ReturnType<typeof setTimeout>;
const summary = computed(() => {
  const labels: Record<string, string> = {
    tag: "标签",
    year: "年份",
    type: "题型",
    q: "关键词",
  };
  return Object.entries(readPracticeFilters(filters.value))
    .filter(([, value]) => value)
    .map(([key, value]) => {
      const name = key === "type" ? typeName(value) : value;
      return `${labels[key]}：${name}`;
    });
});
const canApply = computed(
  () =>
    !props.busy &&
    (props.locked ||
      (!counting.value &&
        count.value !== 0 &&
        !countError.value &&
        !catalogError.value)),
);
function update(key: string, event: Event) {
  filters.value = {
    ...filters.value,
    [key]: (event.target as HTMLInputElement).value,
  };
}
async function loadCatalog() {
  catalogError.value = "";
  try {
    catalog.value = await api("/catalog");
  } catch (e) {
    catalogError.value = (e as Error).message;
  }
}
async function countMatches(seq: number, query: string) {
  try {
    const result = await api<{ total: number }>(
      "/problems?page_size=1&" + query,
    );
    if (seq === sequence) count.value = result.total;
  } catch (e) {
    if (seq === sequence) countError.value = (e as Error).message;
  } finally {
    if (seq === sequence) counting.value = false;
  }
}
function preview() {
  const seq = ++sequence;
  clearTimeout(timer);
  countError.value = "";
  count.value = undefined;
  counting.value = true;
  const query = practiceFilterQuery(filters.value).toString();
  timer = setTimeout(() => countMatches(seq, query), 200);
}
watch(() => practiceFilterQuery(filters.value).toString(), preview, {
  immediate: true,
});
watch(
  () => props.locked,
  (locked) => {
    if (locked) advanced.value = false;
  },
  { immediate: true },
);
onMounted(loadCatalog);
onBeforeUnmount(() => {
  ++sequence;
  clearTimeout(timer);
});
function reset() {
  filters.value = {};
}
</script>

<template>
  <section
    class="paper-card practice-filters"
    aria-labelledby="practice-filter-heading"
  >
    <div class="section-heading compact">
      <h2 id="practice-filter-heading">筛选题目</h2>
      <button
        v-if="locked"
        type="button"
        class="text-button inline"
        :aria-expanded="advanced"
        aria-controls="practice-filter-fields"
        @click="advanced = !advanced"
      >
        <ChevronDown :size="16" />{{ advanced ? "收起条件" : "查看条件" }}
      </button>
    </div>
    <p class="filter-explanation">
      {{
        daily
          ? locked
            ? "今日题目固定，明天可重新筛选。"
            : "筛选后打开题目，当天不再更换。"
          : pending
            ? "筛选尚未应用，当前题目和草稿保留。"
            : "点击“应用筛选”切换范围，草稿自动保留。"
      }}
    </p>
    <form @submit.prevent="canApply && emit('apply', filters)">
      <div v-if="locked && !advanced" class="filter-selection-summary">
        <span v-for="item in summary" :key="item" class="chip neutral">{{
          item
        }}</span>
        <span v-if="!summary.length" class="chip neutral">全部题库</span>
      </div>
      <fieldset
        id="practice-filter-fields"
        v-show="!locked || advanced"
        :disabled="busy || locked"
        class="practice-filter-fields"
      >
        <legend class="sr-only">题目筛选条件</legend>
        <div class="practice-filter-grid">
          <label class="field"
            >标签
            <select :value="filters.tag || ''" @change="update('tag', $event)">
              <option value="">不限标签</option>
              <option
                v-if="filters.tag && !catalog.tags.includes(filters.tag)"
                :value="filters.tag"
              >
                {{ filters.tag }}
              </option>
              <option v-for="tag in catalog.tags" :key="tag" :value="tag">
                {{ tag }}
              </option>
            </select>
          </label>
          <label class="field"
            >年份
            <select
              :value="filters.year || ''"
              @change="update('year', $event)"
            >
              <option value="">全部年份</option>
              <option
                v-if="
                  filters.year &&
                  !catalog.years.map(String).includes(filters.year)
                "
                :value="filters.year"
              >
                {{ filters.year }} 年
              </option>
              <option
                v-for="year in catalog.years"
                :key="year"
                :value="String(year)"
              >
                {{ year }} 年
              </option>
            </select>
          </label>
          <label class="field"
            >题型
            <select
              :value="filters.type || ''"
              @change="update('type', $event)"
            >
              <option value="">全部题型</option>
              <option value="blank">填空题</option>
              <option value="solution">解答题</option>
              <option value="choice">选择题</option>
            </select>
          </label>
        </div>
        <label class="field practice-filter-search">
          关键词
          <input
            :value="filters.q || ''"
            maxlength="200"
            placeholder="搜索题号、年份或标签"
            @input="update('q', $event)"
          />
        </label>
      </fieldset>
      <div v-if="catalogError" class="form-error" role="alert">
        {{ catalogError }}
        <button type="button" class="text-button" @click="loadCatalog">
          重试
        </button>
      </div>
      <div class="practice-filter-actions">
        <p v-if="!locked" class="filter-count" role="status">
          {{
            counting
              ? "查询中…"
              : countError ||
                (count === 0
                  ? "暂无匹配题目，请调整筛选。"
                  : `匹配：${count ?? "—"} 道题`)
          }}
          <button
            v-if="countError"
            type="button"
            class="text-button"
            @click="preview"
          >
            重试
          </button>
        </p>
        <p v-else class="filter-count">
          {{ opened ? "今日题目已打开" : "今日题目已分配" }}
        </p>
        <div class="inline">
          <button
            v-if="!locked"
            type="button"
            class="text-button"
            :disabled="busy"
            @click="reset"
          >
            重置条件
          </button>
          <button
            v-if="!daily || !opened"
            class="button primary"
            :disabled="!canApply"
          >
            {{
              busy
                ? "正在准备…"
                : daily
                  ? locked
                    ? "继续今日题目"
                    : "打开今日题目"
                  : "应用筛选"
            }}<ArrowRight :size="16" />
          </button>
        </div>
      </div>
    </form>
  </section>
</template>
