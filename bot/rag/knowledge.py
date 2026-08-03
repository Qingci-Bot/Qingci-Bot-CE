"""轻量知识库 - 本地文件 + 关键词检索（纯 Python，无重型依赖）

设计要点：
- 知识来源：知识库目录（默认 data/knowledge/）下的 .txt/.md 文件
- 简单分块：按 chunk_size 字符切分，相邻分块保留 chunk_overlap 重叠
- 检索：关键词匹配打分（ASCII 词 + 中文二元组），按词频得分排序取 top_k，
  个人知识库规模下无需向量模型与向量数据库
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


@dataclass
class KnowledgeChunk:
    """知识库分块"""
    source: str                       # 来源文档名（不含扩展名）
    text: str                         # 分块原文
    tf: Counter = field(default_factory=Counter)  # 词频（检索打分用）


class KnowledgeStore:
    """基于本地文件的轻量知识库（分块 + 关键词检索）"""

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

    def reload(self) -> int:
        """（重新）索引知识库目录下所有文档，返回分块总数"""
        chunks: list[KnowledgeChunk] = []
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
                        KnowledgeChunk(
                            source=path.stem,
                            text=piece,
                            tf=Counter(tokenize(piece)),
                        )
                    )
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
            piece = text[start:start + self._chunk_size].strip()
            if piece:
                pieces.append(piece)
            if start + self._chunk_size >= len(text):
                break
        return pieces

    # ============ 检索 ============

    def search(self, query: str, top_k: Optional[int] = None) -> list[KnowledgeChunk]:
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
        """构造注入 system_prompt 的参考资料文本（无命中返回 ""）

        注入长度受 max_chars 上限约束，最后一个片段超长时截断并标注省略。
        """
        hits = self.search(query)
        if not hits or max_chars <= 0:
            return ""
        parts: list[str] = []
        total = 0
        for chunk in hits:
            header = f"[来源《{chunk.source}》]"
            need = len(header) + len(chunk.text) + 1
            if total + need > max_chars:
                # 剩余预算不足时，片段足够长才截断纳入
                remaining = max_chars - total - len(header) - 4
                if remaining >= 40:
                    parts.append(header + chunk.text[:remaining] + "...")
                break
            parts.append(header + chunk.text)
            total += need
        return "\n".join(parts)

    # ============ 文档管理 ============

    def add_document(self, name: str, content: str) -> Path:
        """新增文档（写入知识库目录后重建索引）

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
        self.reload()
        return path

    def remove_document(self, name: str) -> bool:
        """删除文档并重建索引，返回是否删除成功

        安全：与 add_document 对称，入口先经 _safe_name 校验（拦截
        路径穿越字符）；拼接后再用 resolve().is_relative_to 双重
        确认目标位于知识库目录内，防止删除目录外文件。
        """
        safe = self._safe_name(name)
        if not safe:
            logger.warning(f"拒绝非法文档名删除请求: {name!r}")
            return False
        root = self._root.resolve()
        for suffix in self.SUPPORTED_SUFFIXES:
            path = self._root / f"{safe}{suffix}"
            # 双重保险：解析后的真实路径必须仍在知识库目录内
            try:
                if not path.resolve().is_relative_to(root):
                    logger.warning(f"路径穿越拦截: {path}")
                    continue
            except OSError:
                continue
            if path.is_file():
                path.unlink()
                self.reload()
                return True
        return False

    def list_documents(self) -> list[dict]:
        """列出知识库文档（含无有效分块的空文档），按名称排序"""
        docs: dict[str, int] = {}
        for chunk in self._chunks:
            docs[chunk.source] = docs.get(chunk.source, 0) + 1
        if self._root.exists():
            for path in self._root.iterdir():
                if (
                    path.suffix.lower() in self.SUPPORTED_SUFFIXES
                    and path.is_file()
                ):
                    docs.setdefault(path.stem, 0)
        return [
            {"name": name, "chunks": count}
            for name, count in sorted(docs.items())
        ]

    @staticmethod
    def _safe_name(name: str) -> str:
        """文档名安全校验（防路径穿越），合法返回原名，否则返回空串"""
        match = _NAME_RE.fullmatch(name.strip())
        return match.group(0) if match else ""
