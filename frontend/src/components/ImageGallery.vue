<script setup lang="ts">
import { ref, watch } from "vue";
import { Expand, X, RefreshCw } from "lucide-vue-next";
import type { ImageAsset } from "../types";
const props = defineProps<{
  images: ImageAsset[];
  label?: string;
  compact?: boolean;
  removable?: boolean;
  disabled?: boolean;
}>();
defineEmits<{ remove: [id: string] }>();
const dialog = ref<HTMLDialogElement>();
const selected = ref<ImageAsset>();
const failed = ref(new Set<string>());
const retryKey = ref(0);
const fallbacks = ref(new Set<string>());
function open(img: ImageAsset) {
  selected.value = img;
  dialog.value?.showModal();
}
function fail(img: ImageAsset) {
  if (!fallbacks.value.has(img.id) && !img.url.startsWith("/api/v1/media/")) {
    fallbacks.value.add(img.id);
  } else failed.value.add(img.id);
}
function retry(id: string) {
  failed.value.delete(id);
  retryKey.value++;
}
function source(img: ImageAsset) {
  return fallbacks.value.has(img.id) ? "/api/v1/media/" + img.id : img.url;
}
watch(
  () => props.images,
  (images) => {
    if (selected.value && !images.some((img) => img.id === selected.value?.id))
      dialog.value?.close();
  },
  { deep: true },
);
</script>
<template>
  <div class="gallery" :class="{ 'gallery-compact': compact }">
    <div v-for="(img, index) in images" :key="img.id" class="image-piece">
      <div v-if="failed.has(img.id)" class="image-error">
        图片加载失败
        <button class="button small" @click="retry(img.id)">
          <RefreshCw :size="15" />重新加载
        </button>
      </div>
      <button
        v-else
        class="image-zoom"
        :aria-label="`放大${label || '题目'}图片 ${index + 1}`"
        @click="open(img)"
      >
        <img
          :key="retryKey"
          :src="source(img)"
          :width="img.width"
          :height="img.height"
          :alt="`${label || '题目'} · 第 ${index + 1} 张`"
          loading="lazy"
          decoding="async"
          @error="fail(img)"
        /><span class="expand-hint"><Expand :size="14" /> 点击放大</span>
      </button>
      <button
        v-if="removable"
        class="gallery-remove"
        :disabled="disabled"
        :aria-label="`移除${label || '题目'}图片 ${index + 1}`"
        @click="$emit('remove', img.id)"
      >
        <X :size="16" />
      </button>
      <small v-if="images.length > 1" class="piece-number"
        >{{ index + 1 }} / {{ images.length }}</small
      >
    </div>
  </div>
  <dialog
    ref="dialog"
    class="image-dialog"
    :aria-label="`${label || '题目'}原图预览`"
    @close="selected = undefined"
    @click="
      (e: MouseEvent) => {
        if (e.target === dialog) dialog?.close();
      }
    "
  >
    <button
      class="icon-button dialog-close"
      @click="dialog?.close()"
      aria-label="关闭图片"
    >
      <X />
    </button>
    <p>原图查看 · 可使用浏览器缩放</p>
    <img
      v-if="selected"
      :src="source(selected)"
      :alt="label || '题目原图'"
      :style="{ minWidth: Math.min(selected.width, 1600) + 'px' }"
      @error="fail(selected)"
    />
  </dialog>
</template>
<style scoped>
.gallery-compact {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 12px;
}
.gallery-compact .image-piece {
  margin: 0;
  border: 1px solid var(--line);
  border-radius: 7px;
}
.gallery-compact .image-zoom {
  height: 120px;
  padding: 12px;
}
.gallery-compact .image-zoom img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.gallery-compact .piece-number {
  padding: 4px 8px;
  font-size: 11px;
}
.gallery-remove {
  position: absolute;
  top: 0;
  right: 0;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border: 1px solid var(--line);
  border-radius: 0 6px 0 6px;
  background: var(--paper);
  color: var(--danger);
}
</style>
