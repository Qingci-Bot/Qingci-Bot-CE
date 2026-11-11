// Copyright (C) 2026 Zhou Zhe (aka luoqingci) and Qingci-Bot contributors
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms under GPLv3 Section 7: see LICENSE

import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
import './styles/main.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.mount('#app');
