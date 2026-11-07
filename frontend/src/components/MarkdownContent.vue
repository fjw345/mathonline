<script setup lang="ts">
import { computed } from "vue";
import MarkdownIt from "markdown-it";
import katex from "katex";
import "katex/dist/katex.min.css";

type InlineRule = Parameters<MarkdownIt["inline"]["ruler"]["before"]>[2];
type BlockRule = Parameters<MarkdownIt["block"]["ruler"]["before"]>[2];

const props = defineProps<{ source: string; compact?: boolean }>();

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function math(tex: string, display: boolean) {
  try {
    return katex.renderToString(tex, {
      displayMode: display,
      throwOnError: true,
      strict: "ignore",
      trust: false,
      output: "htmlAndMathml",
    });
  } catch {
    // A few OCR fragments are not valid TeX. Keep the page readable and let
    // the student check the original image instead of showing raw red source.
    return `<span class="math-error" title="${escapeHtml(tex)}">公式待校对</span>`;
  }
}

function createRenderer(compact: boolean) {
  const md = new MarkdownIt({
    html: false,
    linkify: !compact,
    breaks: true,
    typographer: false,
  });

  const mathInline: InlineRule = (state, silent) => {
    const start = state.pos;
    const src = state.src;
    if (src[start] !== "$" || src[start + 1] === "$") return false;
    let pos = start + 1;
    while (pos < src.length) {
      pos = src.indexOf("$", pos);
      if (pos < 0) return false;
      if (src[pos - 1] === "\\") {
        pos += 1;
        continue;
      }
      if (src[pos + 1] === "$") {
        pos += 2;
        continue;
      }
      break;
    }
    if (pos < 0 || pos === start + 1) return false;
    const content = src.slice(start + 1, pos);
    if (!content.trim()) return false;
    if (!silent) {
      const token = state.push("math_inline", "math", 0);
      token.content = content;
      token.markup = "$";
    }
    state.pos = pos + 1;
    return true;
  };

  const mathBlock: BlockRule = (
    state,
    startLine,
    endLine,
    silent,
  ) => {
    const lineStart = state.bMarks[startLine] + state.tShift[startLine];
    const lineEnd = state.eMarks[startLine];
    if (state.src.slice(lineStart, lineStart + 2) !== "$$") return false;
    const first = state.src.slice(lineStart + 2, lineEnd);
    let content = "";
    let nextLine = startLine + 1;
    const sameLine = first.indexOf("$$");
    if (sameLine >= 0) {
      content = first.slice(0, sameLine);
    } else {
      const lines = [first];
      let found = false;
      let cursor = startLine + 1;
      while (cursor < endLine) {
        const start = state.bMarks[cursor] + state.tShift[cursor];
        const end = state.eMarks[cursor];
        const line = state.src.slice(start, end);
        const close = line.indexOf("$$");
        if (close >= 0) {
          lines.push(line.slice(0, close));
          found = true;
          cursor += 1;
          break;
        }
        lines.push(line);
        cursor += 1;
      }
      if (!found) return false;
      content = lines.join("\n");
      nextLine = cursor;
    }
    if (silent) return true;
    const token = state.push("math_block", "math", 0);
    token.block = true;
    token.content = content.trim();
    token.markup = "$$";
    token.map = [startLine, nextLine];
    state.line = nextLine;
    return true;
  };

  md.inline.ruler.before("emphasis", "math_inline", mathInline);
  md.block.ruler.before("fence", "math_block", mathBlock, {
    alt: ["paragraph", "reference", "blockquote", "list"],
  });
  md.renderer.rules.math_inline = (tokens, index) =>
    `<span class="math-inline">${math(tokens[index].content, false)}</span>`;
  md.renderer.rules.math_block = (tokens, index) =>
    `<div class="math-block">${math(tokens[index].content, true)}</div>`;

  const renderImage = md.renderer.rules.image;
  md.renderer.rules.image = (tokens, index, options, env, self) => {
    const token = tokens[index];
    token.attrSet("loading", "lazy");
    token.attrSet("decoding", "async");
    token.attrSet("class", "prose-figure");
    return renderImage
      ? renderImage(tokens, index, options, env, self)
      : self.renderToken(tokens, index, options);
  };

  if (compact) {
    // A card is already a RouterLink; nested anchors would be invalid HTML.
    md.renderer.rules.link_open = () => "";
    md.renderer.rules.link_close = () => "";
  } else {
    const openLink = md.renderer.rules.link_open;
    md.renderer.rules.link_open = (tokens, index, options, env, self) => {
      const href = tokens[index].attrGet("href") || "";
      if (/^https?:\/\//i.test(href)) {
        tokens[index].attrSet("target", "_blank");
        tokens[index].attrSet("rel", "noopener noreferrer");
      }
      return openLink
        ? openLink(tokens, index, options, env, self)
        : self.renderToken(tokens, index, options);
    };
  }
  return md;
}

const standard = createRenderer(false);
const compactRenderer = createRenderer(true);
const html = computed(() =>
  (props.compact ? compactRenderer : standard).render(props.source || ""),
);
</script>

<template>
  <div class="prose" :class="{ 'prose-compact': compact }" v-html="html"></div>
</template>
