<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  Download,
  Upload,
  Settings,
  ArrowUp,
  ArrowDown,
  X,
  Plus,
  Check,
} from "lucide-vue-next";
import {
  api,
  write,
  upload,
  loadMe,
  state,
  toast,
  typeName,
  statusName,
  feedbackKindName,
  feedbackStatusName,
} from "../api";
import type {
  Problem,
  Solution,
  Comment,
  User,
  ProblemFeedback,
} from "../types";
import ImageGallery from "../components/ImageGallery.vue";
const creating = ref(false);
const feedback = ref<ProblemFeedback[]>([]),
  feedbackFilter = ref("pending"),
  feedbackNotes = ref<Record<string, string>>({});
const feedbackCount = computed(
  () => feedback.value.filter((f) => f.status === "pending").length,
);
const shownFeedback = computed(() =>
  feedback.value.filter(
    (f) => !feedbackFilter.value || f.status === feedbackFilter.value,
  ),
);
const tab = ref("bank"),
  problems = ref<Problem[]>([]),
  pending = ref<{ solutions: Solution[]; comments: Comment[] }>({
    solutions: [],
    comments: [],
  }),
  users = ref<User[]>([]),
  loading = ref(true),
  busy = ref(false),
  error = ref(""),
  search = ref(""),
  status = ref(""),
  editor = ref<HTMLDialogElement>(),
  edit = ref<Problem>(),
  tags = ref(""),
  options = ref(""),
  correct = ref(""),
  reason = ref("");
