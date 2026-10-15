<script setup lang="ts">
import { ArrowUpRight, Check } from "lucide-vue-next";
import { onMounted, onBeforeUnmount, ref } from "vue";
import { typeName } from "../api";
import type { Problem } from "../types";
defineProps<{ problem: Problem }>();
const preview = ref<HTMLElement>();
const visible = ref(false);
let observer: IntersectionObserver | undefined;
onMounted(() => {
  if (!("IntersectionObserver" in window)) {
    visible.value = true;
    return;
  }
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        visible.value = true;
        observer?.disconnect();
      }
    },
    { rootMargin: "160px" },
  );
  if (preview.value) observer.observe(preview.value);
});
onBeforeUnmount(() => observer?.disconnect());
function imageFallback(event: Event, id: string) {
  const img = event.target as HTMLImageElement;
  if (!img.src.includes("/api/v1/media/")) img.src = "/api/v1/media/" + id;
}
</script>
<template>
  <RouterLink :to="'/problems/' + problem.id" class="problem-card"
    ><div class="card-head">
      <span class="chip">{{ typeName(problem.type) }}</span
      ><span class="card-year"
        >{{ problem.year }} <ArrowUpRight :size="17"
      /></span>
    </div>
    <div ref="preview" class="card-image">
      <img
        v-if="visible && problem.question_images[0]"
        :src="problem.question_images[0].url"
        :alt="problem.title + '题干预览'"
        loading="lazy"
        decoding="async"
        @error="imageFallback($event, problem.question_images[0].id)"
      />
    </div>
    <h3>{{ problem.title }}</h3>
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
