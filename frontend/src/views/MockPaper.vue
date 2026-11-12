<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleHelp,
  Clock,
  Eye,
  Sparkles,
  X,
} from "lucide-vue-next";
import { api, toast, typeName } from "../api";
import type { MockItem, MockPaper } from "../types";
import MarkdownContent from "../components/MarkdownContent.vue";
const route = useRoute();
const paper = ref<MockPaper>();
const loading = ref(true);
const error = ref("");
const busy = ref(false);
const reviewing = ref("");
const answers = reactive<Record<string, { text: string; options: string[] }>>(
  {},
);
const clock = ref(Date.now());
const startedAt = ref(Date.now());
let timer: ReturnType<typeof setInterval>;
const remaining = computed(() => {
  if (!paper.value || paper.value.status !== "ready") return 0;
  return Math.max(
    0,
    (paper.value.duration_minutes || 120) * 60000 -
      (clock.value - startedAt.value),
  );
});
const remainingText = computed(() => {
  const minutes = Math.floor(remaining.value / 60000);
  const seconds = Math.floor((remaining.value % 60000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
});
const review = computed(() => paper.value?.status === "submitted");
const sections = computed(() => {
  const groups: { type: string; items: MockItem[] }[] = [];
  for (const item of paper.value?.items || []) {
    const last = groups[groups.length - 1];
    if (last && last.type === item.type) last.items.push(item);
    else groups.push({ type: item.type, items: [item] });
  }
  return groups;
});
const knowledgeNames = computed(() => {
  const names: Record<string, string> = {};
  for (const item of paper.value?.profile.knowledge || [])
    names[item.code] = item.name;
  for (const item of paper.value?.knowledge_summary || [])
    names[item.code] = item.name;
  return names;
});
function knowledgeName(code: string) {
  return knowledgeNames.value[code] || code;
}
function answerFor(item: MockItem) {
  return answers[item.id] || { text: "", options: [] };
}
function ensure(item: MockItem) {
  if (!answers[item.id]) answers[item.id] = { text: "", options: [] };
  return answers[item.id];
}
function toggleOption(item: MockItem, option: string) {
  const current = ensure(item);
  if (item.multiple) {
    current.options = current.options.includes(option)
      ? current.options.filter((value) => value !== option)
      : [...current.options, option];
  } else {
    current.options = [option];
  }
}
const answeredCount = computed(
  () =>
    (paper.value?.items || []).filter((item) => {
      const answer = answerFor(item);
      return !!answer.text.trim() || answer.options.length > 0;
    }).length,
);
const percent = computed(() =>
  paper.value && paper.value.max_score
    ? Math.round((paper.value.score / paper.value.max_score) * 100)
    : 0,
);
async function load() {
  loading.value = true;
  error.value = "";
  try {
    paper.value = await api<MockPaper>(
      "/mock/papers/" + encodeURIComponent(String(route.params.id)),
    );
    for (const item of paper.value.items) {
      if (!answers[item.id]) answers[item.id] = { text: "", options: [] };
    }
    startedAt.value = Date.now();
    clock.value = Date.now();
    if (timer) clearInterval(timer);
    timer = setInterval(() => (clock.value = Date.now()), 1000);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
onMounted(load);
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});
async function submit() {
  const current = paper.value;
  if (!current) return;
  const unanswered = current.items.length - answeredCount.value;
  const message = unanswered
    ? `还有 ${unanswered} 题未作答，确定交卷？交卷后不能修改。`
    : "确定交卷？交卷后不能修改。";
  if (!window.confirm(message)) return;
  busy.value = true;
  try {
    const payload = current.items.map((item) => ({
      item_id: item.id,
      text: answerFor(item).text,
      options: answerFor(item).options,
    }));
    paper.value = await api<MockPaper>(
      "/mock/papers/" + encodeURIComponent(current.id) + "/submission",
      {
        method: "PUT",
        body: JSON.stringify({ answers: payload }),
        signal: AbortSignal.timeout(240000),
      },
    );
    window.scrollTo({ top: 0, behavior: "smooth" });
    toast("已交卷，下面是判分与解析");
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    busy.value = false;
  }
}
async function mark(item: MockItem, result: "correct" | "incorrect") {
  const current = paper.value;
  if (!current) return;
  reviewing.value = item.id;
  try {
    paper.value = await api<MockPaper>(
      "/mock/papers/" +
        encodeURIComponent(current.id) +
        "/items/" +
        encodeURIComponent(item.id),
      {
        method: "PATCH",
        body: JSON.stringify({ result }),
        signal: AbortSignal.timeout(60000),
      },
    );
    toast(result === "correct" ? "已记为正确" : "已记为错误");
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    reviewing.value = "";
  }
}
function resultText(item: MockItem) {
  if (item.result === "correct") return "正确";
  if (item.result === "incorrect") return "错误";
  if (item.result === "pending") return "待自评";
  return "未作答";
}
function correctAnswer(item: MockItem) {
  if (item.type === "choice")
    return "正确选项：" + (item.correct_options || []).join("、");
  return "参考答案：" + (item.answer_text || "暂无");
}
</script>
<template>
  <div class="practice-page">
    <RouterLink to="/mock" class="back-link"
      ><ArrowLeft :size="16" />返回考试预测</RouterLink
    >
    <div v-if="loading" class="loading-panel">加载中…</div>
    <div v-else-if="error" class="empty-state">
      <h2>模拟卷暂时无法打开</h2>
      <p>{{ error }}</p>
      <button class="button" @click="load">重新加载</button>
    </div>
    <template v-else-if="paper">
      <div class="page-heading">
        <div>
          <h1>{{ paper.title }}</h1>
          <p class="mock-meta">
            <span class="chip">{{ paper.subject }}</span>
            <span class="chip neutral"
              >{{ paper.items.length }} 题 · {{ paper.max_score }} 分</span
            >
            <span class="chip neutral"
              ><Clock :size="14" />{{ paper.duration_minutes }} 分钟</span
            >
            <span v-if="paper.ai_items" class="chip neutral"
              ><Sparkles :size="14" />{{ paper.ai_items }} 道 AI 仿题</span
            >
          </p>
        </div>
        <div
          v-if="!review"
          class="mock-timer"
          :class="{ urgent: remaining < 300000 }"
        >
          <Clock :size="18" />{{ remainingText }}
        </div>
      </div>
      <section v-if="!review" class="paper-card mock-notice">
        <p>
          共 {{ paper.items.length }} 题，已作答
          {{ answeredCount }} 题。交卷后显示参考答案、解析与按知识点统计的得分。
          <template v-if="paper.ai_items">
            AI
            仿题由模型依据同类往年题命制，已通过独立求解校验；如公式或答案有误，请按解析核对并反馈。
          </template>
        </p>
        <div class="editor-actions">
          <span class="small-text muted"
            >自动判分：选择、填空；解答题由 AI 判分，不可用时转自评。</span
          >
          <button class="button primary" :disabled="busy" @click="submit">
            <ArrowRight :size="16" />{{ busy ? "判分中…" : "交卷" }}
          </button>
        </div>
      </section>
      <section v-else class="paper-card score-hero">
        <div>
          <small class="muted">本次得分</small>
          <strong
            >{{ paper.score }}<span> / {{ paper.max_score }}</span></strong
          >
          <p class="small-text muted">
            正确率 {{ percent }}% ·
            {{
              paper.submitted_at
                ? paper.submitted_at.slice(0, 16).replace("T", " ")
                : ""
            }}
          </p>
        </div>
        <div v-if="paper.knowledge_summary.length" class="score-knowledge">
          <div
            v-for="point in paper.knowledge_summary"
            :key="point.code"
            class="score-point"
          >
            <span>{{ point.name }}</span>
            <div class="accuracy-bar">
              <i
                :style="{
                  width:
                    Math.round((point.score / Math.max(1, point.max)) * 100) +
                    '%',
                }"
              ></i>
            </div>
            <small>{{ point.score }} / {{ point.max }}</small>
          </div>
        </div>
      </section>
      <section
        v-if="paper.blueprint.rationale || paper.profile.welcome"
        class="paper-card"
      >
        <div class="section-heading compact">
          <h2><Eye :size="18" /> 预测依据</h2>
        </div>
        <p class="small-text">{{ paper.profile.welcome }}</p>
        <p v-if="paper.blueprint.rationale" class="small-text muted">
          {{ paper.blueprint.rationale }}
        </p>
        <div v-if="paper.blueprint.emphasis?.length" class="knowledge-row">
          <span
            v-for="point in paper.blueprint.emphasis"
            :key="point.code"
            class="knowledge-chip"
            :title="point.reason"
            >{{ point.name }}</span
          >
        </div>
      </section>
      <section
        v-for="(section, sectionIndex) in sections"
        :key="section.type + sectionIndex"
        class="mock-section"
      >
        <div class="section-heading">
          <div>
            <h2>{{ typeName(section.type) }}</h2>
          </div>
          <span class="chip neutral"
            >{{ section.items.length }} 题 ·
            {{ section.items[0].points }} 分/题</span
          >
        </div>
        <article
          v-for="item in section.items"
          :key="item.id"
          class="paper-card mock-item"
          :class="{
            'mock-item-correct': item.result === 'correct',
            'mock-item-wrong': item.result === 'incorrect',
          }"
        >
          <div class="mock-item-heading">
            <strong>第 {{ item.position }} 题</strong>
            <span class="chip neutral">{{ item.points }} 分</span>
            <span v-if="item.origin === 'ai'" class="chip ai-chip"
              ><Sparkles :size="13" />AI 仿题</span
            >
            <span
              v-if="review"
              class="chip"
              :class="
                item.result === 'correct'
                  ? 'success-chip'
                  : item.result === 'incorrect'
                    ? 'error-chip'
                    : 'neutral'
              "
              >{{ resultText(item) }}</span
            >
            <span v-if="review" class="small-text muted"
              >{{ item.score }} / {{ item.points }} 分</span
            >
          </div>
          <div v-if="item.knowledge_points.length" class="knowledge-row">
            <span
              v-for="code in item.knowledge_points"
              :key="code"
              class="knowledge-chip"
              >{{ knowledgeName(code) }}</span
            >
          </div>
          <MarkdownContent :source="item.question_text" />
          <div v-if="!review" class="mock-answer">
            <div v-if="item.type === 'choice'" class="option-list">
              <label v-for="option in item.choice_options" :key="option"
                ><input
                  :checked="answerFor(item).options.includes(option)"
                  :type="item.multiple ? 'checkbox' : 'radio'"
                  :name="'mock-' + item.id"
                  :value="option"
                  @change="toggleOption(item, option)"
                /><span>{{ option }}</span></label
              >
            </div>
            <textarea
              v-else
              v-model="ensure(item).text"
              rows="4"
              maxlength="8000"
              :placeholder="
                item.type === 'blank' ? '填写最终答案' : '写出关键步骤与结果'
              "
            ></textarea>
          </div>
          <template v-else>
            <div class="mock-review-block">
              <p class="small-text muted">
                我的作答：
                {{
                  item.type === "choice"
                    ? answerFor(item).options.join("、") || "未作答"
                    : answerFor(item).text || "未作答"
                }}
              </p>
              <p class="mock-correct-answer">
                {{ correctAnswer(item) }}
              </p>
              <p
                v-if="
                  item.origin === 'ai' &&
                  item.correct_options?.length &&
                  item.type === 'choice'
                "
                class="small-text muted"
              >
                正确选项内容：{{ item.answer_text }}
              </p>
              <MarkdownContent
                v-if="item.explanation"
                :source="item.explanation"
              />
              <p v-if="item.feedback" class="small-text muted">
                {{ item.feedback }}
              </p>
              <div v-if="item.result === 'pending'" class="mock-self-check">
                <span class="small-text"
                  ><CircleHelp :size="15" /> 对照答案后自评：</span
                >
                <button
                  class="button small success"
                  :disabled="reviewing === item.id"
                  @click="mark(item, 'correct')"
                >
                  <Check :size="15" />正确
                </button>
                <button
                  class="button small assessment-incorrect"
                  :disabled="reviewing === item.id"
                  @click="mark(item, 'incorrect')"
                >
                  <X :size="15" />错误
                </button>
              </div>
            </div>
          </template>
        </article>
      </section>
      <div v-if="!review" class="editor-actions mock-submit-bottom">
        <button class="button primary" :disabled="busy" @click="submit">
          <ArrowRight :size="16" />{{ busy ? "判分中…" : "交卷并查看解析" }}
        </button>
      </div>
      <div v-else class="editor-actions">
        <RouterLink to="/mock" class="button"
          ><ArrowLeft :size="16" />返回考试预测</RouterLink
        >
        <RouterLink to="/mock" class="button primary"
          >再生成一张<ArrowRight :size="16"
        /></RouterLink>
      </div>
    </template>
  </div>
