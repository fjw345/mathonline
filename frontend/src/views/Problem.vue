<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  ArrowRight,
  Heart,
  Upload,
  Eye,
  Check,
  X,
  Shuffle,
  Sun,
  Send,
  MessageSquare,
  ChevronDown,
  CircleHelp,
  LockKeyhole,
} from "lucide-vue-next";
import {
  api,
  write,
  upload,
  state,
  loadMe,
  toast,
  typeName,
  statusName,
} from "../api";
import type { Problem, Answer, ImageAsset, Solution, Comment } from "../types";
import ImageGallery from "../components/ImageGallery.vue";
import PracticeFilterForm from "../components/PracticeFilters.vue";
import {
  practiceFilterQuery,
  readPracticeFilters,
  rememberPracticeFilters,
  type PracticeFilters,
} from "../practiceFilters";
import {
  practiceDraftKey,
  readPracticeDraft,
  readRandomPracticeDraft,
  writePracticeDraft,
  type PracticeDraft,
} from "../practiceDraft";
const route = useRoute(),
  router = useRouter();
const problem = ref<Problem>();
const answer = ref<Answer>();
const loading = ref(true),
  error = ref(""),
  busy = ref(false),
  answerOpen = ref(false),
  text = ref(""),
  options = ref<string[]>([]),
  images = ref<ImageAsset[]>([]),
  community = ref<Solution[]>([]),
  solutionText = ref(""),
  solutionImages = ref<ImageAsset[]>([]),
  editingVersion = ref<number | undefined>();
const comments = ref<Record<string, Comment[]>>({}),
  commentText = ref<Record<string, string>>({});
const feedbackOpen = ref(false),
  feedbackKind = ref("incomplete"),
  feedbackDetail = ref("");
interface DailyAssignment {
  completed_at: string | null;
  local_date: string;
  problem_id: string;
  filters: PracticeFilters;
}
const assignment = ref<DailyAssignment>();
const selectedFilters = ref<PracticeFilters>({});
const dailyAttempted = ref(false);
const answerUnlocked = computed(() => !!problem.value?.answer_unlocked);
let sequence = 0;
let draftContext:
  | {
      key: string;
      problemId: string;
      progressRevision: number | null;
    }
  | undefined;
const draftVersion = ref<number>();
const draftOutdated = computed(
  () =>
    draftVersion.value !== undefined &&
    draftVersion.value !== problem.value?.version,
);
const daily = computed(() => route.path === "/daily"),
  random = computed(() => route.path === "/practice");
