# 编码规范

> 本文档定义 Qingci-Bot-CE 的代码组织与命名约定，结合 `pyproject.toml` 中的
> ruff / mypy / pytest 配置。所有新增代码必须遵守本规范。

## 1. 质量门禁（必读）

提交前必须通过以下检查（`pre-commit` 已配置，运行 `pre-commit install` 启用）：

```bash
# 在 Qingci-Bot-CE/ 目录下运行（产物留在本目录内）
ruff check .          # 静态检查
ruff format --check . # 格式检查
mypy api bot desktop  # 类型检查（覆盖 api/bot/desktop，与 CI 一致）
pytest                # 测试（含覆盖率）
```

| 工具 | 配置要点 | 说明 |
|------|----------|------|
| ruff | `target-version = py310`，`line-length = 100`，select `E F I W UP B C4` | 忽略 `E501`（行宽由 formatter 处理）、`B008` |
| mypy | `python_version = "3.12"`，`warn_return_any = true` | 排除 `tests/`；覆盖 `api/` `bot/` `desktop/` |
| pytest | `asyncio_mode = "auto"`，`testpaths = ["tests"]` | 默认带 `--cov=bot --cov=api`，门槛 50%（与 CI 一致；LLM 子系统另有 70% 门槛） |

## 2. 类型与语法约定

- 使用 **PEP 604 联合类型**：`int | None` 而非 `Optional[int]`（`UP` 规则强制）。
- 不使用 `Mapped[...]` 注解（SQLModel 0.0.x 不兼容）；模型用标准类型注解 `int | None`。
- 通用类型用内置泛型：`list[T]` / `dict[K, V]` / `set[T]`（`UP` 规则强制），不用 `typing.List`。
- `except` 块抛出 `HTTPException` / `ValueError` 时用 `raise ... from None`，消除异常链噪音。
- FastAPI 路由参数：`Request` 用 `request: Request = None`，**不要**写成 `Optional[Request]`（后者会导致路由注册失败）。
- 显式返回类型：公开函数/方法标注返回类型；`warn_return_any` 开启，避免静默返回 `Any`（必要时用 `cast` 或在文件头 `# mypy: disable-error-code=...` 局部豁免）。
- forward-ref 联合（`"SomeType" | None`）在模块级必须配合 `from __future__ import annotations`，否则运行时 `TypeError`。

## 3. 分层与依赖方向

```
web/ ──HTTP──▶ api/ ──调用──▶ bot/ ──依赖──▶ bot/core, bot/llm, bot/db, bot/rag
                                     ▲
                bot/plugin 依赖框架层；框架层不得反向依赖插件
```

- **`api/` 只做 HTTP 编排**（鉴权、参数校验、响应组装），业务逻辑下沉到 `bot/`。
- **`bot/core/` 为框架层**：生命周期、连接、调度、DI、组合根装配、事件总线、会话状态。不含具体业务与功能组件（功能组件放 `bot/` 根级：alerter / filter / broadcast / logformat / html_renderer）。
- **`bot/plugin/` 提供插件机制**；`bot/plugin/protocol/` 为协议层薄转发（唯一实现来源为 Plugins-SDK），顶层同名文件为兼容再导出；业务能力以内置插件（`builtin/`）或外部插件（`plugins/`）承载。
- **禁止循环 import**：框架层不 import 插件；`bot/core` 内部模块间保持单向依赖。
- 新增能力优先放在已有的领域模块（`llm`/`db`/`rag`），避免在 `core` 堆积单一模块。

### 协议层归属（重要）

插件协议层（`PluginBase`/`Matcher`/`MatcherContext`/`Permission`/`Rule`/`MessageContext`/`RateLimiter`）的**唯一实现在 `Plugins-SDK`**（`qingci_plugin_sdk` 包）。本仓库约束：

