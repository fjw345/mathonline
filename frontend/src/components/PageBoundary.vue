<script setup lang="ts">
import { nextTick, onErrorCaptured, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { state } from "../api";

const route = useRoute();
const failure = ref("");
const attempt = ref(0);
const detailsOpen = ref(false);
const copyStatus = ref("");
const detailsInput = ref<HTMLTextAreaElement>();

onErrorCaptured((error, _instance, info) => {
  const description =
    error instanceof Error ? error.stack || error.message : String(error);
  // Keep this local and omit query strings, account data, cookies and drafts.
  failure.value = [
    `页面：${route.path}`,
    `时间：${new Date().toISOString()}`,
    `浏览器：${navigator.userAgent}`,
    `阶段：${info}`,
    description.split("\n").slice(0, 10).join("\n"),
  ].join("\n");
  return false;
});

function retry() {
  failure.value = "";
  detailsOpen.value = false;
  copyStatus.value = "";
  attempt.value++;
}

watch([() => route.fullPath, () => state.user?.id], () => {
  if (failure.value) retry();
});

async function copyDetails() {
  try {
    await navigator.clipboard.writeText(failure.value);
    copyStatus.value = "错误信息已复制";
  } catch {
    detailsOpen.value = true;
    await nextTick();
    detailsInput.value?.focus();
    detailsInput.value?.select();
    copyStatus.value = "请长按下方错误详情复制";
  }
}
</script>

<template>
  <section v-if="failure" class="paper-card page-failure" role="alert">
    <h1>页面暂时无法显示</h1>
    <p>请重试；仍有问题时，将错误信息发给管理员。</p>
    <div class="inline">
      <button class="button primary" @click="retry">重新打开</button>
      <button class="button" @click="copyDetails">复制错误信息</button>
      <RouterLink to="/" class="text-button">返回题库</RouterLink>
    </div>
    <p v-if="copyStatus" role="status">{{ copyStatus }}</p>
    <details :open="detailsOpen">
      <summary>错误详情</summary>
      <textarea
        ref="detailsInput"
        :value="failure"
        readonly
        rows="10"
        aria-label="错误详情"
      ></textarea>
    </details>
  </section>
  <div v-else :key="attempt"><slot /></div>
</template>

<style scoped>
.page-failure {
  overflow-wrap: anywhere;
}
.page-failure .inline {
  flex-wrap: wrap;
}
.page-failure details {
  margin-top: 20px;
}
.page-failure summary {
  cursor: pointer;
  margin-bottom: 12px;
}
.page-failure textarea {
  width: 100%;
  font-size: 12px;
}
</style>
