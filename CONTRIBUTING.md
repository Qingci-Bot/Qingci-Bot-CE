# Contributing to Qingci-Bot-CE

感谢你对 Qingci-Bot-CE 的关注！欢迎任何形式的贡献。

## 行为准则

请保持友善和尊重，遵循专业沟通标准。

## 如何贡献

### 报告 Bug

1. 在 [Issues](https://github.com/Qingci-Bot/Qingci-Bot-CE/issues) 中搜索是否已有相同问题
2. 如果没有，创建新 Issue，包含：
   - 清晰的问题描述
   - 复现步骤
   - 期望行为 vs 实际行为
   - 环境信息（Python 版本、操作系统、配置等）
   - 相关日志截图

### 功能建议

1. 在 Issues 中先讨论，确认功能方向
2. 描述清楚使用场景和期望效果

### 提交代码

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feat/your-feature`
3. 遵循代码规范：
   - Python: 遵循 `ruff` 规则（配置见 `pyproject.toml`）
   - 前端: 遵循 ESLint + Prettier 规则
   - 提交信息: 使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式
   - **异常处理**：降级路径允许 `except Exception`，但必须留痕（debug 日志或明确回退分支），
     禁止裸 `except: pass`。完整约定见 `docs/CODING_STANDARDS.md` 第 2 节
4. 确保测试通过：`pytest`
5. 提交 Pull Request

### 版本号管理

升级版本号时**不要手动逐文件修改**，使用统一脚本一次同步全部版本字段：
`pyproject.toml`（打包/CI 读取）、`bot/__init__.py`（`__version__`，后端 `/api/bot/status` 及 Web 关于页据此动态显示）、`web/package.json` 与 `web/package-lock.json`（含 `packages` 两处）、`desktop/electron/package.json` 与 `desktop/electron/package-lock.json`（含 `packages` 两处，安装包版本号来源）。

```bash
python scripts/bump_version.py 1.16.0     # 从单一输入同步升级全部版本字段
python scripts/bump_version.py --check    # 提交前自查各处是否一致（防止漏改）
```

任一文件缺失版本字段、版本格式非法时脚本会明确报错，绝不静默漏改。升级版本后请同步更新 `CHANGELOG.md`（Keep a Changelog 风格）。

> **`uv.lock` 生成约束**：若本机配置了 pip 镜像（`pip.conf` / `~/.config/uv/uv.toml`），
> 直接 `uv lock` 会把镜像地址写进锁文件，CI（官方源）随即判定锁文件过期而失败。
> 请始终用 `uv lock --default-index https://pypi.org/simple` 生成；CI 有一道检查会拦截
> 含非官方索引的锁文件（允许 `pypi.org` 与私有索引 `pypi.luoqingciya.cn`）。
>
> 注意 `uv run` / `uv sync` 也会隐式重解析：当本机默认索引与锁文件里记录的来源不一致时，
> 它们会按本机镜像重写锁文件的 `source` 与 wheel URL（版本与 hash 不变，只是 URL 全被换掉，
> 表现为上千行 diff）。**每次 `uv run` 后提交前请 `git checkout -- uv.lock`**，
> 只有确实要改依赖时才用上面的 `--default-index` 重新生成。

## 开发环境

### 后端

```bash
# 克隆仓库
git clone https://github.com/Qingci-Bot/Qingci-Bot-CE.git
cd Qingci-Bot-CE

# 创建虚拟环境
uv venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/macOS

# 安装开发依赖（含插件协议层 SDK，经私有索引自动安装）
# 推荐用 uv sync：严格按 uv.lock 安装，与 CI 解析结果完全一致
uv sync --locked --extra dev

# 本地开发 Plugins-SDK 时，用 -e 安装覆盖索引版本（与 build.bat 一致）
uv pip install -e ..\Plugins-SDK

# 运行测试
uv run pytest

# 代码检查
uv run ruff check .
uv run ruff format --check .

# 类型检查
uv run mypy api bot desktop plugins
```

> **协议层说明**：`bot/plugin/protocol/{base,context,matcher,permission,rule,ratelimit,session,events}.py` 为 `qingci_plugin_sdk` 薄转发（`bot/plugin/` 顶层同名模块为兼容再导出）。修改权限语义、匹配规则、基类等协议行为时，请前往 [Plugins-SDK](https://github.com/Qingci-Bot/Plugins-SDK) 仓库修改、发版并上传私有索引，再在主项目提升版本号；主项目内只改运行时逻辑（如 `bot/plugin/manager.py`、`bot/plugin/llm_tool.py`）。

### 前端

```bash
cd web
npm install
npm run dev       # 开发服务器
npm test          # 单元测试（vitest，覆盖请求层/store/composable）
npm run lint      # 代码检查
npm run format    # 代码格式化
```

### 构建 EXE / AppImage / Docker

桌面分发为两步打包：先出 Python 后端 onedir，再用 electron-builder 出桌面壳 EXE（Windows 安装版 + 绿色解压 zip）或 AppImage（Linux）。

```bash
# 第一步：Python 后端 onedir（含 Web UI 构建、SDK -e 安装、Playwright 浏览器下载）
uv pip install -e ".[build]"
.\build.bat       # Windows
./build-linux.sh  # Linux（等价脚本；国内跑 SDK 走 Gitee 默认源）

# 第二步：Electron 桌面壳（需 Node.js 22+/npm；Electron 44 要求 Node ≥22.12；内嵌上一步产出的后端）
.\build-electron.bat                          # Windows 安装版 Setup.exe + 绿色解压 zip
cd desktop/electron && npm run build:linux     # Linux AppImage
```

**发布流水线（推荐，自动）**：打 `vX.Y.Z` tag 即自动构建并上传分发包到同名 Release、推 Docker 镜像到 `ghcr.io`，无需手动上传/构建，见 [`.github/workflows/release.yml`](.github/workflows/release.yml)。

> 构建详见 [PLUGIN_DEV.md](PLUGIN_DEV.md)「打包为 exe」与 `build.bat` / `build-linux.sh` / `build-electron.bat`。onedir（`dist\qingci-bot-ce\`）只含后端不含桌面壳；桌面壳由 electron-builder 承担，Windows 安装版/绿色解压版与 Linux AppImage 的实例与数据均随可执行目录 `instances\` 自包含分发。直接执行 `pyinstaller qingci-bot-ce.spec` 也能出 onedir 后端，但缺少 Web UI 产物复制与浏览器下载步骤。

## 项目结构

```
Qingci-Bot-CE/
├── main.py           # 后端入口
├── api/              # FastAPI 接口层
│   ├── server.py     # 应用装配与路由挂载
│   ├── auth.py       # 鉴权 / 审计横切逻辑
│   └── routes/       # REST 路由（auth/bot/config/group/log/plugin/...）
├── bot/              # Bot 核心逻辑
│   ├── core/         # 生命周期、连接、调度、分发、DI、组合根装配、事件总线、会话状态
│   │   ├── composition.py  # 组合根（assemble_bot 组件装配）
│   │   └── bot.py          # Bot 主类（get_bot 经 DI 解析）
│   ├── plugin/       # 插件系统（协议层薄转发 SDK，含 builtin/ 内置插件）
│   ├── llm/          # LLM 管理、适配器、工具调用（Function Calling / MCP）
│   ├── db/           # 数据库 ORM、仓储与迁移
│   ├── rag/          # 知识库检索（lancedb 可选依赖）
│   ├── testing/      # TestBot 测试沙箱
│   ├── config.py     # 配置管理
│   ├── i18n.py       # 国际化翻译器
│   ├── instances.py  # 实例管理（instances/<name>/ 自包含目录）
│   └── paths.py      # 路径解析（app_root / data_root / plugins_dir）
├── web/              # Vue 3 前端（src/ 下 views/stores/router/composables/styles）
├── desktop/          # 桌面应用（窗口、托盘、启动页、single_instance 单实例保护、relaunch 跨进程重启）
├── instances/        # 实例注册表（运行时生成；每个实例一个自包含目录，无全局模式）
├── plugins/          # 外部插件目录（_template 为插件模板，hello 为示例）
├── migrations/       # Alembic 数据库迁移
├── tests/            # pytest 测试（plugin_pkg/ 为测试插件）
├── scripts/          # 工具脚本（如 bump_version 版本升级、SQLite→PostgreSQL 迁移）
└── docs/             # 规范文档（结构 / 编码约定）
```

### 产物归属约定

本仓库是 `Qingci-Bot` 根目录下的子项目之一。**所有运行产物（缓存、构建产物、egg-info 等）必须产生在本目录内**，不得写入外层根目录，避免污染其他子项目。缓存目录已在 `.gitignore` 中忽略，无需提交。

## 插件开发

请参考 [Plugins-SDK](https://github.com/Qingci-Bot/Plugins-SDK) 独立开发仓库。

## 许可证

本项目采用 [GPL-3.0-or-later](LICENSE) 许可证。提交代码即表示你同意在此许可证下发布你的贡献。