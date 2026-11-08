<script setup lang="ts">
import { ref, watch } from "vue";
import { api, loadMe, state, toast, write } from "../api";
import type { Announcement } from "../types";
const rows = ref<Announcement[]>([]);
const opened = ref("");
const page = ref(1),
  total = ref(0),
  loading = ref(true),
  error = ref("");
let sequence = 0;
async function load() {
  const seq = ++sequence;
  loading.value = true;
  error.value = "";
  rows.value = [];
  opened.value = "";
  try {
    if (!state.sessionReady) await loadMe();
    if (seq !== sequence || !state.user) return;
    const data = await api(`/mailbox?page=${page.value}`);
    if (seq !== sequence) return;
    rows.value = data.items;
    total.value = data.total;
  } catch (e) {
    if (seq === sequence) error.value = (e as Error).message;
  } finally {
    if (seq === sequence) loading.value = false;
  }
}
watch([page, () => state.user?.id], load, { immediate: true });
async function open(row: Announcement) {
  if (opened.value === row.id) {
    opened.value = "";
    return;
  }
  opened.value = row.id;
  if (!row.is_read) {
    try {
      await write(`/mailbox/${row.id}/read`, { revision: row.revision }, "PUT");
      row.is_read = true;
    } catch (e) {
      toast((e as Error).message, true);
    }
  }
}
function date(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
}
</script>
<template>
  <div class="page-heading">
    <h1>更新信箱</h1>
    <RouterLink
      v-if="state.user?.role === 'admin'"
      to="/admin?tab=announcements"
      class="button"
      >发布更新</RouterLink
    >
  </div>
  <div v-if="loading" class="loading-panel">加载中…</div>
  <div v-else-if="error" class="empty-state">
    <p>{{ error }}</p>
    <button class="button" @click="load">重试</button>
  </div>
  <div v-else-if="!state.user" class="empty-state">
    <p>登录后查看网站更新。</p>
    <RouterLink class="button primary" to="/auth?next=/mailbox"
      >登录 / 注册</RouterLink
    >
  </div>
  <template v-else>
    <p v-if="!rows.length" class="empty-state">暂无更新</p>
    <article v-for="row in rows" :key="row.id" class="paper-card mail-item">
      <button
        class="mail-title"
        :aria-expanded="opened === row.id"
        :aria-controls="`mail-${row.id}`"
        @click="open(row)"
      >
        <strong>{{ row.title }}</strong
        ><span v-if="!row.is_read" class="chip">未读</span>
      </button>
      <p class="muted small-text">
        {{ date(row.created_at) }}<span v-if="row.revision > 1"> · 已更新</span>
      </p>
      <p v-if="opened === row.id" :id="`mail-${row.id}`" class="preserve-text">
        {{ row.body }}
      </p>
    </article>
    <div v-if="total > 12" class="inline">
      <button class="button" :disabled="page === 1" @click="page--">
        上一页</button
      ><span>{{ page }}</span
      ><button class="button" :disabled="page * 12 >= total" @click="page++">
        下一页
      </button>
    </div>
  </template>
</template>
<style scoped>
.mail-item {
  overflow-wrap: anywhere;
}
.mail-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  text-align: left;
  border: 0;
  padding: 8px 0;
  min-height: 44px;
  background: none;
  color: var(--ink);
  font-size: 18px;
}
</style>
