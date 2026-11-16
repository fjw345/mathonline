<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdown } from "../markdown";
import "katex/dist/katex.min.css";

const props = defineProps<{
  source: string;
  compact?: boolean;
  mathFallback?: boolean;
}>();
const html = computed(() =>
  renderMarkdown(props.source, props.compact, props.mathFallback),
);
</script>

<template>
  <div class="prose" :class="{ 'prose-compact': compact }" v-html="html"></div>
</template>
