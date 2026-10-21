<script setup lang="ts">
import { ref } from "vue";
import { Expand, X, RefreshCw } from "lucide-vue-next";
import type { ImageAsset } from "../types";
defineProps<{ images: ImageAsset[]; label?: string }>();
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
</script>
<template>
  <div class="gallery">
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
          :src="fallbacks.has(img.id) ? '/api/v1/media/' + img.id : img.url"
          :width="img.width"
          :height="img.height"
          :alt="`${label || '题目'} · 第 ${index + 1} 张`"
          loading="lazy"
          decoding="async"
          @error="fail(img)"
        /><span class="expand-hint"><Expand :size="14" /> 点击放大</span>
      </button>
      <small v-if="images.length > 1" class="piece-number"
        >{{ index + 1 }} / {{ images.length }}</small
      >
    </div>
  </div>
  <dialog
    ref="dialog"
    class="image-dialog"
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
      :src="selected.url"
      :alt="label || '题目原图'"
      :style="{ minWidth: Math.min(selected.width, 1600) + 'px' }"
    />
  </dialog>
</template>
