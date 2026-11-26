// Copyright (C) 2026 Zhou Zhe (aka luoqingci)
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms under GPLv3 Section 7: see NOTICE

import { describe, it, expect } from 'vitest';
import { buildConfigPayload, parseList } from './configPayload';

function makeForm() {
  return {
    bot: {
      name: 'Qingci-Bot CE',
      trigger_mode: 'at',
      trigger_keywords: '/bot, /ai',
      super_admin: '12345',
      admin_users: '111, 222',
      group_blacklist: '',
      user_blacklist: '',
    },
    onebot: { enabled: true, host: '127.0.0.1', port: '3001', access_token: '' },
    platforms: {
      telegram: { name: 'telegram', enabled: false, token: '', poll_interval: 1.0 },
      onebot12: {
        name: 'onebot12',
        enabled: false,
        host: '127.0.0.1',
        port: '3002',
        access_token: '',
      },
      qq: { name: 'qq', enabled: true, app_id: '10001', app_secret: 'sec-abc', sandbox: false },
    },
    api_key: 'key-1',
  };
}

describe('buildConfigPayload', () => {
  it('payload 必须包含 qq 节及其全部字段（回归：曾漏发导致「填了保存仍为空」）', () => {
    const payload = buildConfigPayload({
      form: makeForm(),
      runLogEnabled: true,
      currentConfig: {},
    });
    expect(payload.platforms.qq).toEqual({
      name: 'qq',
      enabled: true,
      app_id: '10001',
      app_secret: 'sec-abc',
      sandbox: false,
    });
  });

  it('三个平台节都要在 payload 里，一个都不能少', () => {
    const payload = buildConfigPayload({
      form: makeForm(),
      runLogEnabled: true,
      currentConfig: {},
    });
    expect(Object.keys(payload.platforms).sort()).toEqual(['onebot12', 'qq', 'telegram']);
    expect(payload.onebot.port).toBe(3001);
    expect(payload.platforms.onebot12.port).toBe(3002);
    expect(payload.platforms.telegram.poll_interval).toBe(1.0);
  });

  it('端口为空/非法时省略该键（后端深合并保留原值），绝不静默回退默认端口', () => {
    const form = makeForm();
    form.onebot.port = '';
    form.platforms.onebot12.port = 'abc';
    const payload = buildConfigPayload({
      form,
      runLogEnabled: true,
      currentConfig: { onebot: { port: 4000 }, platforms: { onebot12: { port: 4002 } } },
    });
    expect(payload.onebot.port).toBeUndefined();
    expect(payload.platforms.onebot12.port).toBeUndefined();
    // 非端口数值字段同样不回退：poll_interval 为空时省略
    const form2 = makeForm();
    form2.platforms.telegram.poll_interval = '';
    const payload2 = buildConfigPayload({ form: form2, runLogEnabled: true, currentConfig: {} });
    expect(payload2.platforms.telegram.poll_interval).toBeUndefined();
  });

  it('bot 名单字符串按逗号/换行拆分；空串得空数组', () => {
    const payload = buildConfigPayload({
      form: makeForm(),
      runLogEnabled: true,
      currentConfig: {},
    });
    expect(payload.bot.trigger_keywords).toEqual(['/bot', '/ai']);
    expect(payload.bot.admin_users).toEqual(['111', '222']);
    expect(payload.bot.group_blacklist).toEqual([]);
    expect(payload.bot.super_admin).toBe('12345');
  });

  it('敏感字段为脱敏占位符 "***" 时原样上送（后端负责过滤保留原值）', () => {
    const form = makeForm();
    form.platforms.qq.app_secret = '***';
    form.onebot.access_token = '***';
    const currentConfig = { log: { level: 'INFO' } };
    const payload = buildConfigPayload({ form, runLogEnabled: false, currentConfig });
    expect(payload.platforms.qq.app_secret).toBe('***');
    expect(payload.onebot.access_token).toBe('***');
  });

  it('runLogEnabled 写入 log 节且不覆盖其它 log 字段', () => {
    const currentConfig = { log: { level: 'DEBUG', retention_days: 7 } };
    const payload = buildConfigPayload({ form: makeForm(), runLogEnabled: false, currentConfig });
    expect(payload.log.run_log_enabled).toBe(false);
    expect(payload.log.level).toBe('DEBUG');
    expect(payload.log.retention_days).toBe(7);
  });
});

describe('parseList', () => {
  it('支持中英文逗号与换行混用；asNumber 过滤非法数字', () => {
    expect(parseList('1, 2，3\n4')).toEqual([1, 2, 3, 4]);
    expect(parseList('a, b', false)).toEqual(['a', 'b']);
    expect(parseList('')).toEqual([]);
  });
});
