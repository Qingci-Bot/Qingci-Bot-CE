// Copyright (C) 2026 Zhou Zhe (aka luoqingci)
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms under GPLv3 Section 7: see NOTICE

// ESLint 10 只认 flat config（原 .eslintrc.cjs 在 v9 起已废弃、v10 移除）。
// 规则集与旧配置等价：eslint:recommended + plugin:vue/vue3-recommended，
// 环境为 browser + node，格式类规则继续交给 Prettier（npm run check）。
import js from '@eslint/js'
import globals from 'globals'
import pluginVue from 'eslint-plugin-vue'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['src/**/*.{js,cjs,mjs,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      // ESLint 9+ 起 no-unused-vars 默认检查 catch 参数（caughtErrors: 'all'），
      // 与 ESLint 8 行为不同；显式保持旧行为，避免工具链升级顺带改 15 处业务代码
      'no-unused-vars': ['error', { caughtErrors: 'none' }],
      'vue/multi-word-component-names': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-indent': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-closing-bracket-newline': 'off',
    },
  },
]
