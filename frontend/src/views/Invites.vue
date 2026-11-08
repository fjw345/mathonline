<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api, loadMe, state, toast, write } from "../api";
const route = useRoute();
interface InviteStatus {
  code: string;
  confirmed: number;
  pending: number;
  required: number;
  unlocked: boolean;
  bound: boolean;
  download_ready: boolean;
}
const status = ref<InviteStatus>();
const loading = ref(true),
  error = ref(""),
  busy = ref(false);
const inputCode = ref(String(route.query.code || "").slice(0, 10));
const resource = ref<{ url: string; extraction_code: string }>();
const copyFallback = ref("");
const copyInput = ref<HTMLInputElement>();
const inviteUrl = computed(() =>
  status.value
    ? `${location.origin}/invites?code=${encodeURIComponent(status.value.code)}`
    : "",
);
const loginUrl = computed(
  () => `/auth?next=${encodeURIComponent(route.fullPath)}`,
);
let sequence = 0;
async function load() {
  const seq = ++sequence;
  loading.value = true;
  error.value = "";
  status.value = undefined;
  resource.value = undefined;
  copyFallback.value = "";
  try {
    if (!state.sessionReady) await loadMe();
    if (seq !== sequence || !state.user) return;
    const data = await api<InviteStatus>("/me/invites");
    if (seq === sequence) status.value = data;
  } catch (e) {
    if (seq === sequence) error.value = (e as Error).message;
  } finally {
    if (seq === sequence) loading.value = false;
  }
}
watch(() => state.user?.id, load, { immediate: true });
watch(
  () => route.query.code,
  (value) => (inputCode.value = String(value || "").slice(0, 10)),
);
async function bind() {
  if (busy.value) return;
  busy.value = true;
  try {
    await write("/me/invites/bind", {
      code: inputCode.value.trim().toUpperCase(),
    });
    await load();
    toast("邀请人已绑定");
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    busy.value = false;
  }
}
async function copy(value: string) {
  const seq = sequence;
  try {
    await navigator.clipboard.writeText(value);
    toast("已复制");
  } catch {
    if (seq !== sequence) return;
    copyFallback.value = value;
    toast("请长按下方内容复制");
  }
}
async function unlock() {
  const seq = sequence;
  busy.value = true;
  try {
    const data = await api("/me/resources");
    if (seq === sequence) resource.value = data;
  } catch (e) {
    if (seq === sequence) toast((e as Error).message, true);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading"><h1>邀请与资料</h1></div>
  <div v-if="loading" class="loading-panel">加载中…</div>
  <div v-else-if="error" class="empty-state">
    <p>{{ error }}</p>
    <button class="button" @click="load">重试</button>
  </div>
  <div v-else-if="!state.user" class="paper-card">
    <h2>邀请好友，一起练习</h2>
    <p>注册或登录后填写朋友的邀请码。邀请 2 名用户即可解锁资料下载。</p>
    <p v-if="inputCode">
      朋友的邀请码：<strong>{{ inputCode }}</strong>
    </p>
    <RouterLink :to="loginUrl" class="button primary">登录 / 注册</RouterLink>
  </div>
  <template v-else-if="status">
    <section class="paper-card invite-card">
      <h2>我的邀请码</h2>
      <strong class="invite-code">{{ status.code }}</strong>
      <div class="inline">
        <button class="button" @click="copy(status.code)">复制邀请码</button
        ><button class="button primary" @click="copy(inviteUrl)">
          复制邀请链接
        </button>
      </div>
      <p class="muted">
        朋友登录后在本页绑定你的邀请码，每个账号只能绑定一位邀请人，不能更换。
      </p>
    </section>
    <section class="paper-card">
      <h2>邀请进度：{{ status.confirmed }} / {{ status.required }}</h2>
      <progress
        :value="Math.min(status.confirmed, status.required)"
        :max="status.required"
        aria-label="邀请进度"
      ></progress>
      <p v-if="status.pending">待验证邮箱：{{ status.pending }} 人</p>
      <p v-if="state.health.mode === 'supabase'" class="muted small-text">
        好友验证邮箱并绑定邀请码后计入进度，未更新时请刷新。
      </p>
      <button class="text-button" :disabled="busy" @click="load">
        刷新进度
      </button>
      <p>
        {{
          status.unlocked
            ? "资料下载已解锁"
            : `再邀请 ${status.required - status.confirmed} 人即可解锁资料`
        }}
      </p>
      <button
        class="button primary"
        :disabled="busy || !status.unlocked || !status.download_ready"
        @click="unlock"
      >
        {{ busy ? "处理中…" : "查看下载链接" }}
      </button>
      <p v-if="status.unlocked && !status.download_ready" class="muted">
        管理员正在准备资料下载。
      </p>
      <div v-if="resource" class="download-details">
        <a
          class="button primary"
          :href="resource.url"
          target="_blank"
          rel="noopener noreferrer"
          >打开夸克网盘</a
        >
        <p v-if="resource.extraction_code">
          提取码：<strong>{{ resource.extraction_code }}</strong>
          <button class="text-button" @click="copy(resource.extraction_code)">
            复制
          </button>
        </p>
        <button class="text-button" @click="copy(resource.url)">
          复制下载链接
        </button>
      </div>
    </section>
    <section class="paper-card">
      <h2>填写朋友的邀请码</h2>
      <p v-if="status.bound">已绑定邀请人，每个账号只能绑定一次。</p>
      <form v-else @submit.prevent="bind">
        <label class="field"
          >邀请码<input
            v-model.trim="inputCode"
            maxlength="10"
            minlength="10"
            autocomplete="off"
            autocapitalize="characters"
            required
            :disabled="busy"
        /></label>
        <p class="muted small-text">
          绑定后不能更换，请确认邀请码来自邀请你的朋友。
        </p>
        <button class="button" :disabled="busy">确认绑定</button>
      </form>
    </section>
    <label v-if="copyFallback" class="field"
      >长按复制<input
        ref="copyInput"
        :value="copyFallback"
        readonly
        @focus="copyInput?.select()"
    /></label>
  </template>
</template>
<style scoped>
.invite-card {
  overflow-wrap: anywhere;
}
.invite-code {
  display: block;
  font-size: clamp(24px, 5vw, 36px);
  letter-spacing: 3px;
  margin: 20px 0;
}
.inline {
  flex-wrap: wrap;
}
progress {
  width: 100%;
  height: 16px;
  accent-color: var(--green);
}
.download-details {
  margin-top: 20px;
  border-top: 1px solid var(--line);
  padding-top: 20px;
  overflow-wrap: anywhere;
}
</style>