- `bot/plugin/protocol/{base,matcher,permission,rule,ratelimit,session,events,context}.py` **只允许薄转发**（`from qingci_plugin_sdk.xxx import *` + 显式 `__all__`），不得新增协议层实现；`bot/plugin/` 顶层同名文件仅作**兼容再导出**（不新增逻辑）。
- 修改协议行为（权限语义、匹配规则、基类方法等）必须改 `Plugins-SDK` 仓库，并同步主项目 git 依赖版本。
- 主项目可在 `bot/plugin/llm_tool.py` 等保留**运行时专属逻辑**（如注册到 `ToolRegistry`），但协议定义本身不得在本仓库重复。

### 装配与单例

- 组件装配统一走 `bot/core/composition.py` 的 `assemble_bot(bot)`：创建服务 + `register_sync` 进 DI。`QingciBot.__init__` 不得手写组件创建。
- 访问当前 bot 实例：优先通过 handler 参数注入（`QingciBot` 类型注解或 `Depends`）；模块级 `get_bot()` 仅用于非异步入口（如 API 路由同步代码），内部经 DI 容器 `resolve_sync` 解析，不新增模块级单例。

## 4. 命名约定

| 对象 | 约定 | 示例 |
|------|------|------|
| 包 / 模块 | 小写下划线 `snake_case` | `event_bus.py`, `session_state.py`, `composition.py` |
| 类 | 大驼峰 `PascalCase` | `EventBus`, `PluginManager` |
| 函数 / 方法 / 变量 | 小写下划线 | `resolve_handler_args`, `run_matchers`, `assemble_bot` |
| 私有成员 | 单下划线前缀 `_` | `_run_preprocessors`, `_plugin_tools` |
| 常量 | 全大写 | `_MAX_PENDING_EVENTS`, `DEFAULT_SELF_ID` |
| 测试文件 | `test_<被测模块>.py` | `test_plugin_manager.py` |
| 测试函数 | `test_<行为描述>` | `test_event_bus_cross_plugin_broadcast` |
| 插件名 | 小写单词 | `chat`, `imagegen` |
| 插件模板 | `_` 前缀（不参与加载） | `plugins/_template/` |

## 5. 插件开发约定

- 插件入口：模块级 `@on_command(...)` / `@llm_tool(...)` 装饰器，或 `PluginBase` 子类覆写生命周期。
- 依赖注入：handler 参数按签名自动注入（`MatcherContext`、`Bot`、DI 服务），必要时用 `Depends(...)`。
- 跨插件协作：优先用 `require`/`export`（服务式）或 `EventBus` 事件广播（解耦式），避免硬 import。
- 配置：用 `Config` 内嵌类（pydantic），框架自动生成 Web 表单与 JSON Schema。
- 国际化：文案走 `self._(...)`，资源放 `i18n/<locale>.json`。
- 插件级指标：框架自动记录 Matcher 耗时/错误，无需手动埋点。
- 旧式回调 `on_message`/`on_notice`/`on_request` 已弃用，新代码一律使用 Matcher。

## 6. 前端约定（`web/`）

- Vue 3 组合式 API（`<script setup>`）+ Pinia + Vue Router。
- 遵守 ESLint + Prettier 规则（`npm run lint` / `npm run format`）。
- 目录：`views/`（页面）、`components/`（通用组件）、`stores/`（状态）、`router/`（路由）、`composables/`（复用逻辑）、`styles/`（全局样式）。
- 组件命名：PascalCase 文件或语义化 kebab-case，与目录职责一致。

## 7. Git 约定

- 提交信息使用 [Conventional Commits](https://www.conventionalcommits.org/)：
  `feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:` / `perf:` / `build:`。
- 分支命名：`feat/xxx`、`fix/xxx`。
- 大改动同步更新 `CHANGELOG.md`（`[Unreleased]` 段）。
- 协议层改动在 `Plugins-SDK` 仓库提交后，主项目通过 git 依赖版本锁定同步，两个仓库的 CHANGELOG 均需记录。
- 不提交产物与缓存（见 `docs/PROJECT_STRUCTURE.md` 产物归属约定）。
