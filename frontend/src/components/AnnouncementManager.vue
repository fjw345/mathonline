<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, toast, write } from "../api";
import type { Announcement } from "../types";
const rows = ref<Announcement[]>([]);
const title = ref("");
const body = ref("");
const editing = ref<Announcement>();
const busy = ref(false);
const loading = ref(false);
const error = ref("");
const page = ref(1);
const total = ref(0);
let sequence = 0;
async function load() {
  const seq = ++sequence;
  loading.value = true;
  error.value = "";
  try {
    const data = await api(`/admin/announcements?page=${page.value}`);
    if (seq !== sequence) return;
    rows.value = data.items;
    total.value = data.total;
  } catch (e) {
    if (seq === sequence) error.value = (e as Error).message;
  } finally {
    if (seq === sequence) loading.value = false;
  }
}
onMounted(load);
function edit(row: Announcement) {
  editing.value = row;
  title.value = row.title;
  body.value = row.body;
}
function reset() {
  editing.value = undefined;
  title.value = "";
  body.value = "";
}
async function save(published = true) {
  if (busy.value) return;
  busy.value = true;
  try {
    await write(
      editing.value
        ? `/admin/announcements/${editing.value.id}`
        : "/admin/announcements",
      {
        title: title.value,
        body: body.value,
        published,
        revision: editing.value?.revision,
      },
      editing.value ? "PUT" : "POST",
    );
    if (!editing.value) page.value = 1;
    reset();
    await load();
    toast(published ? "更新已发布" : "已保存草稿");
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    busy.value = false;
  }
}
async function hide(row: Announcement) {
  if (busy.value) return;
  busy.value = true;
  try {
    await write(
      `/admin/announcements/${row.id}`,
      { ...row, published: !row.published },
      "PUT",
    );
    if (editing.value?.id === row.id) reset();
    await load();
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    busy.value = false;
  }
}
async function move(delta: number) {
  page.value += delta;
  await load();
}
</script>
<template>
  <section class="paper-card">
    <h2>{{ editing ? "编辑更新" : "发布更新" }}</h2>
    <form @submit.prevent="save(true)">
      <label class="field"
        >标题<input
          v-model.trim="title"
          maxlength="100"
          required
          :disabled="busy"
      /></label>
      <label class="field"
        >更新内容<textarea
          v-model="body"
          rows="6"
          maxlength="10000"
          required
          :disabled="busy"
        ></textarea>
      </label>
      <div class="inline">
        <button class="button primary" :disabled="busy">发布</button>
        <button
          type="button"
          class="button"
          :disabled="busy || !title.trim() || !body.trim()"
          @click="save(false)"
        >
          保存草稿
        </button>
        <button
          v-if="editing"
          type="button"
          class="text-button"
          :disabled="busy"
          @click="reset"
        >
          取消编辑
        </button>
      </div>
    </form>
  </section>
  <div v-if="error" class="form-error" role="alert">
    {{ error }} <button class="text-button" @click="load">重试</button>
  </div>
  <p v-if="loading" class="muted" role="status">加载中…</p>
  <article
    v-for="row in rows"
    :key="row.id"
    class="paper-card announcement-row"
  >
    <h3>
      {{ row.title }}
      <span class="chip neutral">{{
        row.published ? "已发布" : "草稿／已隐藏"
      }}</span>
    </h3>
    <p class="preserve-text">{{ row.body }}</p>
    <div class="inline">
      <button
        class="text-button"
        :disabled="busy || loading"
        @click="edit(row)"
      >
        编辑</button
      ><button
        class="text-button"
        :disabled="busy || loading"
        @click="hide(row)"
      >
        {{ row.published ? "隐藏" : "发布" }}
      </button>
    </div>
  </article>
  <div class="inline">
    <button
      class="button"
      :disabled="busy || loading || page === 1"
      @click="move(-1)"
    >
      上一页</button
    ><span>{{ page }} / {{ Math.max(1, Math.ceil(total / 12)) }}</span
    ><button
      class="button"
      :disabled="busy || loading || page * 12 >= total"
      @click="move(1)"
    >
      下一页
    </button>
  </div>
</template>
<style scoped>
.announcement-row {
  overflow-wrap: anywhere;
}
.inline {
  flex-wrap: wrap;
}
</style>
