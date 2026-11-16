<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  BookOpen,
  Check,
  Flag,
  Sun,
  ArrowRight,
  Heart,
  Bell,
  Sparkles,
  RefreshCw,
} from "lucide-vue-next";
import { api, write, loadMe, state, toast, statusName } from "../api";
import type {
  AiKeyStatus,
  Analysis,
  Problem,
  Solution,
  Notification,
} from "../types";
import ProblemCard from "../components/ProblemCard.vue";
const route = useRoute(),
  router = useRouter();
const tab = ref(String(route.query.tab || "history"));
const loading = ref(true),
  error = ref(""),
  items = ref<Problem[]>([]),
  solutions = ref<Solution[]>([]),
  notifications = ref<Notification[]>([]),
  page = ref(1),
  total = ref(0);
const analysis = ref<Analysis>();
const reportBusy = ref(false);
const aiKey = ref<AiKeyStatus>();
const aiKeyInput = ref("");
const aiKeySaving = ref(false);
const nickname = ref("");
const settings = ref(false),
  saving = ref(false);
const mastering = ref<string[]>([]);
const domainNames: Record<string, string> = {
  calculus: "高等数学",
  linear_algebra: "线性代数",
  probability: "概率论",
};
function domainName(value: string) {
  return domainNames[value] || value;
}
function percent(value: number | null) {
  return value === null ? "—" : Math.round(value * 100) + "%";
}
function accuracyWidth(value: number | null) {
  return (value === null ? 0 : Math.max(4, Math.round(value * 100))) + "%";
}
const tabs = [
  { id: "history", label: "练习记录" },
  { id: "mistakes", label: "错题" },
  { id: "analysis", label: "学情分析" },
  { id: "likes", label: "喜欢" },
  { id: "solutions", label: "我的题解" },
  { id: "notifications", label: "消息" },
  { id: "ai", label: "AI 设置" },
];
async function loadAnalysis() {
  analysis.value = await api<Analysis>("/me/analysis");
}
async function loadAiKey() {
  aiKey.value = await api<AiKeyStatus>("/me/ai-key");
}
async function saveAiKey() {
  if (!aiKeyInput.value.trim()) return;
  aiKeySaving.value = true;
  try {
    aiKey.value = await write<AiKeyStatus>(
      "/me/ai-key",
      { api_key: aiKeyInput.value.trim() },
      "PUT",
    );
    aiKeyInput.value = "";
    toast("API 密钥已保存");
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    aiKeySaving.value = false;
  }
}
async function removeAiKey() {
  if (!window.confirm("删除已保存的 API 密钥？删除后 AI 功能将不可用。"))
    return;
  try {
    aiKey.value = await api<AiKeyStatus>("/me/ai-key", { method: "DELETE" });
    toast("API 密钥已删除");
  } catch (e) {
    toast((e as Error).message, true);
  }
}
async function generateReport(refresh = false) {
  reportBusy.value = true;
  try {
    analysis.value = await api<Analysis>(
      "/me/analysis" + (refresh ? "?refresh=true" : ""),
      { method: "POST", signal: AbortSignal.timeout(180000) },
    );
    toast("AI 诊断已更新");
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    reportBusy.value = false;
  }
}
async function saveProfile() {
  saving.value = true;
  try {
    state.user = await write(
      "/me/profile",
      { nickname: nickname.value },
      "PATCH",
    );
    settings.value = false;
    toast("昵称已更新");
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    saving.value = false;
  }
}
async function logout() {
  try {
    await api("/auth/logout", { method: "POST" });
    state.user = null;
    router.push("/");
    toast("已退出登录");
  } catch (e) {
    toast((e as Error).message, true);
  }
}
async function master(p: Problem) {
  mastering.value.push(p.id);
  try {
    await api("/me/mistakes/" + encodeURIComponent(p.id), { method: "DELETE" });
    items.value = items.value.filter((item) => item.id !== p.id);
    total.value = Math.max(0, total.value - 1);
    state.stats.mistakes = Math.max(0, state.stats.mistakes - 1);
    toast("已移出错题");
    if (page.value > 1 && page.value > Math.ceil(total.value / 12)) {
      page.value--;
    } else {
      await load();
    }
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    mastering.value = mastering.value.filter((id) => id !== p.id);
  }
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    await loadMe();
    if (!state.user) return;
    items.value = [];
    solutions.value = [];
    if (tab.value === "analysis") {
      await loadAnalysis();
    } else if (tab.value === "ai") {
      await loadAiKey();
    } else if (tab.value === "notifications") {
      notifications.value = await api("/me/notifications");
    } else if (tab.value === "solutions") {
      solutions.value = await api("/me/solutions");
    } else if (tab.value === "likes") {
      const data = await api("/me/likes");
      items.value = data.problems;
      solutions.value = data.solutions;
      total.value = items.value.length;
    } else {
      const data = await api(
        "/me/history?scope=" +
          (tab.value === "mistakes" ? "mistakes" : "all") +
          "&page=" +
          page.value,
      );
      items.value = data.items;
      total.value = data.total;
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
onMounted(load);
watch(tab, () => {
  page.value = 1;
  if (tab.value === "analysis") analysis.value = undefined;
  router.replace({ query: { tab: tab.value } });
  load();
});
watch(page, load);
async function read(n: Notification) {
  try {
    await api("/me/notifications/" + encodeURIComponent(n.id), {
      method: "PATCH",
      body: "{}",
    });
    n.is_read = true;
  } catch (e) {
    toast((e as Error).message, true);
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>我的学习</h1>
    </div>
    <RouterLink to="/practice" class="button primary"
      >继续练习<ArrowRight :size="16"
    /></RouterLink>
  </div>
  <div v-if="!state.user && !loading" class="empty-state">
    <BookOpen :size="35" />
    <h2>请先登录</h2>
    <p>登录后查看作答、错题和喜欢的内容。</p>
    <RouterLink to="/auth?next=/me" class="button primary">登录</RouterLink>
  </div>
  <template v-else
    ><div v-if="state.user" class="profile-bar">
      <span>{{ state.user.nickname }} · {{ state.user.email }}</span>
      <div class="inline">
        <button
          class="text-button"
          @click="
            settings = !settings;
            nickname = state.user.nickname;
          "
        >
          编辑昵称</button
        ><button class="text-button" @click="logout">退出登录</button>
      </div>
    </div>
    <form v-if="settings" class="profile-form" @submit.prevent="saveProfile">
      <label class="field"
        >昵称<input v-model.trim="nickname" required maxlength="30" /></label
      ><button class="button" :disabled="saving">保存</button>
    </form>
    <div class="stats-grid">
      <div>
        <BookOpen :size="21" /><strong>{{ state.stats.practiced }}</strong
        ><span>已练习</span>
      </div>
      <div>
        <Check :size="21" /><strong>{{ state.stats.correct }}</strong
        ><span>曾做对</span>
      </div>
      <div>
        <Flag :size="21" /><strong>{{ state.stats.mistakes }}</strong
        ><span>待复习错题</span>
      </div>
      <div>
        <Sun :size="21" /><strong>{{ state.stats.checkins }}</strong
        ><span>打卡天数</span>
      </div>
    </div>
    <div class="tabs" role="tablist" aria-label="学习内容">
      <button
        v-for="t in tabs"
        :key="t.id"
        role="tab"
        :aria-selected="tab === t.id"
        :class="{ selected: tab === t.id }"
        @click="tab = t.id"
      >
        {{ t.label }}
      </button>
    </div>
    <p v-if="tab === 'mistakes'" class="muted small-text">
      答错或点过“不会”的题目在这里复习，掌握后可移出。
    </p>
    <div v-if="loading" class="loading-panel">加载中…</div>
    <div v-else-if="error" class="empty-state">
      <p>{{ error }}</p>
      <button class="button" @click="load">重试</button>
    </div>
    <template v-else-if="tab === 'ai'">
      <section class="paper-card ai-key-panel">
        <div class="section-heading compact">
          <div><h2>DeepSeek API 密钥</h2></div>
          <span class="chip neutral">{{
            aiKey?.configured ? "已配置" : "未配置"
          }}</span>
        </div>
        <p class="small-text muted">
          AI 判分、学情分析、考试预测与一题多练都使用你自己的密钥调用
          DeepSeek。密钥加密保存在服务器，页面只显示末四位，接口不会返回完整密钥。
        </p>
        <p v-if="aiKey?.reason" class="form-warning">{{ aiKey.reason }}</p>
        <p v-if="aiKey?.configured" class="small-text">
          当前：{{ aiKey.hint }}
          <span v-if="aiKey.updated_at"
            >· {{ aiKey.updated_at.slice(0, 19).replace("T", " ") }} 更新</span
          >
        </p>
        <form class="ai-key-form" @submit.prevent="saveAiKey">
          <label class="field"
            >新的 API 密钥<input
              v-model.trim="aiKeyInput"
              type="password"
              autocomplete="off"
              spellcheck="false"
              placeholder="sk-..."
              required
          /></label>
          <div class="ai-key-actions">
            <button class="button" :disabled="aiKeySaving">
              {{
                aiKeySaving
                  ? "保存中…"
                  : aiKey?.configured
                    ? "更换密钥"
                    : "保存密钥"
              }}
            </button>
            <button
              v-if="aiKey?.configured"
              type="button"
              class="text-button"
              @click="removeAiKey"
            >
              删除密钥
            </button>
          </div>
        </form>
        <p class="small-text muted">
          只支持 DeepSeek 官方密钥（sk-
          开头）。请不要填写他人的密钥；密钥只用于你发起的 AI 请求，可随时删除。
        </p>
      </section>
    </template>
    <template v-else-if="tab === 'analysis'">
      <div v-if="!analysis" class="loading-panel">正在统计…</div>
      <template v-else>
        <div class="analysis-summary">
          <div>
            <strong>{{ analysis.stats.totals.practiced }}</strong
            ><span>已练习</span>
          </div>
          <div>
            <strong>{{ analysis.stats.totals.correct }}</strong
            ><span>当前做对</span>
          </div>
          <div>
            <strong>{{ analysis.stats.totals.incorrect }}</strong
            ><span>当前做错</span>
          </div>
          <div>
            <strong>{{ analysis.stats.totals.mistakes }}</strong
            ><span>待复习错题</span>
          </div>
        </div>
        <div v-if="!analysis.stats.knowledge.length" class="empty-state">
          <BookOpen :size="32" />
          <h3>还没有可分析的练习记录</h3>
          <p>做几道带知识点的题目后再来看；已做过的题会自动计入统计。</p>
          <RouterLink to="/practice" class="button"
            >去练习<ArrowRight :size="16"
          /></RouterLink>
        </div>
        <template v-else>
          <section class="paper-card">
            <div class="section-heading compact">
              <div>
                <h2>最需加强</h2>
              </div>
              <span class="chip neutral">按正确率与错题数排序</span>
            </div>
            <div class="weak-grid">
              <article
                v-for="item in analysis.stats.weakest"
                :key="item.code"
                class="weak-card"
              >
                <h3>{{ item.name }}</h3>
                <p class="small-text muted">
                  正确率 {{ percent(item.accuracy) }} · 已判分
                  {{ item.graded }} 题 · 错题 {{ item.mistakes }}
                </p>
                <div class="accuracy-bar" aria-hidden="true">
                  <i :style="{ width: accuracyWidth(item.accuracy) }"></i>
                </div>
                <RouterLink
                  :to="'/practice?knowledge=' + item.code"
                  class="text-button inline"
                  >针对练习<ArrowRight :size="15"
                /></RouterLink>
              </article>
            </div>
          </section>
          <section class="paper-card">
            <div class="section-heading">
              <div>
                <h2>知识点掌握</h2>
              </div>
              <span class="chip neutral"
                >{{ analysis.stats.knowledge.length }} 个知识点</span
              >
            </div>
            <div class="analysis-table-wrap">
              <table class="analysis-table">
                <thead>
                  <tr>
                    <th scope="col">知识点</th>
                    <th scope="col">正确率</th>
                    <th scope="col">已判分</th>
                    <th scope="col">错题</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in analysis.stats.knowledge" :key="item.code">
                    <td>
                      <strong>{{ item.name }}</strong>
                      <br />
                      <small class="muted">{{ domainName(item.domain) }}</small>
                    </td>
                    <td>
                      <div class="accuracy-bar" aria-hidden="true">
                        <i :style="{ width: accuracyWidth(item.accuracy) }"></i>
                      </div>
                      <small>{{ percent(item.accuracy) }}</small>
                    </td>
                    <td>{{ item.graded }}</td>
                    <td>{{ item.mistakes }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p
              v-if="analysis.stats.variant_totals.practiced"
              class="small-text muted"
            >
              一题多练：做过
              {{ analysis.stats.variant_totals.practiced }} 道变式题， 做对
              {{ analysis.stats.variant_totals.correct }} 道。
            </p>
          </section>
          <section v-if="analysis.stats.mock.length" class="paper-card">
            <div class="section-heading compact">
              <div>
                <h2>模拟卷成绩</h2>
              </div>
              <span class="chip neutral"
                >{{ analysis.stats.mock.length }} 次</span
              >
            </div>
            <div
              v-for="paper in analysis.stats.mock"
              :key="paper.paper_id"
              class="mock-score-row"
            >
              <div class="mock-score-heading">
                <RouterLink :to="'/mock/' + paper.paper_id"
                  ><strong>{{ paper.title }}</strong></RouterLink
                >
                <span class="chip neutral"
                  >{{ paper.score }} / {{ paper.max_score }} 分</span
                >
              </div>
              <div class="knowledge-row">
                <span
                  v-for="point in paper.knowledge"
                  :key="point.code"
                  class="knowledge-chip"
                  >{{ point.name }} {{ point.score }}/{{ point.max }}</span
                >
              </div>
            </div>
          </section>
          <section class="paper-card ai-analysis">
            <div class="section-heading compact">
              <div>
                <h2><Sparkles :size="18" /> AI 学情诊断</h2>
              </div>
              <button
                v-if="analysis.available"
                class="button small"
                :disabled="reportBusy"
                @click="generateReport(!!analysis.report)"
              >
                <RefreshCw :size="15" />{{
                  reportBusy
                    ? "生成中…"
                    : analysis.report
                      ? "重新生成"
                      : "生成 AI 诊断"
                }}
              </button>
            </div>
            <template v-if="analysis.report">
              <p class="ai-summary">{{ analysis.report.summary }}</p>
              <div v-if="analysis.report.strengths.length" class="report-block">
                <h3>相对掌握较好</h3>
                <ul>
                  <li
                    v-for="item in analysis.report.strengths"
                    :key="item.code"
                  >
                    <strong>{{ item.name }}</strong
                    >：{{ item.reason }}
                  </li>
                </ul>
              </div>
              <div
                v-if="analysis.report.weaknesses.length"
                class="report-block"
              >
                <h3>需要重点补的</h3>
                <ul>
                  <li
                    v-for="item in analysis.report.weaknesses"
                    :key="item.code"
                  >
                    <strong>{{ item.name }}</strong
                    >：{{ item.reason }}
                    <span v-if="item.suggestion"
                      ><br /><em>建议：{{ item.suggestion }}</em></span
                    >
                  </li>
                </ul>
              </div>
              <div v-if="analysis.report.plan.length" class="report-block">
                <h3>一周练习计划</h3>
                <ol>
                  <li
                    v-for="(step, index) in analysis.report.plan"
                    :key="index"
                  >
                    {{ step }}
                  </li>
                </ol>
              </div>
              <p class="small-text muted">
                由 {{ analysis.report.model || "模型" }} 生成 ·
                仅依据你的练习统计
              </p>
            </template>
            <p v-else-if="!analysis.available" class="muted">
              {{ analysis.reason || "AI 诊断暂不可用，先看上面的统计。" }}
            </p>
            <p v-else class="muted small-text">
              点击“生成 AI
              诊断”，模型会结合正确率与错题分布指出薄弱知识点，并给出复习计划。
            </p>
          </section>
        </template>
      </template>
    </template>
    <template v-else
      ><div v-if="items.length" class="card-grid">
        <div v-for="p in items" :key="p.id" class="learning-item">
          <ProblemCard :problem="p" />
          <button
            v-if="tab === 'mistakes'"
            class="button small"
            :disabled="mastering.includes(p.id)"
            @click="master(p)"
          >
            <Check :size="16" />已掌握，移出错题
          </button>
        </div>
      </div>
      <div v-for="s in solutions" :key="s.id" class="paper-card saved-solution">
        <div class="inline">
          <span class="chip">{{ statusName(s.status) }}</span
          ><span>{{ s.author }}</span>
        </div>
        <p class="preserve-text">{{ s.body || "图片题解" }}</p>
        <RouterLink :to="'/problems/' + s.problem_id" class="text-button inline"
          >查看题目<ArrowRight :size="15"
        /></RouterLink>
      </div>
      <div v-if="tab === 'notifications'" class="notification-list">
        <button
          v-for="n in notifications"
          :key="n.id"
          class="notification"
          :class="{ unread: !n.is_read }"
          @click="read(n)"
        >
          <Bell :size="20" /><span
            >{{ n.event === "like" ? "题解收到点赞" : "题解收到评论"
            }}<small
              >{{ n.created_at.slice(0, 16).replace("T", " ") }} ·
              {{ n.is_read ? "已读" : "点击标记已读" }}</small
            ></span
          >
        </button>
      </div>
      <div
        v-if="
          !items.length &&
          !solutions.length &&
          (tab !== 'notifications' || !notifications.length)
        "
        class="empty-state"
      >
        <Heart v-if="tab === 'likes'" :size="30" /><BookOpen
          v-else
          :size="30"
        />
        <h3>
          {{
            tab === "notifications"
              ? "暂无消息"
              : tab === "solutions"
                ? "暂无题解"
                : tab === "likes"
                  ? "暂无喜欢的内容"
                  : tab === "mistakes"
                    ? "暂无错题"
                    : "暂无练习记录"
          }}
        </h3>
        <RouterLink to="/" class="button"
          >查看题库<ArrowRight :size="16"
        /></RouterLink>
      </div>
      <div
        v-if="(tab === 'history' || tab === 'mistakes') && total > 12"
        class="pagination"
      >
        <button class="button small" :disabled="page === 1" @click="page--">
          上一页</button
        ><span>{{ page }} / {{ Math.ceil(total / 12) }}</span
        ><button
          class="button small"
          :disabled="page >= Math.ceil(total / 12)"
          @click="page++"
        >
          下一页
        </button>
      </div></template
    ></template
  >
</template>
<style scoped>
.learning-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.learning-item .problem-card {
  flex: 1;
}
.ai-key-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 720px;
}
.ai-key-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
}
.ai-key-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
</style>
