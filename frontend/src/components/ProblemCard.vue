<script setup lang="ts">
import { ArrowUpRight, Check } from "lucide-vue-next";
import { typeName } from "../api";
import type { Problem } from "../types";
import MarkdownContent from "./MarkdownContent.vue";
defineProps<{ problem: Problem }>();
</script>
<template>
  <RouterLink :to="'/problems/' + problem.id" class="problem-card"
    ><div class="card-head">
      <span class="chip">{{ typeName(problem.type) }}</span
      ><span class="card-year"
        >{{ problem.year }} <ArrowUpRight :size="17"
      /></span>
    </div>
    <div class="card-image text-preview">
      <MarkdownContent
        v-if="problem.question_text"
        :source="problem.question_text"
        compact
        class="card-text"
      />
      <p v-else class="card-text-missing">本题文字版整理中</p>
    </div>
    <h3>{{ problem.title }}</h3>
    <div v-if="problem.knowledge_points.length" class="knowledge-row">
      <span
        v-for="item in problem.knowledge_points.slice(0, 2)"
        :key="item.code"
        class="knowledge-chip"
        >{{ item.name }}</span
      >
    </div>
    <div class="card-foot">
      <span>{{ problem.tags.join("、") }}</span
      ><span
        v-if="problem.progress"
        class="state-text"
        :class="problem.progress.result"
        ><Check v-if="problem.progress.result === 'correct'" :size="14" />{{
          problem.progress.outdated
            ? "题目已更新"
            : problem.progress.result === "pending"
              ? "待自检"
              : problem.progress.result === "correct"
                ? "已做对"
                : "错误"
        }}</span
      ><span v-else class="new-dot">未作答</span>
    </div></RouterLink
  >
</template>
<style scoped>
.text-preview {
  position: relative;
  align-items: flex-start;
  justify-content: flex-start;
  padding: 0 1px;
}
.text-preview::after {
  content: "";
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 26px;
  pointer-events: none;
  background: linear-gradient(rgba(255, 255, 255, 0), #fff);
}
.card-text {
  width: 100%;
  max-height: 107px;
  overflow: hidden;
}
.card-text :deep(.prose-figure) {
  display: none;
}
.card-text-missing {
  margin: 0;
  color: var(--muted);
  font-size: 11px;
}
</style>
