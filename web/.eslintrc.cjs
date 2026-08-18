module.exports = {
  root: true,
  env: {
    browser: true,
    es2021: true,
    node: true,
  },
  extends: [
    "eslint:recommended",
    "plugin:vue/vue3-recommended",
  ],
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
  },
  rules: {
    "vue/multi-word-component-names": "off",
    "vue/max-attributes-per-line": "off",
    "vue/singleline-html-element-content-newline": "off",
    // 格式类规则交由 Prettier 统一管理（与 npm run check 保持一致），避免双格式源冲突
    "vue/html-indent": "off",
    "vue/html-self-closing": "off",
    "vue/html-closing-bracket-newline": "off",
  },
};