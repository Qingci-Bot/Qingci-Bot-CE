# 生产环境部署

> 面向生产运维：向量检索初始化、SQLite → PostgreSQL 迁移、协议端断连恢复。
> 架构分层见 [ARCHITECTURE.md](../ARCHITECTURE.md)，日常安装与启动见 [SETUP.md](./SETUP.md)，配置项见 [CONFIGURATION.md](./CONFIGURATION.md)。


## 向量检索（RAG）初始化

向量检索模式基于 LanceDB（嵌入式向量数据库）+ litellm embedding，无需额外服务端部署，但需要完成以下初始化步骤：

> **依赖**：向量模式需要 `lancedb`（可选依赖，`pip install -e ".[vector]"` 或 `uv pip install lancedb`）。未安装时 `mode: vector` 自动回退为关键词检索并输出警告，Bot 不会崩溃。

### 1. 启用向量检索模式

在 `config.yaml` 中配置 `rag` 节：

```yaml
rag:
  enabled: true
  mode: vector                        # 从 keyword 切换到 vector
  embedding_model: "text-embedding-3-small"  # OpenAI 兼容的 embedding 模型
  embedding_api_key: ""               # 留空则复用 llm.api_key
  embedding_api_url: ""               # 留空则复用 llm.api_url
  knowledge_dir: "data/knowledge"     # 知识库文档目录
  top_k: 3                            # 检索返回的最相关条目数
  chunk_size: 400                     # 文档分块大小（字符）
  chunk_overlap: 50                   # 相邻分块重叠字符数
  collection_name: "qingci_knowledge" # LanceDB 集合名
```

> **embedding 模型选择**：使用 OpenAI 兼容的 embedding API（如 `text-embedding-3-small`、`bge-large-zh` 等）。Ollama 本地模型需在 `embedding_api_url` 中指定地址（如 `http://localhost:11434/v1`）。

### 2. 准备知识库文档

将 `.txt` 或 `.md` 文件放入 `data/knowledge/` 目录。支持中文、英文混合文档，文件命名建议使用中文标题（如 `产品手册.txt`）。

### 3. 生成 Embedding 并建立索引

索引生成是**全自动**的，无需手动执行脚本：

- **启动时自动索引**：Bot 启动时若 `mode: vector`，自动扫描 `knowledge_dir` 下所有文档，调用 embedding API 生成向量并写入 LanceDB

- **文档变更自动重建**：通过 `/kb` 命令添加/删除文档时，自动触发全量索引重建

- **手动触发重建**：`/kb reload` 重新索引知识库目录（知识库无独立 Web 管理页，统一经 `/kb` 命令管理）

**首次索引耗时估算**：取决于文档总量和 embedding API 响应速度。100 篇中等长度文档约需 30-60 秒，期间 Bot 其他功能不受影响。

### 4. 索引存储位置

LanceDB 数据存储在 `data/knowledge/.lancedb/` 目录下，无需额外维护。如需迁移或备份，直接复制整个 `.lancedb/` 目录即可。

### 5. 验证索引状态

通过 `/kb list` 命令可查看：知识库文档列表与分块状态（`/kb reload` 可手动重建索引）。

### 注意事项

- **embedding API 费用**：每次全量索引重建会调用 embedding API，文档量大时请注意 API 调用成本。日常增量添加文档也会触发全量重建（当前版本未实现增量索引）

- **embedding 模型一致性**：索引重建时使用的 embedding 模型必须与检索时一致，否则向量维度不匹配会导致检索失败

- **降级到 keyword 模式**：若 embedding API 不可用，可将 `mode` 改回 `keyword`，立即回退到关键词检索模式，无需重建索引；若 `lancedb` 未安装，程序自动回退到 keyword 并告警

## 数据库迁移至 PostgreSQL

默认使用 SQLite + WAL 模式，适合小规模部署（<10 个活跃群）。当面对数百个高频群聊时，SQLite 的串行写锁可能成为性能瓶颈。

**方式一：使用迁移脚本（推荐）**

项目提供了 `scripts/migrate_sqlite_to_pg.py` 工具脚本，可一键将 SQLite 数据迁移至 PostgreSQL：

```bash
# 1. 安装 PostgreSQL 驱动
uv pip install asyncpg --python .venv\Scripts\python.exe

# 2. 创建目标数据库（在 psql 中执行）
# CREATE DATABASE qingci_bot;

# 3. 运行迁移脚本
python scripts/migrate_sqlite_to_pg.py \
  --pg-url "postgresql+asyncpg://user:password@localhost:5432/qingci_bot"
```

脚本会自动：

- 读取 SQLite 中所有表数据

- 在 PostgreSQL 中创建表结构

- 逐表迁移数据（messages / sessions / plugin\_configs / group\_configs / usage\_logs / audit\_logs）

- 输出迁移进度与统计

**方式二：手动迁移**

1. 安装驱动：

```bash
uv pip install asyncpg --python .venv\Scripts\python.exe
```

1. 修改 `bot/db/engine.py` 中的连接串：

```python
# 替换 SQLite 连接串为 PostgreSQL
DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/qingci_bot"
engine = create_async_engine(DATABASE_URL, echo=False)
```

1. 运行 Alembic 迁移重建表结构：

```bash
alembic upgrade head
```

1. 移除 SQLite 专用 PRAGMA 设置（`_set_sqlite_pragma` 函数与 `event.listen` 调用）。

> 注意：PostgreSQL 的 `expire_on_commit=False` 配置与 SQLite 一致，无需改动。

## 协议端断连恢复

Qingci-Bot CE 在 OneBot 11 / OneBot 12 两种接入方式下都是**反向 WebSocket 的服务端**：连接由协议端发起，断连后的重连也由协议端驱动，本项目不主动发起重连，只负责监测状态与恢复收发：

- **断连检测**：WebSocket 断开即触发 `on_disconnect` 回调，`/api/bot/status` 与 Web UI 侧边栏同步显示「未连接」

- **优雅降级**：断连期间 Web UI 与 API 服务正常可用，已加载插件与配置保持不变

- **重连恢复**：协议端重新连入时触发 `on_reconnect` / `on_connect`（插件级 `on_bot_connect`），自动恢复消息收发

- **主动重连（仅 Telegram）**：Telegram 是长轮询客户端角色，失败时由本项目按指数退避重试（起始 1.5s，上限 60s，连续失败累加、成功即复位）

断连监控与重连回调内置于 `bot/core/platforms/onebot11.py` / `onebot12.py` / `telegram.py`，无需额外配置。
