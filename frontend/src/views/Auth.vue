<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowRight, KeyRound } from "lucide-vue-next";
import { write, state, loadMe, toast } from "../api";
const route = useRoute(),
  router = useRouter();
const mode = ref(route.query.mode === "recovery" ? "recovery" : "login");
const email = ref(""),
  password = ref(""),
  nickname = ref(""),
  busy = ref(false),
  error = ref(""),
  message = ref("");
watch(mode, () => {
  error.value = "";
  message.value = "";
});
onMounted(async () => {
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.get("access_token")) {
    try {
      const data = await write("/auth/session", {
        access_token: params.get("access_token"),
        refresh_token: params.get("refresh_token"),
      });
      state.user = data.user;
      history.replaceState(null, "", location.pathname + location.search);
      if (params.get("type") === "recovery") mode.value = "recovery";
      else {
        await loadMe();
        router.replace("/me");
      }
    } catch (e) {
      error.value = (e as Error).message;
    }
  }
});
async function submit() {
  busy.value = true;
  error.value = "";
  message.value = "";
  try {
    if (mode.value === "reset") {
      await write("/auth/reset", { email: email.value });
      message.value = "如果邮箱已注册，重置邮件将发送到该邮箱。";
    } else if (mode.value === "recovery") {
      await write("/auth/password", { password: password.value }, "PATCH");
      toast("密码已更新");
      router.replace("/me");
    } else {
      const data = await write(
        "/auth/" + (mode.value === "register" ? "register" : "login"),
        {
          email: email.value,
          password: password.value,
          nickname: nickname.value || "同学",
        },
      );
      if (data.verification_required) {
        message.value = "请检查邮箱，完成验证后回来登录。";
        return;
      }
      state.user = data.user;
      await loadMe();
      const dest = String(route.query.next || "/me");
      router.replace(
        dest.startsWith("/") && !dest.startsWith("//") ? dest : "/me",
      );
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="auth-page">
    <section class="auth-card">
      <KeyRound :size="25" />
      <h2>
        {{
          mode === "register"
            ? "注册"
            : mode === "reset"
              ? "找回密码"
              : mode === "recovery"
                ? "设置新密码"
                : "登录"
        }}
      </h2>
      <form @submit.prevent="submit">
        <label v-if="mode !== 'recovery'" class="field"
          >邮箱<input
            v-model.trim="email"
            type="email"
            required
            autocomplete="email"
            placeholder="you@example.com" /></label
        ><label v-if="mode === 'register'" class="field"
          >昵称<input
            v-model.trim="nickname"
            maxlength="30"
            required
            autocomplete="nickname"
            placeholder="输入昵称" /></label
        ><label v-if="mode !== 'reset'" class="field"
          >密码<input
            v-model="password"
            type="password"
            :minlength="mode === 'login' ? 1 : 8"
            maxlength="128"
            required
            :autocomplete="
              mode === 'login' ? 'current-password' : 'new-password'
            "
            placeholder="至少 8 位"
        /></label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <p v-if="message" class="form-success" role="status">{{ message }}</p>
        <button class="button primary wide" :disabled="busy">
          {{
            busy
              ? "处理中…"
              : mode === "register"
                ? "注册"
                : mode === "reset"
                  ? "发送重置邮件"
                  : mode === "recovery"
                    ? "更新密码"
                    : "登录"
          }}<ArrowRight :size="17" />
        </button>
      </form>
      <div class="auth-links">
        <button
          class="text-button"
          @click="mode = mode === 'register' ? 'login' : 'register'"
        >
          {{ mode === "register" ? "登录" : "注册" }}</button
        ><button
          v-if="mode === 'login'"
          class="text-button"
          @click="mode = 'reset'"
        >
          忘记密码</button
        ><button
          v-if="mode === 'reset'"
          class="text-button"
          @click="mode = 'login'"
        >
          返回登录
        </button>
      </div>
      <small v-if="state.health.mode === 'local'" class="local-note"
        >本地版不发送验证或重置邮件。首个账号为管理员。</small
      >
    </section>
  </div>
</template>