</template>
<style scoped>
.mock-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
}
.mock-timer {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  font-variant-numeric: tabular-nums;
  background: #fff;
}
.mock-timer.urgent {
  color: #a6392f;
  border-color: #e7c4bf;
  background: #fdf4f2;
}
.mock-notice p {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.8;
}
.score-hero {
  display: grid;
  grid-template-columns: minmax(160px, 220px) 1fr;
  gap: 24px;
  align-items: center;
}
.score-hero strong {
  display: block;
  font-size: 42px;
  line-height: 1.1;
}
.score-hero strong span {
  font-size: 16px;
  color: var(--muted);
}
.score-knowledge {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}
.score-point {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.score-point .accuracy-bar {
  margin: 0;
}
.mock-section {
  margin-top: 22px;
}
.mock-item {
  margin-bottom: 12px;
}
.mock-item-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.ai-chip {
  color: #6b4a12;
  border-color: #e3cd9a;
  background: #fdf6e3;
}
.success-chip {
  color: #24624d;
  border-color: #bfdccd;
  background: #eef8f2;
}
.error-chip {
  color: #8a3128;
  border-color: #e7c4bf;
  background: #fdf1ef;
}
.mock-item-correct {
  border-left: 3px solid #3d7d62;
}
.mock-item-wrong {
  border-left: 3px solid #b4503f;
}
.mock-answer {
  margin-top: 10px;
}
.mock-answer textarea {
  width: 100%;
  min-height: 96px;
}
.mock-review-block {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e4e9dd;
}
.mock-correct-answer {
  margin: 6px 0;
  font-weight: 600;
  color: var(--green-dark);
}
.mock-self-check {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.mock-self-check .small-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.mock-submit-bottom {
  margin-top: 8px;
}
@media (max-width: 640px) {
  .score-hero {
    grid-template-columns: 1fr;
  }
}
</style>