const filtersPending = computed(
  () =>
    random.value &&
    practiceFilterQuery(selectedFilters.value).toString() !==
      filterQuery().toString(),
);
function filterQuery() {
  return practiceFilterQuery(readPracticeFilters(route.query));
}
function saveDraft() {
  if (!draftContext || !problem.value) return;
  writePracticeDraft(draftContext.key, {
    problemId: draftContext.problemId,
    problemVersion: draftVersion.value ?? problem.value.version,
    progressRevision: draftContext.progressRevision,
    text: text.value,
    options: options.value,
    images: images.value,
    solutionText: solutionText.value,
    solutionImages: solutionImages.value,
    editingVersion: editingVersion.value,
  });
}
watch(
  [text, options, images, solutionText, solutionImages, editingVersion],
  saveDraft,
  { deep: true, flush: "sync" },
);
onBeforeUnmount(() => {
  saveDraft();
  draftContext = undefined;
  ++sequence;
});
function setProblem(data: Problem, key?: string, draft?: PracticeDraft) {
  feedbackOpen.value = false;
  feedbackKind.value = "incomplete";
  feedbackDetail.value = "";
  draftContext = undefined;
  problem.value = data;
  answer.value = undefined;
  answerOpen.value = false;
  options.value = [];
  solutionText.value = "";
  solutionImages.value = [];
  editingVersion.value = undefined;
  draftVersion.value = data.version;
  hydrate(data);
  if (
    draft?.problemId === data.id &&
    draft.progressRevision === (data.progress?.revision ?? null)
  ) {
    text.value = draft.text;
    images.value = draft.images;
    options.value = draft.options.filter((option) =>
      data.choice_options.includes(option),
    );
    solutionText.value = draft.solutionText;
    solutionImages.value = draft.solutionImages;
    editingVersion.value = draft.editingVersion;
    draftVersion.value = draft.problemVersion;
  }
  if (key) {
    draftContext = {
      key,
      problemId: data.id,
      progressRevision: data.progress?.revision ?? null,
    };
    saveDraft();
  }
}
async function load() {
  const seq = ++sequence;
  saveDraft();
  draftContext = undefined;
  const mode = daily.value ? "daily" : random.value ? "random" : "detail";
  const filters = filterQuery();
  selectedFilters.value = Object.fromEntries(filters);
  dailyAttempted.value = false;
  const id = String(route.params.id);
  loading.value = true;
  busy.value = false;
  error.value = "";
  problem.value = undefined;
  answer.value = undefined;
  answerOpen.value = false;
  assignment.value = undefined;
  community.value = [];
  text.value = "";
  images.value = [];
  options.value = [];
  solutionText.value = "";
  solutionImages.value = [];
  editingVersion.value = undefined;
  draftVersion.value = undefined;
  comments.value = {};
  commentText.value = {};
  try {
    if (!state.sessionReady) await loadMe();
    if (seq !== sequence) return;
    let data: Problem;
    let key: string | undefined;
    let draft: PracticeDraft | undefined;
    if (mode === "daily") {
      if (!state.user) {
        loading.value = false;
        return;
      }
      const result = await api<{ assignment: DailyAssignment | null }>(
        "/practice/daily",
      );
      if (seq !== sequence) return;
      assignment.value = result.assignment ?? undefined;
      if (result.assignment)
        selectedFilters.value = readPracticeFilters(result.assignment.filters);
      return;
    } else if (mode === "random") {
      key = practiceDraftKey(state.user?.id, filters.toString());
      draft = readRandomPracticeDraft(state.user?.id, filters.toString());
      if (draft) filters.set("resume", draft.problemId);
      data = await api("/practice/random?" + filters);
    } else {
      if (id === "undefined") return;
      data = await api("/problems/" + encodeURIComponent(id));
    }
    if (seq !== sequence) return;
    if (mode === "random")
      rememberPracticeFilters(state.user?.id, readPracticeFilters(route.query));
    await displayProblem(seq, data, key, draft);
  } catch (e) {
    if (seq === sequence) error.value = (e as Error).message;
  } finally {
    if (seq === sequence) loading.value = false;
  }
}
watch([() => route.fullPath, () => state.user?.id], load, { immediate: true });
async function displayProblem(
  seq: number,
  data: Problem,
  key?: string,
  draft?: PracticeDraft,
) {
  setProblem(data, key, draft);
  // Community content must not block the question or turn a usable question
  // into an error page when the community endpoint is slow/unavailable.
  void api<Solution[]>("/problems/" + data.id + "/solutions")
    .then((solutions) => {
      if (seq === sequence) community.value = solutions;
    })
    .catch((e: Error) => {
      if (seq === sequence) toast("题解加载失败：" + e.message, true);
    });
}
async function openDaily() {
  if (!state.user) {
    login();
    return;
  }
  if (loading.value || busy.value) return;
  const seq = ++sequence;
  saveDraft();
  draftContext = undefined;
  loading.value = true;
  error.value = "";
  dailyAttempted.value = true;
  try {
    const result = await write<{
      problem: Problem;
      assignment: DailyAssignment;
    }>("/practice/daily", readPracticeFilters(selectedFilters.value));
    if (seq !== sequence) return;
    assignment.value = result.assignment;
    selectedFilters.value = readPracticeFilters(result.assignment.filters);
    const key = practiceDraftKey(
      state.user?.id,
      result.assignment.local_date,
      "daily",
    );
    await displayProblem(seq, result.problem, key, readPracticeDraft(key));
  } catch (e) {
    if (seq === sequence) error.value = (e as Error).message;
  } finally {
    if (seq === sequence) loading.value = false;
  }
}
async function applyFilters(filters: PracticeFilters) {
  if (daily.value) {
    await openDaily();
    return;
  }
  const query = practiceFilterQuery(filters);
  if (query.toString() === filterQuery().toString()) {
    if (error.value) await load();
    return;
  }
  await router.push({ path: "/practice", query: Object.fromEntries(query) });
}
function hydrate(p: Problem) {
  text.value = p.progress?.text || "";
  images.value = p.progress?.images || [];
}
function login() {
  const destination = daily.value
    ? "/daily?" + practiceFilterQuery(selectedFilters.value)
    : route.fullPath;
  router.push("/auth?next=" + encodeURIComponent(destination));
}
async function action(work: () => Promise<void>) {
  const seq = sequence;
  busy.value = true;
  try {
    await work();
  } catch (e) {
    if (seq === sequence) toast((e as Error).message, true);
  } finally {
    if (seq === sequence) busy.value = false;
  }
}
async function showAnswer() {
  if (!problem.value || !answerUnlocked.value) return;
  const seq = sequence;
  const id = problem.value.id;
  const data = await api<Answer>("/problems/" + id + "/answer");
  if (seq !== sequence || problem.value?.id !== id) return;
  answer.value = data;
  answerOpen.value = true;
}
async function toggleAnswer() {
  if (!answerUnlocked.value) return;
  if (answerOpen.value) {
    answerOpen.value = false;
    return;
  }
  await action(showAnswer);
}
async function giveUp() {
  if (!state.user) {
    login();
    return;
  }
  await action(async () => {
    const seq = sequence;
    const p = problem.value!;
    const data = await write<Problem>("/me/problems/" + p.id + "/give-up", {
      problem_version: p.version,
    });
    if (seq !== sequence) return;
    problem.value = data;
    saveDraft();
    await showAnswer();
    if (seq !== sequence) return;
    toast("答案已解锁，草稿已保留");
  });
}
async function submit() {
  if (!state.user) {
    login();
    return;
  }
  await action(async () => {
    const seq = sequence;
    const p = problem.value!;
    const data = await write<Problem>(
      "/me/problems/" + p.id + "/submission",
      {
        text: text.value,
        image_ids: images.value.map((i) => i.id),
        options: options.value,
        problem_version: p.version,
      },
      "PUT",
    );
    if (seq !== sequence) return;
    if (draftContext)
      draftContext.progressRevision = data.progress?.revision ?? null;
    draftVersion.value = data.version;
    problem.value = data;
    hydrate(data);
    saveDraft();
    await showAnswer();
    await loadMe();
    if (seq !== sequence) return;
    toast(p.type === "choice" ? "答案已核对" : "作答已保存，请核对答案并自检");
  });
}
async function assess(result: string) {
  await action(async () => {
    const seq = sequence;
    const p = problem.value!;
    const data = await write<Problem>(
      "/me/problems/" + p.id + "/self-assessment",
      { result, revision: p.progress!.revision },
      "PATCH",
    );
    if (seq !== sequence) return;
    problem.value = data;
    const me = await loadMe();
    if (seq !== sequence) return;
    if (daily.value) assignment.value = me.daily;
    toast(result === "correct" ? "已标记正确" : "已记入错题");
  });
}
async function next() {
  await action(async () => {
    const seq = sequence;
    const key = draftContext?.key;
    const filters = filterQuery();
    if (problem.value) filters.set("exclude", problem.value.id);
    const data = await api<Problem>("/practice/random?" + filters);
    if (seq !== sequence) return;
    setProblem(data, key);
    comments.value = {};
    commentText.value = {};
    community.value = [];
    const solutions = await api<Solution[]>(
      "/problems/" + data.id + "/solutions",
    );
    if (seq !== sequence) return;
    community.value = solutions;
  });
}
async function likeProblem() {
  if (!state.user) {
    login();
    return;
  }
  await action(async () => {
    const p = problem.value!;
    const data = await api("/problems/" + p.id + "/like", {
      method: p.liked ? "DELETE" : "PUT",
    });
    p.liked = data.liked;
  });
}
async function files(event: Event, purpose: string) {
  const seq = sequence;
  const input = event.target as HTMLInputElement;
  const target = purpose === "submission" ? images : solutionImages;
  const selected = Array.from(input.files || []);
  if (target.value.length + selected.length > 5) {
    toast("每份作答或题解最多 5 张图片", true);
    input.value = "";
    return;
  }
  await action(async () => {
    for (const file of selected) {
      const image = await upload(file, purpose);
      if (seq !== sequence) return;
      target.value.push(image);
    }
  });
  input.value = "";
}
async function publish() {
  await action(async () => {
    const seq = sequence;
    const id = problem.value!.id;
    await write("/problems/" + id + "/solutions", {
      body: solutionText.value,
      image_ids: solutionImages.value.map((i) => i.id),
      version: editingVersion.value,
    });
    if (seq !== sequence) return;
    const solutions = await api<Solution[]>("/problems/" + id + "/solutions");
    if (seq !== sequence) return;
    community.value = solutions;
    solutionText.value = "";
    solutionImages.value = [];
    editingVersion.value = undefined;
    toast("题解已提交，待审核");
  });
}
function edit(s: Solution) {
  solutionText.value = s.body;
  solutionImages.value = s.images;
  editingVersion.value = s.version;
  document
    .getElementById("solution-editor")
    ?.scrollIntoView({ behavior: "smooth" });
}
async function hide(s: Solution) {
  await action(async () => {
    await api("/solutions/" + encodeURIComponent(s.id), { method: "DELETE" });
    community.value = await api(
      "/problems/" + problem.value!.id + "/solutions",
    );
    toast("题解已隐藏");
  });
}
async function likeSolution(s: Solution) {
  if (!state.user) {
    login();
    return;
  }
  await action(async () => {
    const data = await api("/solutions/" + encodeURIComponent(s.id) + "/like", {
      method: s.liked ? "DELETE" : "PUT",
    });
    s.likes += data.liked ? 1 : -1;
    s.liked = data.liked;
  });
}
async function showComments(s: Solution) {
  await action(async () => {
    if (comments.value[s.id]) {
      delete comments.value[s.id];
      return;
    }
    comments.value[s.id] = await api(
      "/solutions/" + encodeURIComponent(s.id) + "/comments",
    );
  });
}
function openFeedback() {
  if (!state.user) {
    login();
    return;
  }
  feedbackOpen.value = !feedbackOpen.value;
}
async function sendFeedback() {
  const p = problem.value;
  if (!p) return;
  await action(async () => {
    await write("/problems/" + p.id + "/feedback", {
      kind: feedbackKind.value,
      detail: feedbackDetail.value,
      problem_version: p.version,
    });
    if (problem.value?.id === p.id) {
      feedbackOpen.value = false;
      feedbackDetail.value = "";
    }
    toast("反馈已提交");
  });
}
async function comment(s: Solution) {
  if (!state.user) {
    login();
    return;
  }
  await action(async () => {
    await write("/solutions/" + encodeURIComponent(s.id) + "/comments", {
      body: commentText.value[s.id] || "",
    });
    comments.value[s.id] = await api(
      "/solutions/" + encodeURIComponent(s.id) + "/comments",
    );
    commentText.value[s.id] = "";
    toast("评论已提交审核");
  });
}
</script>
<template>
  <div class="practice-page">
    <RouterLink to="/" class="back-link"
      ><ArrowLeft :size="16" />返回题库</RouterLink
    >
    <div class="page-heading">
      <div>
        <h1>
          {{ daily ? "每日一题" : random ? "随机练习" : "题目详情" }}
        </h1>
      </div>
      <span v-if="daily" class="chip"
        ><Sun :size="15" />{{ state.health.date }}</span
      ><button
        v-if="random && problem"
        class="button"
        :disabled="busy || loading || filtersPending"
        @click="next"
      >
        <ArrowRight :size="17" />下一题
      </button>
    </div>
    <PracticeFilterForm
      v-if="daily || random"
      v-model="selectedFilters"
      :daily="daily"
      :busy="busy || loading"
      :locked="daily && !!assignment"
      :opened="!!problem"
      :pending="filtersPending"
      @apply="applyFilters"
    />
    <div v-if="loading" class="loading-panel" role="status">加载中…</div>
    <div v-else-if="error" class="empty-state">
      <h2>题目暂时无法打开</h2>
      <p>{{ error }}</p>
      <button
        class="button"
        @click="daily && dailyAttempted ? openDaily() : load()"
      >
        重新加载
      </button>
    </div>
    <div v-else-if="daily && !state.user" class="empty-state">
      <Sun :size="36" />
      <h2>请先登录</h2>
      <p>登录后打开今日题目，提交并完成自检后打卡。</p>
      <button class="button" @click="login">
        登录<ArrowRight :size="16" />
      </button>
    </div>
    <div v-else-if="daily && !problem" class="empty-state daily-ready">
      <Sun :size="36" />
      <h2>{{ assignment ? "今日题目已分配" : "尚未打开今日题目" }}</h2>
      <p>
        {{
          assignment
            ? "点击“继续今日题目”。"
            : "选择筛选条件，点击“打开今日题目”。"
        }}
      </p>
    </div>
    <template v-else-if="problem"
      ><div v-if="assignment" class="daily-banner">
        <Sun :size="20" /><span>{{
          assignment.completed_at ? "今日已打卡" : "提交并完成自检后打卡"
        }}</span
        ><Check v-if="assignment.completed_at" :size="20" />
      </div>
      <div class="practice-layout">
        <div class="practice-main">
          <section class="paper-card">
            <div class="paper-heading">
              <div class="inline">
                <span class="chip">{{ typeName(problem.type) }}</span
                ><span class="muted small-text"
                  >{{ problem.subject }} · {{ problem.group_name }}</span
                >
              </div>
              <button
                class="icon-button"
                :class="{ liked: problem.liked }"
                :disabled="busy"
                :aria-label="problem.liked ? '取消喜欢题目' : '喜欢题目'"
                @click="likeProblem"
              >
                <Heart
                  :size="20"
                  :fill="problem.liked ? 'currentColor' : 'none'"
                />
              </button>
            </div>
            <h2>{{ problem.title }}</h2>
            <div class="tag-row">
              <span v-for="tag in problem.tags" :key="tag">{{ tag }}</span>
            </div>
            <div class="question-canvas">
              <ImageGallery
                :images="problem.question_images"
                :label="problem.title"
              />
            </div>
            <div class="source-note">
              来源：{{ problem.source.name
              }}<span>版本 {{ problem.version }}</span>
            </div>
            <div class="problem-feedback">
              <button
                class="text-button"
                :disabled="busy"
                :aria-expanded="feedbackOpen"
                aria-controls="feedback-form"
                @click="openFeedback"
              >
                反馈问题
              </button>
              <form
                v-if="feedbackOpen"
                id="feedback-form"
                class="feedback-form"
                @submit.prevent="sendFeedback"
              >
                <label class="field"
                  >问题类型<select v-model="feedbackKind">
                    <option value="incomplete">题目不完整</option>
                    <option value="answer_mismatch">答案不匹配</option>
                    <option value="other">其他问题</option>
                  </select></label
                >
                <label class="field"
                  >问题说明<textarea
                    v-model="feedbackDetail"
                    maxlength="2000"
                    rows="3"
                    :required="feedbackKind === 'other'"
                    placeholder="如缺少哪一部分，或答案哪里不匹配"
                  ></textarea>
                </label>
                <div class="editor-actions">
                  <button
                    type="button"
                    class="button"
                    :disabled="busy"
                    @click="feedbackOpen = false"
                  >
                    取消
                  </button>
                  <button class="button primary" :disabled="busy">
                    {{ busy ? "提交中…" : "提交反馈" }}
                  </button>
                </div>
              </form>
            </div>
          </section>
          <section class="paper-card answer-editor">
            <div class="section-heading compact">
              <h2>我的作答</h2>
              <span class="chip neutral">{{
                problem.type === "choice" ? "自动判分" : "自检"
              }}</span>
            </div>
            <p v-if="problem.progress" class="submission-status" role="status">
              {{
                problem.progress.outdated
                  ? "题目已更新"
                  : statusName(problem.progress.result)
              }}
              ·
              {{
                problem.progress.result_source === "self_assessment"
                  ? "自检结果"
                  : "自动判分"
              }}
            </p>
            <p v-if="problem.progress?.outdated" class="form-warning">
              题目或答案已更新，原记录保留。请重新作答并核对。
            </p>
            <p v-else-if="draftOutdated" class="form-warning">
              题目已更新，草稿保留。请核对后提交。
            </p>
            <div v-if="problem.type === 'choice'" class="option-list">
              <label v-for="option in problem.choice_options" :key="option"
                ><input
                  :checked="options.includes(option)"
                  :type="problem.multiple ? 'checkbox' : 'radio'"
                  :value="option"
                  :name="problem.multiple ? undefined : 'choice'"
                  @change="
                    problem.multiple
                      ? (options = options.includes(option)
                          ? options.filter((o) => o !== option)
                          : [...options, option])
                      : (options = [option])
                  "
                /><span>{{ option }}</span></label
              >
            </div>
            <template v-else
              ><label for="submission" class="small-text muted"
                >填写答案或上传解题过程</label
              ><textarea
                id="submission"
                v-model="text"
                rows="5"
                maxlength="20000"
                placeholder="输入答案或解题过程"
              ></textarea>
              <div v-if="images.length" class="upload-previews">
                <div v-for="img in images" :key="img.id">
                  <img :src="img.url" alt="我的作答图片" /><button
                    aria-label="移除作答图片"
                    @click="images = images.filter((i) => i.id !== img.id)"
                  >
                    <X :size="13" />
                  </button>
                </div></div
            ></template>
            <div class="editor-actions">
              <label
                v-if="problem.type !== 'choice'"
                class="button upload-label"
                :class="{ disabled: busy || !state.user }"
                ><Upload :size="16" />上传图片<input
                  type="file"
                  multiple
                  accept="image/jpeg,image/png,image/webp"
                  :disabled="busy || !state.user"
                  @change="files($event, 'submission')"
              /></label>
              <div class="submission-buttons">
                <button class="button" :disabled="busy" @click="giveUp">
                  <CircleHelp :size="16" />不会
                </button>
                <button class="button primary" :disabled="busy" @click="submit">
                  {{
                    busy
                      ? "处理中…"
                      : !state.user
                        ? "登录后提交"
                        : problem.progress
                          ? "重新提交作答"
                          : "提交作答"
                  }}<ArrowRight :size="16" />
                </button>
              </div>
            </div>
            <small class="muted"
              >作答仅自己可见。{{
                problem.type === "choice"
                  ? "提交后自动判分。"
                  : "最多 5 张图片；重新提交后需再次自检。"
              }}</small
            >
          </section>
          <div v-if="random" class="editor-actions">
            <button
              class="button primary"
              :disabled="busy || loading || filtersPending"
              @click="next"
            >
              下一题<ArrowRight :size="17" />
            </button>
          </div>
          <section class="paper-card reference">
            <button
              class="reference-heading"
              :aria-expanded="answerOpen"
              :disabled="busy || !answerUnlocked"
              @click="toggleAnswer"
            >
              <div class="inline">
                <Eye v-if="answerUnlocked" :size="19" /><LockKeyhole
                  v-else
                  :size="19"
                />
                <h2>参考答案</h2>
              </div>
              <span
                >{{
                  !answerUnlocked ? "待解锁" : answerOpen ? "收起" : "展开答案"
                }}<ChevronDown :size="18" :class="{ rotated: answerOpen }"
              /></span>
            </button>
            <p v-if="!answerUnlocked" class="answer-lock-note">
              提交作答或点击“不会”后可查看。
            </p>
            <div v-if="answerOpen && answer" class="reference-content">
              <p class="small-text muted">{{ answer.provenance }}</p>
              <p v-if="answer.correct_options.length" class="answer-choice">
                正确选项：{{ answer.correct_options.join("、") }}
              </p>
              <ImageGallery :images="answer.images" label="参考答案" />
              <div v-if="!answer.images.length" class="empty-state">
                暂无答案图片
              </div>
              <div
                v-if="
                  problem.progress?.result === 'pending' &&
                  !problem.progress.outdated
                "
                class="self-assessment"
              >
                <div>
                  <h3>自检结果</h3>
                  <p>核对整题后选择对错，结果将保存。</p>
                </div>
                <div class="inline">
                  <button
                    class="button success"
                    :disabled="busy"
                    @click="assess('correct')"
                  >
                    <Check :size="18" />正确</button
                  ><button
                    class="button"
                    :disabled="busy"
                    @click="assess('incorrect')"
                  >
                    <X :size="18" />错误
                  </button>
                </div>
              </div>
              <p v-else-if="!problem.progress" class="form-warning">
                草稿已保留。提交后可自检；查看答案不判错、不打卡。
              </p>
            </div>
          </section>
          <section class="community-section">
            <div class="section-heading">
              <div>
                <h2>
                  题解 <span class="count">{{ community.length }}</span>
                </h2>
              </div>
              <MessageSquare :size="23" />
            </div>
            <div v-if="!community.length" class="community-empty">暂无题解</div>
            <article
              v-for="s in community"
              :key="s.id"
              class="paper-card solution-card"
            >
              <div class="solution-heading">
                <span class="avatar small-avatar">{{
                  s.author.slice(0, 1)
                }}</span
                ><strong>{{ s.author }}</strong
                ><span class="muted small-text">{{
                  s.created_at.slice(0, 10)
                }}</span
                ><span class="chip neutral">{{
                  s.status === "pending" ? "待审核" : statusName(s.status)
                }}</span>
              </div>
              <p class="preserve-text">{{ s.body }}</p>
              <ImageGallery :images="s.images" label="用户题解" />
              <div class="solution-actions">
                <button
                  v-if="s.status === 'approved'"
                  class="text-button inline"
                  @click="likeSolution(s)"
                  :disabled="busy"
                >
                  <Heart
                    :size="16"
                    :fill="s.liked ? 'currentColor' : 'none'"
                  />{{ s.likes }}</button
                ><button
                  v-if="s.status === 'approved'"
                  class="text-button inline"
                  @click="showComments(s)"
                  :disabled="busy"
                >
                  <MessageSquare :size="16" />评论</button
                ><template v-if="s.user_id === state.user?.id"
                  ><button class="text-button" @click="edit(s)">编辑题解</button
                  ><button
                    class="text-button"
                    @click="hide(s)"
                    :disabled="busy"
                  >
                    隐藏
                  </button></template
                >
              </div>
              <div v-if="comments[s.id]" class="comment-area">
                <div v-for="c in comments[s.id]" :key="c.id" class="comment">
                  <strong>{{ c.author }}</strong
                  ><span v-if="c.status === 'pending'" class="muted">
                    · 待审核</span
                  >
                  <p>{{ c.body }}</p>
                </div>
                <form class="comment-form" @submit.prevent="comment(s)">
                  <input
                    v-model="commentText[s.id]"
                    aria-label="评论内容"
                    maxlength="2000"
                    placeholder="输入评论"
                  /><button class="button small" :disabled="busy">发送</button>
                </form>
              </div>
            </article>
            <section
              v-if="problem.progress?.can_publish"
              id="solution-editor"
              class="paper-card"
            >
              <h3>{{ editingVersion ? "编辑题解" : "发布题解" }}</h3>
              <p class="muted small-text">审核通过后公开，作答不会自动发布。</p>
              <label class="sr-only" for="solution-body">题解内容</label
              ><textarea
                id="solution-body"
                v-model="solutionText"
                rows="4"
                maxlength="20000"
                placeholder="输入解题过程"
              ></textarea>
              <div class="upload-previews">
                <div v-for="img in solutionImages" :key="img.id">
                  <img :src="img.url" alt="题解图片" /><button
                    aria-label="移除题解图片"
                    @click="
                      solutionImages = solutionImages.filter(
                        (i) => i.id !== img.id,
                      )
                    "
                  >
                    <X :size="13" />
                  </button>
                </div>
              </div>
              <div class="editor-actions">
                <label class="button upload-label"
                  ><Upload :size="16" />题解图片<input
                    type="file"
                    multiple
                    accept="image/jpeg,image/png,image/webp"
                    :disabled="busy"
                    @change="files($event, 'solution')" /></label
                ><button
                  class="button primary"
                  :disabled="
                    busy || (!solutionText.trim() && !solutionImages.length)
                  "
                  @click="publish"
                >
                  <Send :size="16" />提交题解
                </button>
              </div>
            </section>
            <p v-else class="small-text muted center">
              本版本做对后可发布题解。
            </p>
          </section>
        </div>
      </div></template
    >
  </div>
</template>
