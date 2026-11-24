// Copyright (C) 2026 Zhou Zhe (aka luoqingci)
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms under GPLv3 Section 7: see NOTICE

/**
 * 系统设置页的保存 payload 组装（纯函数，便于单测）。
 *
 * 背景：Settings.vue 的 QQ 卡片 v-model 绑定一直是对的，但保存时组装
 * newConfig.platforms 只放了 telegram / onebot12，qq 被整个丢掉——后端
 * PUT /api/config 是深度合并，payload 里没有的键原样保留，于是「填入
 * AppID/AppSecret 保存后仍为空」。把组装抽到这里并用测试锁定每个
 * platforms 子键，防止新增平台卡片时再漏。
 */

/**
 * "a, b；c
 * d" → ['a', 'b', 'c', 'd']；asNumber=true 时转数字并过滤 NaN
 */
export function parseList(str, asNumber = true) {
  if (!str || !str.trim()) return [];
  const arr = str
    .split(/[,，\n]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (asNumber) {
    return arr.map(Number).filter((n) => !isNaN(n));
  }
  return arr;
}

/**
 * 由表单 + 当前已加载配置组装 PUT /api/config 的 payload。
 * 敏感字段若被 GET 脱敏为 "***"，原样上送——后端 _filter_masked 会
 * 跳过占位符、保留原值，前端不自行判断。
 */
export function buildConfigPayload({ form, runLogEnabled, currentConfig }) {
  const newConfig = JSON.parse(JSON.stringify(currentConfig));
  newConfig.bot = {
    ...form.bot,
    super_admin: form.bot.super_admin ? String(form.bot.super_admin).trim() : null,
    trigger_keywords: parseList(form.bot.trigger_keywords, false),
    admin_users: parseList(form.bot.admin_users, false),
    group_blacklist: parseList(form.bot.group_blacklist, false),
    user_blacklist: parseList(form.bot.user_blacklist, false),
  };
  newConfig.onebot = {
    ...form.onebot,
    port: Number(form.onebot.port) || 3001,
  };
  newConfig.platforms = {
    telegram: {
      ...form.platforms.telegram,
      name: 'telegram',
      poll_interval: Number(form.platforms.telegram.poll_interval) || 1.0,
    },
    onebot12: {
      ...form.platforms.onebot12,
      name: 'onebot12',
      port: Number(form.platforms.onebot12.port) || 3002,
    },
    qq: {
      ...form.platforms.qq,
      name: 'qq',
    },
  };
  newConfig.api_key = form.api_key;
  newConfig.log = { ...(newConfig.log || {}), run_log_enabled: runLogEnabled };
  return newConfig;
}
