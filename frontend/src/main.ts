import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import Library from "./views/Library.vue";
import Problem from "./views/Problem.vue";
import Auth from "./views/Auth.vue";
import Learning from "./views/Learning.vue";
import Admin from "./views/Admin.vue";
import "./style.css";
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: Library },
    { path: "/problems/:id", component: Problem },
    { path: "/practice", component: Problem },
    { path: "/daily", component: Problem },
    { path: "/auth", component: Auth },
    { path: "/me", component: Learning },
    { path: "/admin", component: Admin },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
  scrollBehavior: () => ({ top: 0 }),
});
createApp(App).use(router).mount("#app");
