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
} from "lucide-vue-next";
import { api, write, loadMe, state, toast, statusName } from "../api";
import type { Problem, Solution, Notification } from "../types";
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
const nickname = ref("");
const settings = ref(false),
  saving = ref(false);
const tabs = [
  { id: "history", label: "练习记录" },
  { id: "mistakes", label: "错题" },
  { id: "likes", label: "喜欢" },
  { id: "solutions", label: "我的题解" },
  { id: "notifications", label: "消息" },
];
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
async function load() {
  loading.value = true;
  error.value = "";
  try {
    await loadMe();
    if (!state.user) return;
    items.value = [];
    solutions.value = [];
    if (tab.value === "notifications") {
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
        ><span>曾做错</span>
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
      曾做错的题目会保留，重做正确后也不移除。
    </p>
    <div v-if="loading" class="loading-panel">加载中…</div>
    <div v-else-if="error" class="empty-state">
      <p>{{ error }}</p>
      <button class="button" @click="load">重试</button>
    </div>
    <template v-else
      ><div v-if="items.length" class="card-grid">
        <ProblemCard v-for="p in items" :key="p.id" :problem="p" />
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