const shown = computed(() =>
  problems.value.filter(
    (p) =>
      (!search.value || (p.title + p.id).includes(search.value)) &&
      (!status.value || p.status === status.value),
  ),
);
const page = ref(1), total = ref(0), bankLoading = ref(false);
let bankRequest = 0;
async function loadBank() {
  const request = ++bankRequest;
  bankLoading.value = true;
  try {
    const query = new URLSearchParams({ page: String(page.value), page_size: "30", q: search.value, status: status.value });
    const data = await api("/admin/problems?" + query);
    if (request === bankRequest) {
      problems.value = data.items;
      total.value = data.total;
    }
  } catch (e) {
    if (request === bankRequest) error.value = "题库：" + (e as Error).message;
  } finally {
    if (request === bankRequest) bankLoading.value = false;
  }
}
let searchTimer: ReturnType<typeof setTimeout>;
watch([search, status], () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { page.value = 1; void loadBank(); }, 300);
});
async function turnPage(step: number) {
  page.value += step;
  await loadBank();
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    await loadMe();
    if (!state.user || state.user.role === "user") return;
    if (state.user.role === "admin") {
      const results = await Promise.allSettled([
        api("/admin/moderation"), loadBank(), api("/admin/users"), api("/admin/feedback"),
      ]);
      const labels = ["内容审核", "题库", "用户角色", "题目反馈"];
      results.forEach((result, i) => {
        if (result.status === "rejected") error.value += `${labels[i]}：${result.reason.message} `;
      });
      if (results[0].status === "fulfilled") pending.value = results[0].value;
      if (results[2].status === "fulfilled") users.value = results[2].value;
      if (results[3].status === "fulfilled") feedback.value = results[3].value;
    } else {
      pending.value = await api("/admin/moderation");
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
onMounted(load);
function open(p: Problem) {
  creating.value = false;
  edit.value = JSON.parse(JSON.stringify(p));
  tags.value = p.tags.join("、");
  options.value = p.choice_options.join(",");
  correct.value = p.answer!.correct_options.join(",");
  editor.value?.showModal();
}
function newProblem() {
  creating.value = true;
  edit.value = {
    id: "",
    source_id: "custom-source",
    title: "",
    type: "solution",
    year: 2026,
    group_name: "自定义组别",
    subject: "高等数学",
    tags: [],
    status: "published",
    version: 1,
    grading_mode: "self_assessment",
    question_images: [],
    choice_options: [],
    multiple: false,
    verified: false,
    answer_verified: false,
    progress: null,
    liked: false,
    source: {
      id: "custom-source",
      name: "新增题源",
      source_url: "",
      rights_note: "",
      verified: false,
    },
    answer: { images: [], correct_options: [], provenance: "平台整理参考答案" },
  };
  tags.value = "";
  options.value = "";
  correct.value = "";
  editor.value?.showModal();
}
async function action(fn: () => Promise<void>) {
  busy.value = true;
  try {
    await fn();
  } catch (e) {
    toast((e as Error).message, true);
  } finally {
    busy.value = false;
  }
}
async function save() {
  await action(async () => {
    const p = edit.value!;
    const payload = {
      ...p,
      tags: tags.value
        .split(/[、,，]/)
        .map((t) => t.trim())
        .filter(Boolean),
      choice_options: options.value
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      question_images: p.question_images.map((i) => i.id),
      grading_mode: p.type === "choice" ? "choice" : "self_assessment",
      answer: {
        ...p.answer,
        images: p.answer!.images.map((i) => i.id),
        correct_options: correct.value
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      },
    };
    if (creating.value) {
      const keys = [
        "id",
        "source_id",
        "title",
        "type",
        "year",
        "group_name",
        "subject",
        "tags",
        "status",
        "version",
        "grading_mode",
        "question_images",
        "choice_options",
        "multiple",
        "verified",
        "answer_verified",
        "answer",
      ];
      const data = Object.fromEntries(
        keys.map((k) => [k, (payload as Record<string, unknown>)[k]]),
      );
      data.source_id = p.source.id;
      data.source = p.source;
      await write("/admin/problems", data);
    } else {
      await write(
        "/admin/sources/" + p.source.id,
        {
          source_url: p.source.source_url,
          rights_note: p.source.rights_note,
          verified: p.source.verified,
        },
        "PATCH",
      );
      await write("/admin/problems/" + p.id, payload, "PUT");
    }
    editor.value?.close();
    await load();
    toast(creating.value ? "题目已添加" : "题目已保存，历史记录保留");
  });
}
function move(role: string, index: number, direction: number) {
  const rows =
    role === "question"
      ? edit.value!.question_images
      : edit.value!.answer!.images;
  const j = index + direction;
  if (j >= 0 && j < rows.length)
    [rows[index], rows[j]] = [rows[j], rows[index]];
}
async function imageUpload(event: Event, role: string) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  await action(async () => {
    const image = await upload(file, role);
    (role === "question"
      ? edit.value!.question_images
      : edit.value!.answer!.images
    ).push(image);
  });
  input.value = "";
}
async function importFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  await action(async () => {
    const manifest = JSON.parse(await file.text());
    const result = await write("/admin/import", manifest);
    await load();
    toast(
      `导入完成：新增 ${result.created}，更新 ${result.updated}，保护人工修改 ${result.protected}`,
    );
  });
  input.value = "";
}
async function exportFile() {
  await action(async () => {
    const data = await api("/admin/export");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "mathonline-bank.json";
    a.click();
    URL.revokeObjectURL(url);
  });
}
async function moderate(kind: string, c: Solution | Comment, decision: string) {
  await action(async () => {
    await write(
      "/admin/moderation/" + kind + "/" + encodeURIComponent(c.id),
      {
        status: decision,
        reason: reason.value,
        version: "version" in c ? c.version : undefined,
      },
      "PATCH",
    );
    pending.value = await api("/admin/moderation");
    toast(decision === "approved" ? "审核已通过" : "内容已隐藏");
  });
}
async function roleChange(u: User, event: Event) {
  await action(async () => {
    await write(
      "/admin/users/" + u.id,
      { role: (event.target as HTMLSelectElement).value },
      "PATCH",
    );
    await load();
  });
}
async function editFeedbackProblem(f: ProblemFeedback) {
  await action(async () => {
    let p = problems.value.find((p) => p.id === f.problem_id);
    if (!p) {
      const data = await api("/admin/problems?page=1&pid=" + encodeURIComponent(f.problem_id));
      p = data.items[0];
    }
    if (p) open(p);
    else throw new Error("题目不存在，请刷新反馈列表。");
  });
}
async function reviewFeedback(
  f: ProblemFeedback,
  status: "resolved" | "dismissed",
) {
  await action(async () => {
    await write(
      "/admin/feedback/" + f.id,
      {
        status,
        review_note: feedbackNotes.value[f.id] || "",
      },
      "PATCH",
    );
    feedback.value = await api("/admin/feedback");
    delete feedbackNotes.value[f.id];
    toast("反馈已处理");
  });
}
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>管理</h1>
    </div>
    <Settings :size="28" />
  </div>
  <div v-if="loading" class="loading-panel">加载中…</div>
  <div v-if="error" class="form-error" role="alert">
    {{ error }}<button class="button small" @click="load">重试</button>
  </div>
  <div
    v-if="!loading && (!state.user || state.user.role === 'user')"
    class="empty-state"
  >
    <h2>需要管理权限</h2>
    <p>登录管理员或审核账号后使用此页面。</p>
    <RouterLink to="/auth?next=/admin" class="button primary">登录</RouterLink>
  </div>
  <template v-if="!loading && state.user && state.user.role !== 'user'"
    ><div class="tabs">
      <button
        v-if="state.user.role === 'admin'"
        :class="{ selected: tab === 'bank' }"
        @click="tab = 'bank'"
      >
        题库维护</button
      ><button
        v-if="state.user.role === 'admin'"
        :class="{ selected: tab === 'feedback' }"
        @click="tab = 'feedback'"
      >
        题目反馈 <span class="count">{{ feedbackCount }}</span></button
      ><button
        :class="{ selected: tab === 'moderation' }"
        @click="tab = 'moderation'"
      >
        内容审核
        <span class="count">{{
          pending.solutions.length + pending.comments.length
        }}</span></button
      ><button
        v-if="state.user.role === 'admin'"
        :class="{ selected: tab === 'users' }"
        @click="tab = 'users'"
      >
        用户角色
      </button>
    </div>
    <section v-if="tab === 'bank' && state.user.role === 'admin'">
      <div class="admin-toolbar">
        <label class="field"
          >搜索题目<input
            v-model="search"
            placeholder="年份、题号或 ID" /></label
        ><label class="field"
          >发布状态<select v-model="status">
            <option value="">全部</option>
            <option value="draft">草稿</option>
            <option value="published">已发布</option>
            <option value="archived">已下架</option>
          </select></label
        ><button class="button primary" :disabled="busy" @click="newProblem">
          <Plus :size="16" />新增题目</button
        ><button class="button" :disabled="busy" @click="exportFile">
          <Download :size="16" />导出清单</button
        ><label class="button upload-label"
          ><Upload :size="16" />导入清单<input
            type="file"
            accept="application/json,.json"
            :disabled="busy"
            @change="importFile"
        /></label>
      </div>
      <p class="small-text muted">
        同一题目使用相同 ID。重复导入保留人工修改，不删除原记录。
      </p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>题目</th>
              <th>题型</th>
              <th>状态</th>
              <th>版本</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in shown" :key="p.id">
              <td>
                <strong>{{ p.title }}</strong
                ><small>{{ p.id }}</small>
              </td>
              <td>{{ typeName(p.type) }}</td>
              <td>
                <span class="chip neutral">{{ statusName(p.status) }}</span>
              </td>
              <td>{{ p.version }}</td>
              <td>
                <button class="button small" @click="open(p)">编辑</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="admin-toolbar">
        <button class="button" :disabled="bankLoading || page <= 1" @click="turnPage(-1)">上一页</button>
        <span>第 {{ page }} 页 · 共 {{ total }} 道题</span>
        <button class="button" :disabled="bankLoading || page * 30 >= total" @click="turnPage(1)">下一页</button>
      </div>
    </section>
    <section v-if="tab === 'feedback' && state.user.role === 'admin'">
      <label class="field feedback-filter"
        >处理状态<select v-model="feedbackFilter">
          <option value="pending">待处理</option>
          <option value="resolved">已处理</option>
          <option value="dismissed">无需修改</option>
          <option value="">全部</option>
        </select></label
      >
      <article
        v-for="f in shownFeedback"
        :key="f.id"
        class="paper-card moderation-card feedback-card"
      >
        <div class="section-heading compact">
          <h2>{{ f.problem_title }}</h2>
          <span class="chip neutral">{{ feedbackStatusName(f.status) }}</span>
        </div>
        <p>{{ feedbackKindName(f.kind) }} · {{ f.reporter }}</p>
        <p class="small-text muted">
          反馈版本 {{ f.problem_version
          }}<span v-if="f.current_version !== f.problem_version">
            · 当前版本 {{ f.current_version }}</span
          >
          ·
          {{
            new Date(f.created_at).toLocaleString("zh-CN", {
              timeZone: "Asia/Shanghai",
            })
          }}
        </p>
        <p v-if="f.detail" class="preserve-text">{{ f.detail }}</p>
        <div class="inline">
          <RouterLink :to="'/problems/' + f.problem_id" class="text-button"
            >查看题目</RouterLink
          >
          <button
            class="text-button"
            :disabled="busy"
            @click="editFeedbackProblem(f)"
          >
            编辑题目
          </button>
        </div>
        <template v-if="f.status === 'pending'">
          <label class="field"
            >处理说明<input
              v-model="feedbackNotes[f.id]"
              maxlength="1000"
              placeholder="选填：修改内容或核查结果"
          /></label>
          <div class="editor-actions">
            <button
              class="button"
              :disabled="busy"
              @click="reviewFeedback(f, 'dismissed')"
            >
              无需修改
            </button>
            <button
              class="button primary"
              :disabled="busy"
              @click="reviewFeedback(f, 'resolved')"
            >
              标记已处理
            </button>
          </div>
        </template>
        <p v-else class="small-text muted">
          {{ f.reviewer }}：{{ f.review_note || "未填写处理说明" }}
        </p>
      </article>
      <div v-if="!shownFeedback.length" class="empty-state">暂无反馈</div>
    </section>
    <section v-if="tab === 'moderation' || state.user.role === 'moderator'">
      <label class="field"
        >处理说明<input
          v-model="reason"
          maxlength="500"
          placeholder="选填：修改建议或隐藏原因"
      /></label>
      <article
        v-for="s in pending.solutions"
        :key="s.id"
        class="paper-card moderation-card"
      >
        <div class="inline">
          <span class="chip">题解</span><strong>{{ s.author }}</strong
          ><RouterLink :to="'/problems/' + s.problem_id" class="text-button"
            >查看题目</RouterLink
          >
        </div>
        <p class="preserve-text">{{ s.body }}</p>
        <ImageGallery :images="s.images" label="待审核题解" />
        <div class="editor-actions">
          <button
            class="button"
            :disabled="busy"
            @click="moderate('solutions', s, 'hidden')"
          >
            隐藏</button
          ><button
            class="button primary"
            :disabled="busy"
            @click="moderate('solutions', s, 'approved')"
          >
            <Check :size="16" />审核通过
          </button>
        </div>
      </article>
      <article
        v-for="c in pending.comments"
        :key="c.id"
        class="paper-card moderation-card"
      >
        <div class="inline">
          <span class="chip">评论</span><strong>{{ c.author }}</strong>
        </div>
        <p>{{ c.body }}</p>
        <div class="editor-actions">
          <button
            class="button"
            :disabled="busy"
            @click="moderate('comments', c, 'hidden')"
          >
            隐藏</button
          ><button
            class="button primary"
            :disabled="busy"
            @click="moderate('comments', c, 'approved')"
          >
            审核通过
          </button>
        </div>
      </article>
      <div
        v-if="!pending.solutions.length && !pending.comments.length"
        class="empty-state"
      >
        暂无待审核内容
      </div>
    </section>
    <section
      v-if="tab === 'users' && state.user.role === 'admin'"
      class="table-wrap"
    >
      <table>
        <thead>
          <tr>
            <th>昵称</th>
            <th>邮箱</th>
            <th>角色</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td>{{ u.nickname }}</td>
            <td>{{ u.email }}</td>
            <td>
              <select
                :value="u.role"
                :disabled="busy"
                :aria-label="u.nickname + '的角色'"
                @change="roleChange(u, $event)"
              >
                <option value="user">用户</option>
                <option value="moderator">审核人员</option>
                <option value="admin">管理员</option>
              </select>
            </td>
          </tr>
        </tbody>
      </table>
    </section></template
  >
  <dialog ref="editor" class="editor-dialog">
    <template v-if="edit"
      ><div class="dialog-heading">
        <h2>{{ creating ? "新增题目" : "编辑题目" }}</h2>
        <button
          class="icon-button"
          :disabled="busy"
          @click="editor?.close()"
          aria-label="关闭编辑"
        >
          <X />
        </button>
      </div>
      <p class="small-text muted">{{ edit.id }} · 版本 {{ edit.version }}</p>
      <form @submit.prevent="save">
        <template v-if="creating"
          ><label class="field"
            >稳定题目 ID<input
              v-model.trim="edit.id"
              required
              pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{0,159}"
              placeholder="如 bjt-math-2026-q1"
            /><small>以后修改题干或答案时保留这个 ID。</small></label
          ><label class="field"
            >来源 ID<input
              v-model.trim="edit.source.id"
              required
              placeholder="同卷题目使用相同来源 ID" /></label
          ><label class="field"
            >来源名称<input
              v-model.trim="edit.source.name"
              required /></label></template
        ><label class="field"
          >标题<input v-model="edit.title" required maxlength="200"
        /></label>
        <div class="form-grid">
          <label class="field"
            >年份<input
              v-model.number="edit.year"
              type="number"
              min="1900"
              max="2100"
              required /></label
          ><label class="field"
            >组别<input v-model="edit.group_name" required /></label
          ><label class="field"
            >科目<input v-model="edit.subject" required /></label
          ><label class="field"
            >题型<select v-model="edit.type">
              <option value="blank">填空题</option>
              <option value="solution">解答题</option>
              <option value="choice">选择题</option>
            </select></label
          ><label class="field"
            >状态<select v-model="edit.status">
              <option value="draft">草稿</option>
              <option value="published">发布</option>
              <option value="archived">下架</option>
            </select></label
          >
        </div>
        <label class="field"
          >题库标签（以顿号或逗号分隔）<input
            v-model="tags"
            placeholder="例如：全国大学生数学竞赛·非数" /></label
        ><template v-if="edit.type === 'choice'"
          ><label class="field"
            >选项 ID（逗号分隔）<input
              v-model="options"
              placeholder="A,B,C,D" /></label
          ><label class="field"
            >正确选项 ID<input v-model="correct" placeholder="A" /></label
          ><label class="check-label"
            ><input v-model="edit.multiple" type="checkbox" />多选题</label
          ></template
        >
        <div
          v-for="role in ['question', 'answer']"
          :key="role"
          class="admin-images"
        >
          <h3>
            {{ role === "question" ? "题干图片" : "参考答案图片" }}
            <small>按顺序展示</small>
          </h3>
          <div
            v-for="(img, i) in role === 'question'
              ? edit.question_images
              : edit.answer!.images"
            :key="img.id"
            class="admin-image-row"
          >
            <img
              :src="img.url"
              :alt="role === 'question' ? '题干' : '答案'"
            /><span>{{ i + 1 }}</span
            ><button
              class="icon-button"
              type="button"
              :disabled="i === 0"
              aria-label="图片上移"
              @click="move(role, i, -1)"
            >
              <ArrowUp :size="17" /></button
            ><button
              class="icon-button"
              type="button"
              aria-label="图片下移"
              @click="move(role, i, 1)"
            >
              <ArrowDown :size="17" /></button
            ><button
              class="icon-button"
              type="button"
              aria-label="移除图片"
              @click="
                role === 'question'
                  ? edit.question_images.splice(i, 1)
                  : edit.answer!.images.splice(i, 1)
              "
            >
              <X :size="17" />
            </button>
          </div>
          <label class="button small upload-label"
            ><Plus :size="15" />添加图片<input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              :disabled="busy"
              @change="imageUpload($event, role)"
          /></label>
        </div>
        <label class="field"
          >参考答案来源说明<input
            v-model="edit.answer!.provenance"
            required /></label
        ><label class="field"
          >来源链接<input
            v-model="edit.source.source_url"
            type="url"
            placeholder="https://…" /></label
        ><label class="field"
          >材料使用依据<textarea
            v-model="edit.source.rights_note"
            rows="2"
          ></textarea>
        </label>
        <p class="form-warning">保存后版本递增，历史记录保留。</p>
        <div class="editor-actions">
          <button
            type="button"
            class="button"
            :disabled="busy"
            @click="editor?.close()"
          >
            取消</button
          ><button class="button primary" :disabled="busy">
            {{ busy ? "保存中…" : "保存修改" }}
          </button>
        </div>
      </form></template
    >
  </dialog>
</template>
