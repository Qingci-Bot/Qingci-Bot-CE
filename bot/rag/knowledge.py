"""知识库 - 支持关键词检索与 ChromaDB 向量检索

设计要点：
- keyword 模式：纯 Python 关键词匹配打分，无外部依赖，适合小规模文档
- vector 模式：ChromaDB 持久化向量存储 + litellm embedding，语义匹配更精准
- 两种模式共享同一套文档管理 API（add/remove/list/reload）
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("qingci-bot.rag")

# ASCII 词（小写英数）与中文连续片段
_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

# 文档名安全校验：中文/字母/数字/下划线/短横线，最长 32 字符
_NAME_RE = re.compile(r"[\w\u4e00-\u9fff-]{1,32}")


def tokenize(text: str) -> list[str]:
    """轻量分词：ASCII 词原样保留，中文按二元组切分"""
    tokens = _WORD_RE.findall(text.lower())
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


@dataclass
class KnowledgeChunk:
    """知识库分块"""

    source: str  # 来源文档名（不含扩展名）
    text: str  # 分块原文
    tf: Counter = field(default_factory=Counter)  # 词频（keyword 模式检索打分用）


class KeywordKnowledgeStore:
    """基于关键词匹配的轻量知识库（纯 Python，无外部依赖）"""

    SUPPORTED_SUFFIXES = {".txt", ".md"}

    def __init__(
        self,
        root: Path,
        chunk_size: int = 400,
        chunk_overlap: int = 50,
        top_k: int = 3,
    ):
        self._root = Path(root)
        self._chunk_size = max(50, chunk_size)
        self._chunk_overlap = max(0, min(chunk_overlap, self._chunk_size // 2))
        self._top_k = max(1, top_k)
        self._chunks: list[KnowledgeChunk] = []
        self.reload()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    # ============ 索引 ============

    def _index_file(self, path: Path) -> list[KnowledgeChunk]:
        """索引单个文档文件，返回其分块列表（供全量/增量索引复用）"""
        chunks: list[KnowledgeChunk] = []
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            logger.exception(f"读取知识文件失败: {path.name}")
            return chunks
        source = path.stem
        for piece in self._chunk_text(text):
            chunks.append(
                KnowledgeChunk(
                    source=source,
                    text=piece,
                    tf=Counter(tokenize(piece)),
                )
            )
        return chunks

    def reload(self) -> int:
        """（重新）索引知识库目录下所有文档，返回分块总数"""
        chunks: list[KnowledgeChunk] = []
        if self._root.exists():
            for path in sorted(self._root.iterdir()):
                if path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                    continue
                if not path.is_file():
                    continue
                chunks.extend(self._index_file(path))
        self._chunks = chunks
        logger.info(
            f"知识库索引完成: 目录={self._root}, "
            f"文档 {len(self.list_documents())} 篇, 分块 {len(chunks)} 个"
        )
        return len(chunks)

    def _chunk_text(self, text: str) -> list[str]:
        """按固定窗口切分文本，相邻分块保留重叠"""
        text = text.strip()
        if not text:
            return []
        step = self._chunk_size - self._chunk_overlap
        pieces: list[str] = []
        for start in range(0, len(text), step):
            piece = text[start : start + self._chunk_size].strip()
            if piece:
                pieces.append(piece)
            if start + self._chunk_size >= len(text):
                break
        return pieces

    # ============ 检索 ============

    def search(self, query: str, top_k: int | None = None) -> list[KnowledgeChunk]:
        """关键词检索：返回得分最高的 top_k 个分块（无命中返回空列表）"""
        q_tokens = tokenize(query)
        if not q_tokens or not self._chunks:
            return []
        scored: list[tuple[int, KnowledgeChunk]] = []
        for chunk in self._chunks:
            score = sum(chunk.tf.get(t, 0) for t in q_tokens)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        limit = top_k or self._top_k
        return [chunk for _, chunk in scored[:limit]]

    def build_reference(self, query: str, max_chars: int) -> str:
        """构造注入 system_prompt 的参考资料文本（无命中返回 ""）"""
        hits = self.search(query)
        if not hits or max_chars <= 0:
            return ""
        parts: list[str] = []
        total = 0
        for chunk in hits:
            header = f"[来源《{chunk.source}》]"
            need = len(header) + len(chunk.text) + 1
            if total + need > max_chars:
                remaining = max_chars - total - len(header) - 4
                if remaining >= 40:
                    parts.append(header + chunk.text[:remaining] + "...")
                break
            parts.append(header + chunk.text)
            total += need
        return "\n".join(parts)

    # ============ 文档管理 ============

    def add_document(self, name: str, content: str) -> Path:
        """新增文档（写入知识库目录后增量索引，不重建其余文档）

        Raises:
            ValueError: 文档名非法或已存在、内容为空
        """
        safe = self._safe_name(name)
        if not safe:
            raise ValueError("文档名仅支持中文/字母/数字/下划线/短横线，长度 1-32")
        if not content.strip():
            raise ValueError("文档内容不能为空")
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{safe}.txt"
        if path.exists():
            raise ValueError(f"文档已存在: {safe}")
        path.write_text(content.strip(), encoding="utf-8")
        # 增量索引：仅追加新文档的分块，避免全量重建已索引文档
        self._chunks.extend(self._index_file(path))
        return path

    def remove_document(self, name: str) -> bool:
        """删除文档并增量更新索引，返回是否删除成功

        仅移除被删文档的分块，不重建其余文档索引。
        """
        safe = self._safe_name(name)
        if not safe:
            logger.warning(f"拒绝非法文档名删除请求: {name!r}")
            return False
        root = self._root.resolve()
        for suffix in self.SUPPORTED_SUFFIXES:
            path = self._root / f"{safe}{suffix}"
            try:
                if not path.resolve().is_relative_to(root):
                    logger.warning(f"路径穿越拦截: {path}")
                    continue
            except OSError:
                continue
            if path.is_file():
                path.unlink()
                # 增量索引：仅剔除该文档的分块
                self._chunks = [c for c in self._chunks if c.source != path.stem]
                return True
        return False

    def list_documents(self) -> list[dict]:
        """列出知识库文档（含无有效分块的空文档），按名称排序"""
        docs: dict[str, int] = {}
        for chunk in self._chunks:
            docs[chunk.source] = docs.get(chunk.source, 0) + 1
        if self._root.exists():
            for path in self._root.iterdir():
                if path.suffix.lower() in self.SUPPORTED_SUFFIXES and path.is_file():
                    docs.setdefault(path.stem, 0)
        return [{"name": name, "chunks": count} for name, count in sorted(docs.items())]

    @staticmethod
    def _safe_name(name: str) -> str:
        """文档名安全校验（防路径穿越），合法返回原名，否则返回空串"""
        match = _NAME_RE.fullmatch(name.strip())
        return match.group(0) if match else ""


class VectorKnowledgeStore:
    """基于 LanceDB 的向量知识库（语义检索）

    使用 litellm embedding 将文档分块和查询转为向量，
    LanceDB 持久化存储（嵌入式，无服务端），支持语义相似度检索。
    """

    SUPPORTED_SUFFIXES = {".txt", ".md"}

    def __init__(
        self,
        root: Path,
        chunk_size: int = 400,
        chunk_overlap: int = 50,
        top_k: int = 3,
        embedding_model: str = "text-embedding-3-small",
        embedding_api_url: str = "",
        embedding_api_key: str = "",
        collection_name: str = "qingci_knowledge",
    ):
        self._root = Path(root)
        self._chunk_size = max(50, chunk_size)
        self._chunk_overlap = max(0, min(chunk_overlap, self._chunk_size // 2))
        self._top_k = max(1, top_k)
        self._embedding_model = embedding_model
        self._embedding_api_url = embedding_api_url
        self._embedding_api_key = embedding_api_key
        self._collection_name = collection_name
        self._db_path = self._root / ".lancedb"
        self._db = None
        self._table = None

    @property
    def root(self) -> Path:
        return self._root

    @property
    def chunk_count(self) -> int:
        try:
            tbl = self._get_table()
            return tbl.count_rows() if tbl else 0
        except Exception:
            return 0

    # ============ LanceDB 初始化 ============

    def _get_db(self):
        """获取 LanceDB 连接（懒加载）"""
        if self._db is None:
            import lancedb

            self._db_path.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self._db_path))
        return self._db

    def _get_table(self):
        """获取或创建表"""
        if self._table is None:
            db = self._get_db()
            table_name = self._collection_name
            try:
                self._table = db.open_table(table_name)
            except Exception:
                # 表不存在，创建空表
                import pyarrow as pa

                schema = pa.schema(
                    [
                        pa.field("id", pa.string()),
                        pa.field("vector", pa.list_(pa.float32(), list_size=-1)),
                        pa.field("text", pa.string()),
                        pa.field("source", pa.string()),
                    ]
                )
                self._table = db.create_table(table_name, schema=schema)
        return self._table

    # ============ Embedding ============

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """使用 litellm 将文本列表转为向量"""
        from ..llm.litellm_adapter import _get_litellm

        litellm = _get_litellm()

        kwargs: dict = {"model": self._embedding_model, "input": texts}
        if self._embedding_api_key:
            kwargs["api_key"] = self._embedding_api_key
        if self._embedding_api_url:
            kwargs["api_base"] = self._embedding_api_url

        response = await litellm.aembedding(**kwargs)
        return [d["embedding"] for d in (response.data or [])]

    # ============ 索引 ============

    async def reload(self) -> int:
        """（重新）索引知识库目录下所有文档，返回分块总数"""
        import uuid

        import pyarrow as pa

        chunks: list[dict] = []  # [{id, text, source}, ...]
        if self._root.exists():
            for path in sorted(self._root.iterdir()):
                if path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                    continue
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    logger.exception(f"读取知识文件失败: {path.name}")
                    continue
                for piece in self._chunk_text(text):
                    chunks.append(
                        {
                            "id": str(uuid.uuid4()),
                            "text": piece,
                            "source": path.stem,
                        }
                    )

        if not chunks:
            # 重建空表
            db = self._get_db()
            table_name = self._collection_name
            try:
                db.drop_table(table_name)
            except Exception:
                pass
            self._table = None
            self._get_table()
            logger.info(f"向量知识库索引完成: 目录={self._root}, 文档 0 篇, 分块 0 个")
            return 0

        # 批量 embedding
        texts = [c["text"] for c in chunks]
        try:
            embeddings = await self._embed(texts)
        except Exception as e:
            logger.error(f"向量知识库 embedding 失败: {e}，请检查 embedding_model 与 API 配置")
            return 0

        # 构建 Arrow 表并写入
        ids = [c["id"] for c in chunks]
        sources = [c["source"] for c in chunks]
        table = pa.table(
            {
                "id": pa.array(ids, type=pa.string()),
                "vector": pa.array(embeddings, type=pa.list_(pa.float32())),
                "text": pa.array(texts, type=pa.string()),
                "source": pa.array(sources, type=pa.string()),
            }
        )

        db = self._get_db()
        table_name = self._collection_name
        # 原子替换：先写入临时表，成功后再替换旧表——
        # 避免建表中途失败（磁盘满等）导致旧索引被删、数据丢失
        tmp_name = f"{table_name}__new"
        try:
            db.drop_table(tmp_name)
        except Exception:
            pass
        db.create_table(tmp_name, table)
        try:
            db.drop_table(table_name)
        except Exception:
            pass
        try:
            db.rename_table(tmp_name, table_name)
        except Exception:
            logger.warning(
                f"知识库表替换失败（临时表 {tmp_name} 保留，未覆盖旧数据）: table={table_name}"
            )
            self._table = db.open_table(tmp_name)
            raise
        self._table = db.open_table(table_name)

        doc_count = len(set(sources))
        logger.info(
            f"向量知识库索引完成: 目录={self._root}, 文档 {doc_count} 篇, 分块 {len(chunks)} 个"
        )
        return len(chunks)

    def reload_sync(self) -> int:
        """同步版 reload（供初始化时使用）"""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.reload())
        import concurrent.futures

        future = asyncio.run_coroutine_threadsafe(self.reload(), loop)
        try:
            return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            logger.error("向量知识库索引超时（60s）")
            return 0

    def _chunk_text(self, text: str) -> list[str]:
        """按固定窗口切分文本，相邻分块保留重叠"""
        text = text.strip()
        if not text:
            return []
        step = self._chunk_size - self._chunk_overlap
        pieces: list[str] = []
        for start in range(0, len(text), step):
            piece = text[start : start + self._chunk_size].strip()
            if piece:
                pieces.append(piece)
            if start + self._chunk_size >= len(text):
                break
        return pieces

    # ============ 检索 ============

    def search(self, query: str, top_k: int | None = None) -> list[KnowledgeChunk]:
        """向量检索（同步包装，供 build_reference 调用）"""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._search_async(query, top_k))
        import concurrent.futures

        future = asyncio.run_coroutine_threadsafe(self._search_async(query, top_k), loop)
        try:
            return future.result(timeout=30)
        except concurrent.futures.TimeoutError:
            logger.error("向量检索超时（30s）")
            return []

    async def _search_async(self, query: str, top_k: int | None = None) -> list[KnowledgeChunk]:
        """异步向量检索"""
        limit = top_k or self._top_k
        tbl = self._get_table()
        if tbl.count_rows() == 0:
            return []

        try:
            query_embedding = await self._embed([query])
        except Exception as e:
            logger.error(f"查询 embedding 失败: {e}")
            return []

        try:
            results = tbl.search(query_embedding[0]).limit(limit).to_list()
        except Exception as e:
            logger.error(f"LanceDB 检索失败: {e}")
            return []

        chunks: list[KnowledgeChunk] = []
        for row in results:
            chunks.append(
                KnowledgeChunk(
                    source=row.get("source", "unknown"),
                    text=row.get("text", ""),
                )
            )
        return chunks

    def build_reference(self, query: str, max_chars: int) -> str:
        """构造注入 system_prompt 的参考资料文本（无命中返回 ""）"""
        hits = self.search(query)
        if not hits or max_chars <= 0:
            return ""
        parts: list[str] = []
        total = 0
        for chunk in hits:
            header = f"[来源《{chunk.source}》]"
            need = len(header) + len(chunk.text) + 1
            if total + need > max_chars:
                remaining = max_chars - total - len(header) - 4
                if remaining >= 40:
                    parts.append(header + chunk.text[:remaining] + "...")
                break
            parts.append(header + chunk.text)
            total += need
        return "\n".join(parts)

    # ============ 文档管理 ============

    def add_document(self, name: str, content: str) -> Path:
        """新增文档（写入知识库目录后重建索引）"""
        safe = self._safe_name(name)
        if not safe:
            raise ValueError("文档名仅支持中文/字母/数字/下划线/短横线，长度 1-32")
        if not content.strip():
            raise ValueError("文档内容不能为空")
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{safe}.txt"
        if path.exists():
            raise ValueError(f"文档已存在: {safe}")
        path.write_text(content.strip(), encoding="utf-8")
        self.reload_sync()
        return path

    async def add_document_async(self, name: str, content: str) -> Path:
        """新增文档（异步版：事件循环内直接 await，避免 reload_sync 死锁）"""
        safe = self._safe_name(name)
        if not safe:
            raise ValueError("文档名仅支持中文/字母/数字/下划线/短横线，长度 1-32")
        if not content.strip():
            raise ValueError("文档内容不能为空")
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{safe}.txt"
        if path.exists():
            raise ValueError(f"文档已存在: {safe}")
        path.write_text(content.strip(), encoding="utf-8")
        await self.reload()
        return path

    def remove_document(self, name: str) -> bool:
        """删除文档并重建索引，返回是否删除成功"""
        safe = self._safe_name(name)
        if not safe:
            logger.warning(f"拒绝非法文档名删除请求: {name!r}")
            return False
        root = self._root.resolve()
        for suffix in self.SUPPORTED_SUFFIXES:
            path = self._root / f"{safe}{suffix}"
            try:
                if not path.resolve().is_relative_to(root):
                    logger.warning(f"路径穿越拦截: {path}")
                    continue
            except OSError:
                continue
            if path.is_file():
                path.unlink()
                self.reload_sync()
                return True
        return False

    async def remove_document_async(self, name: str) -> bool:
        """删除文档并重建索引（异步版：事件循环内直接 await）"""
        safe = self._safe_name(name)
        if not safe:
            logger.warning(f"拒绝非法文档名删除请求: {name!r}")
            return False
        root = self._root.resolve()
        for suffix in self.SUPPORTED_SUFFIXES:
            path = self._root / f"{safe}{suffix}"
            try:
                if not path.resolve().is_relative_to(root):
                    logger.warning(f"路径穿越拦截: {path}")
                    continue
            except OSError:
                continue
            if path.is_file():
                path.unlink()
                await self.reload()
                return True
        return False

    def list_documents(self) -> list[dict]:
        """列出知识库文档，按名称排序"""
        docs: dict[str, int] = {}
        tbl = self._get_table()
        if tbl is not None:
            try:
                rows = tbl.to_pandas()
                for source in rows["source"].unique():
                    docs[source] = int((rows["source"] == source).sum())
            except Exception:
                pass
        # 也列出目录中的文件（含空文档）
        if self._root.exists():
            for path in self._root.iterdir():
                if path.suffix.lower() in self.SUPPORTED_SUFFIXES and path.is_file():
                    docs.setdefault(path.stem, 0)
        return [{"name": name, "chunks": count} for name, count in sorted(docs.items())]

    @staticmethod
    def _safe_name(name: str) -> str:
        match = _NAME_RE.fullmatch(name.strip())
        return match.group(0) if match else ""


class KnowledgeStore:
    """知识库统一门面：根据 mode 选择 keyword 或 vector 后端

    对外 API 与旧版 KnowledgeStore 完全兼容，调用方无需感知后端差异。
    """

    def __init__(
        self,
        root: Path,
        mode: str = "keyword",
        chunk_size: int = 400,
        chunk_overlap: int = 50,
        top_k: int = 3,
        embedding_model: str = "",
        embedding_api_url: str = "",
        embedding_api_key: str = "",
        collection_name: str = "qingci_knowledge",
    ):
        self._mode = mode
        self._backend: KeywordKnowledgeStore | VectorKnowledgeStore
        if mode == "vector":
            # lancedb 为可选依赖（见 pyproject 的 vector 分组）；未安装时
            # 回退到 keyword 后端，避免向量检索直接崩溃。
            try:
                import lancedb  # noqa: F401

                self._backend = VectorKnowledgeStore(
                    root=root,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    top_k=top_k,
                    embedding_model=embedding_model or "text-embedding-3-small",
                    embedding_api_url=embedding_api_url,
                    embedding_api_key=embedding_api_key,
                    collection_name=collection_name,
                )
            except ModuleNotFoundError:
                logger.warning(
                    "lancedb 未安装，向量检索不可用，已回退到 keyword 模式。"
                    "如需语义检索请安装：uv pip install 'qingci-bot-ce[vector]'"
                )
                self._mode = "keyword"
                self._backend = KeywordKnowledgeStore(
                    root=root,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    top_k=top_k,
                )
        else:
            self._backend = KeywordKnowledgeStore(
                root=root,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                top_k=top_k,
            )

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def root(self) -> Path:
        return self._backend.root

    @property
    def chunk_count(self) -> int:
        return self._backend.chunk_count

    def reload(self) -> int:
        backend = self._backend
        if isinstance(backend, VectorKnowledgeStore):
            return backend.reload_sync()
        return backend.reload()

    async def reload_async(self) -> int:
        backend = self._backend
        if isinstance(backend, VectorKnowledgeStore):
            return await backend.reload()
        return backend.reload()

    def search(self, query: str, top_k: int | None = None) -> list[KnowledgeChunk]:
        return self._backend.search(query, top_k)

    async def search_async(self, query: str, top_k: int | None = None) -> list[KnowledgeChunk]:
        """异步检索（vector 后端在事件循环内直接 await，避免同步包装死锁）"""
        backend = self._backend
        if isinstance(backend, VectorKnowledgeStore):
            return await backend._search_async(query, top_k)
        return backend.search(query, top_k)

    def build_reference(self, query: str, max_chars: int) -> str:
        return self._backend.build_reference(query, max_chars)

    async def build_reference_async(self, query: str, max_chars: int) -> str:
        """异步构造参考资料（vector 后端使用，避免事件循环内死锁）"""
        hits = await self.search_async(query)
        if not hits or max_chars <= 0:
            return ""
        parts: list[str] = []
        total = 0
        for chunk in hits:
            header = f"[来源《{chunk.source}》]"
            need = len(header) + len(chunk.text) + 1
            if total + need > max_chars:
                remaining = max_chars - total - len(header) - 4
                if remaining >= 40:
                    parts.append(header + chunk.text[:remaining] + "...")
                break
            parts.append(header + chunk.text)
            total += need
        return "\n".join(parts)

    def add_document(self, name: str, content: str) -> Path:
        return self._backend.add_document(name, content)

    async def add_document_async(self, name: str, content: str) -> Path:
        """异步新增文档（vector 后端使用，避免 reload 死锁）"""
        backend = self._backend
        if isinstance(backend, VectorKnowledgeStore):
            return await backend.add_document_async(name, content)
        return backend.add_document(name, content)

    def remove_document(self, name: str) -> bool:
        return self._backend.remove_document(name)

    async def remove_document_async(self, name: str) -> bool:
        """异步删除文档（vector 后端使用）"""
        backend = self._backend
        if isinstance(backend, VectorKnowledgeStore):
            return await backend.remove_document_async(name)
        return backend.remove_document(name)

    def list_documents(self) -> list[dict]:
        return self._backend.list_documents()
