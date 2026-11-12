<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  ArrowRight,
  ClipboardList,
  RefreshCw,
  Sparkles,
  Target,
  Trash2,
} from "lucide-vue-next";
import { api, state, toast, typeName } from "../api";
import type {
  MockPaper,
  MockPaperSummary,
  MockSubject,
  MockSubjectsResponse,
} from "../types";
const router = useRouter();
const subjects = ref<MockSubject[]>([]);
const papers = ref<MockPaperSummary[]>([]);
const aiReady = ref(false);
const loading = ref(true);
const creating = ref("");
const error = ref("");
const difficulty = ref("standard");
const difficulties = [
  { value: "standard", label: "接近往年" },
  { value: "easier", label: "偏基础" },
  { value: "harder", label: "偏综合" },
];
function yearsLabel(item: MockSubject) {
  if (!item.years.length) return "暂无年份";
  const newest = item.years[0];
  const oldest = item.years[item.years.length - 1];
  return (newest === oldest ? String(newest) : `${oldest}–${newest}`) + " 年";
}
function typeMix(item: MockSubject) {
  return ["choice", "blank", "solution"]
    .map((kind) => `${typeName(kind)} ${item.type_mix[kind] || 0}`)
    .join(" · ");
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [catalog, mine] = await Promise.all([
      api<MockSubjectsResponse>("/mock/subjects"),
      state.user
        ? api<{ items: MockPaperSummary[] }>("/mock/papers")
        : Promise.resolve({ items: [] }),
    ]);
    subjects.value = catalog.subjects;
    aiReady.value = catalog.ai;
    papers.value = mine.items;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
onMounted(load);
async function create(subject: string) {
  if (!state.user) {
    router.push("/auth?next=/mock");
    return;
  }
  if (!aiReady.value) {
    toast("AI 生题未配置，暂时无法组卷。", true);
    return;
  }
  creating.value = subject;
  try {
    const paper = await api<MockPaper>("/mock/papers", {
      method: "POST",
      body: JSON.stringify({ subject, difficulty: difficulty.value }),
      signal: AbortSignal.timeout(240000),
    });
    await router.push("/mock/" + paper.id);
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    creating.value = "";
  }
}
async function remove(paper: MockPaperSummary) {
  if (!window.confirm("删除这张模拟卷？已交卷的解析也会一起删除。")) return;
  try {
    await api("/mock/papers/" + encodeURIComponent(paper.id), {
      method: "DELETE",
    });
    papers.value = papers.value.filter((item) => item.id !== paper.id);
    toast("模拟卷已删除");
  } catch (e) {
    toast((e as Error).message, true);
  }
}
function scoreText(paper: MockPaperSummary) {
  return paper.status === "submitted"
    ? `${paper.score} / ${paper.max_score} 分`
    : "未交卷";
}
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>考试预测</h1>
      <p class="page-subtitle">
        按北交期末往年卷的知识点分布、题型与难度预测下一张卷，生成可在线作答的模拟卷。
      </p>
    </div>
    <Target :size="30" />
  </div>
  <section class="paper-card prediction-note">
    <div class="inline">
      <Sparkles :size="20" />
      <strong>预测怎么来的</strong>
    </div>
    <p>
      先统计每科近 6 年的题型结构、知识点权重与难度，再由模型给出预测蓝图；
      题目优先从现有题库按知识点挑选，每张卷至少包含一道 AI
      仿题（同思路、换数据，并经独立求解校验），因此每张卷都有一道新题可练。
    </p>
    <p v-if="!aiReady" class="form-warning">
      当前环境未配置 AI 生题，暂时只能浏览预测分析，无法生成模拟卷。
    </p>
  </section>
  <div v-if="loading" class="loading-panel">加载中…</div>
  <div v-else-if="error" class="empty-state">
    <p>{{ error }}</p>
    <button class="button" @click="load">重试</button>
  </div>
  <template v-else>
    <div class="section-heading">
      <div>
        <h2>选择科目</h2>
      </div>
      <label class="inline mock-difficulty"
        >难度
        <select v-model="difficulty">
          <option
            v-for="item in difficulties"
            :key="item.value"
            :value="item.value"
          >
            {{ item.label }}
          </option>
        </select>
      </label>
    </div>
    <div class="subject-grid">
      <article v-for="item in subjects" :key="item.subject" class="paper-card">
        <div class="subject-heading">
          <h3>{{ item.subject }}</h3>
          <span class="chip neutral">{{ yearsLabel(item) }}</span>
        </div>
        <p class="small-text muted">
          {{ item.problems }} 道往年题 · {{ typeMix(item) }}
        </p>
        <div class="knowledge-row">
          <span
            v-for="point in item.knowledge.slice(0, 4)"
            :key="point.code"
            class="knowledge-chip"
            >{{ point.name }}</span
          >
        </div>
        <p v-if="item.difficulty.sample" class="small-text muted">
          平均难度 {{ item.difficulty.mean }} / 5（按内部标注估算）
        </p>
        <button
          class="button primary"
          :disabled="!item.ready || !aiReady || !!creating"
          @click="create(item.subject)"
        >
          <Sparkles :size="16" />
          {{
            creating === item.subject
              ? "预测与组卷中…"
              : item.ready
                ? "生成预测卷"
                : "题量不足"
          }}
        </button>
        <small v-if="creating === item.subject" class="muted">
          需要约 30–90 秒：分析 → 组卷 → AI 生题 → 校验可解性。
        </small>
      </article>
      <div v-if="!subjects.length" class="empty-state">
        <ClipboardList :size="32" />
        <h3>暂无可预测的科目</h3>
        <p>需要先完成北交期末题库导入与知识点标注。</p>
      </div>
    </div>
    <section class="my-papers">
      <div class="section-heading">
        <div>
          <h2>我的模拟卷</h2>
        </div>
        <button class="text-button inline" :disabled="loading" @click="load">
          <RefreshCw :size="15" />刷新
        </button>
      </div>
      <div v-if="!papers.length" class="community-empty">
        还没有模拟卷。选一个科目生成第一张卷。
      </div>
      <article
        v-for="paper in papers"
        :key="paper.id"
        class="paper-card mock-paper-row"
      >
        <div class="mock-paper-main">
          <RouterLink :to="'/mock/' + paper.id">
            <strong>{{ paper.title }}</strong>
          </RouterLink>
          <p class="small-text muted">
            {{ paper.subject }} · {{ paper.duration_minutes }} 分钟 ·
            {{ paper.ai_items }} 道 AI 仿题 ·
            {{ paper.created_at.slice(0, 10) }}
          </p>
        </div>
        <span
          class="chip"
          :class="paper.status === 'submitted' ? '' : 'neutral'"
        >
          {{ scoreText(paper) }}
        </span>
        <RouterLink :to="'/mock/' + paper.id" class="button small">
          {{ paper.status === "submitted" ? "查看解析" : "继续作答" }}
          <ArrowRight :size="15" />
        </RouterLink>
        <button
          class="icon-button"
          aria-label="删除模拟卷"
          @click="remove(paper)"
        >
          <Trash2 :size="16" />
        </button>
      </article>
    </section>
  </template>
</template>
<style scoped>
.page-subtitle {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}
.prediction-note {
  margin-bottom: 18px;
}
.prediction-note p {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--ink);
}
.subject-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 14px;
  margin-bottom: 26px;
}
.subject-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.subject-heading h3 {
  margin: 0;
  font-size: 17px;
}
.subject-grid .button {
  margin-top: 10px;
  width: 100%;
}
.mock-difficulty {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}
.mock-paper-row {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 10px;
}
.mock-paper-main {
  flex: 1;
  min-width: 0;
}
.mock-paper-main p {
  margin: 4px 0 0;
}
</style>
