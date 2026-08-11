# Contributing to Qingci-Bot

感谢你对 Qingci-Bot 的关注！欢迎任何形式的贡献。

## 行为准则

请保持友善和尊重，遵循专业沟通标准。

## 如何贡献

### 报告 Bug

1. 在 [Issues](https://atomgit.com/Qingci-Bot/Qingci-Bot-CE/issues) 中搜索是否已有相同问题
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
4. 确保测试通过：`pytest`
5. 提交 Pull Request

## 开发环境

### 后端

```bash
# 克隆仓库
git clone https://atomgit.com/Qingci-Bot/Qingci-Bot-CE.git
cd Qingci-Bot-CE

# 创建虚拟环境
uv venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/macOS

# 安装开发依赖
uv pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check .
ruff format --check .

# 类型检查
mypy bot api
```

### 前端

```bash
cd web
npm install
npm run dev       # 开发服务器
npm run lint      # 代码检查
npm run format    # 代码格式化
```

### 构建 EXE

```bash
uv pip install -e ".[build]"
pyinstaller qingci-bot.spec
```

## 项目结构

```
Qingci-Bot-CE/
├── api/            # FastAPI 接口层
├── bot/            # Bot 核心逻辑
│   ├── core/       # 生命周期、连接、调度
│   ├── llm/        # LLM 管理、适配器、工具调用
│   ├── plugin/     # 插件系统
│   ├── db/         # 数据库 ORM 与仓储
│   ├── rag/        # 知识库检索
│   └── config.py   # 配置管理
├── web/            # Vue 3 前端
├── desktop/        # 桌面应用
├── plugins/        # 内置插件
├── tests/          # 测试
└── scripts/        # 工具脚本
```

## 插件开发

请参考 [Plugins-Dev](https://atomgit.com/luoqingci/Plugins-Dev) 独立开发仓库。

## 许可证

本项目采用 [GPL-3.0-or-later](LICENSE) 许可证。提交代码即表示你同意在此许可证下发布你的贡献。