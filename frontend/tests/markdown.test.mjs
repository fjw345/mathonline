import assert from "node:assert/strict";
import { test } from "node:test";
import Module from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const filename = fileURLToPath(new URL("../src/markdown.ts", import.meta.url));
const result = await build({
  entryPoints: [filename],
  bundle: true,
  platform: "node",
  format: "cjs",
  write: false,
});
const compiled = new Module(filename);
compiled._compile(result.outputFiles[0].text, filename);
const { renderMarkdown } = compiled.exports;
const formulas = (html) => (html.match(/class="katex"/g) || []).length;

test("renders the integral, legacy code formula and function together", () => {
  const html = renderMarkdown(
    "已知 `\\frac{\\sin x}{x+1}` 是 `f(x)` 的一个原函数，则 $\\int f(x)\\cdot\\frac{\\sin x}{x+1}\\,dx=\\text{\\_\\_\\_\\_}$。",
  );
  assert.equal(formulas(html), 3);
  assert.ok(!html.includes("math-error"));
});

test("supports standard TeX delimiters, display math inside prose and multiline math", () => {
  for (const source of [
    String.raw`结果 \(\frac{1}{2}\)。`,
    String.raw`结果 \[\frac{1}{2}\]。`,
    "结果 $$x^2$$。",
    "$$\nx^2\n$$",
    "\\[\nx^2\n\\]",
  ]) {
    assert.equal(formulas(renderMarkdown(source)), 1, source);
  }
});

test("renders bare math only when the answer fallback is explicitly requested", () => {
  assert.equal(
    formulas(renderMarkdown(String.raw`\frac{x^2}{2}+C`, false, true)),
    1,
  );
  assert.equal(formulas(renderMarkdown(String.raw`\frac{x^2}{2}+C`)), 0);
  assert.match(renderMarkdown("暂无", false, true), /暂无/);
});

test("preserves ordinary code, fenced code, escaped dollars and ambiguous formulas", () => {
  for (const source of [
    "`AI_MODEL`",
    "```tex\n$x^2$\n```",
    String.raw`价格 \$10`,
    "`sin⁡xx+1`",
  ]) {
    assert.equal(formulas(renderMarkdown(source)), 0, source);
  }
  assert.match(renderMarkdown("`sin⁡xx+1`"), /sin⁡xx\+1/);
});

test("unclosed and invalid formulas do not consume trailing prose or break rendering", () => {
  assert.match(renderMarkdown("未闭合 $x 后文"), /后文/);
  assert.match(renderMarkdown("$\\unknowncommand{x}$ 后文"), /公式待校对/);
  assert.match(renderMarkdown("$\\unknowncommand{x}$ 后文"), /后文/);
});

test("keeps HTML, unsafe links and TeX commands untrusted", () => {
  const html = renderMarkdown(
    "<img src=x onerror=alert(1)> [坏链接](javascript:alert(1)) $\\href{javascript:alert(1)}{x}$",
  );
  assert.ok(!html.includes("<img src=x"));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(
    !renderMarkdown("[link](https://example.com)", true).includes("<a"),
  );
});
