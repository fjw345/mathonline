<script setup lang="ts">
import { onMounted } from "vue";
import { useRouter } from "vue-router";
import {
  BookOpen,
  Sun,
  Shuffle,
  ChartNoAxesCombined,
  Settings,
  ArrowUpRight,
  LogOut,
  GraduationCap,
} from "lucide-vue-next";
import { api, loadMe, state, toast } from "./api";
import { rememberedPracticeRoute } from "./practiceFilters";
const router = useRouter();
const nav = [
  { path: "/", label: "题库", icon: BookOpen },
  { path: "/daily", label: "每日一题", icon: Sun },
  { path: "/practice", label: "随机练习", icon: Shuffle },
  { path: "/me", label: "我的学习", icon: ChartNoAxesCombined },
];
onMounted(async () => {
  try {
    state.health = await api("/health");
    await loadMe();
  } catch (e) {
    toast((e as Error).message, true);
  }
});
async function logout() {
  try {
    await api("/auth/logout", { method: "POST" });
    state.user = null;
    await router.push("/");
    toast("已退出登录");
  } catch (e) {
    toast((e as Error).message, true);
  }
}
</script>
<template>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <aside class="sidebar">
    <RouterLink to="/" class="brand"
      ><span class="brand-mark"><GraduationCap :size="25" /></span
      ><span>嗟，来算</span></RouterLink
    >
    <RouterLink class="mobile-account" :to="state.user ? '/me' : '/auth'">{{
      state.user ? state.user.nickname.slice(0, 4) : "登录 / 注册"
    }}</RouterLink>
    <nav aria-label="主导航">
      <RouterLink
        v-for="item in nav"
        :key="item.path"
        :to="
          item.path === '/practice'
            ? rememberedPracticeRoute(state.user?.id)
            : item.path
        "
        :class="{ active: $route.path === item.path }"
        ><component :is="item.icon" :size="19" /><span>{{ item.label }}</span
        ><ArrowUpRight
          v-if="$route.path === item.path"
          :size="15"
          class="nav-arrow"
      /></RouterLink>
      <RouterLink
        v-if="state.user && state.user.role !== 'user'"
        to="/admin"
        :class="{ active: $route.path === '/admin' }"
        ><Settings :size="19" /><span>管理</span></RouterLink
      >
    </nav>
    <div class="sidebar-user">
      <template v-if="state.user"
        ><span class="avatar">{{ state.user.nickname.slice(0, 1) }}</span
        ><RouterLink to="/me"
          ><strong>{{ state.user.nickname }}</strong></RouterLink
        ><button class="icon-button" aria-label="退出登录" @click="logout">
          <LogOut :size="17" /></button></template
      ><RouterLink v-else to="/auth" class="button primary wide"
        >登录 / 注册 <ArrowUpRight :size="16"
      /></RouterLink>
    </div>
  </aside>
  <div class="app-body">
    <header class="topbar">
      <span>别样的数学♂练习大战</span
      ><span class="environment"
        ><i></i>{{ state.health.mode === "local" ? "本地版" : "在线版" }}</span
      >
    </header>
    <main id="main" tabindex="-1"><RouterView /></main>
    <footer>
      <span>嗟，来算 · {{ state.health.date.slice(0, 4) }}</span>
    </footer>
  </div>
  <div
    v-if="state.toast"
    class="toast"
    :class="{ error: state.toastError }"
    role="status"
  >
    {{ state.toast
    }}<button @click="state.toast = ''" aria-label="关闭提示">×</button>
  </div>
</template>
