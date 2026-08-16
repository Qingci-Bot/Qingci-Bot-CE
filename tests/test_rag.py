"""知识库（RAG）测试：关键词库增量索引与检索"""

import builtins
from pathlib import Path

from bot.rag.knowledge import KeywordKnowledgeStore, KnowledgeStore


def _store(tmp_path: Path) -> KeywordKnowledgeStore:
    return KeywordKnowledgeStore(root=tmp_path / "knowledge", chunk_size=10, chunk_overlap=2)


def test_add_document_incremental(tmp_path, monkeypatch):
    """add_document 增量索引：不触发全量 reload"""
    store = _store(tmp_path)

    # 禁用 reload，验证增量 add 不会走全量重建
    def _boom():
        raise AssertionError("增量 add 不应触发全量 reload")

    monkeypatch.setattr(store, "reload", _boom)

    p1 = store.add_document("doc_a", "北京 上海 广州")
    assert p1.exists()
    assert store.chunk_count > 0

    before = store.chunk_count
    store.add_document("doc_b", "深圳 杭州 成都")
    assert store.chunk_count > before
    assert store.search("北京")  # 旧文档仍可检索


def test_remove_document_incremental(tmp_path, monkeypatch):
    """remove_document 增量索引：仅剔除被删分块，不触发全量 reload"""
    store = _store(tmp_path)
    store.add_document("doc_a", "北京 上海")
    store.add_document("doc_b", "深圳 杭州")

    def _boom():
        raise AssertionError("增量 remove 不应触发全量 reload")

    monkeypatch.setattr(store, "reload", _boom)

    total_before = store.chunk_count
    assert store.remove_document("doc_a") is True
    assert store.chunk_count < total_before
    # 被删文档不再命中，其余文档保留
    assert not store.search("北京")
    assert store.search("深圳")

    # 重复删除返回 False
    assert store.remove_document("doc_a") is False


def test_remove_document_preserves_other_chunks(tmp_path):
    """删除某文档后，其他文档的分块对象保持不变（增量而非重建）"""
    store = _store(tmp_path)
    store.add_document("doc_a", "alpha beta")
    store.add_document("doc_b", "gamma delta")

    doc_b_chunks = [c for c in store._chunks if c.source == "doc_b"]
    assert doc_b_chunks

    store.remove_document("doc_a")
    remaining = [c for c in store._chunks if c.source == "doc_b"]
    # 增量删除未触碰其他文档的分块（对象身份一致）
    assert remaining == doc_b_chunks


def test_reload_full_reindex(tmp_path):
    """reload 全量重建索引，与增量结果一致"""
    store = _store(tmp_path)
    store.add_document("doc_a", "北京 上海")
    store.add_document("doc_b", "深圳 杭州")

    text_chunks_a = sorted(c.text for c in store._chunks if c.source == "doc_a")
    count_before = store.chunk_count

    store.reload()
    assert store.chunk_count == count_before
    assert sorted(c.text for c in store._chunks if c.source == "doc_a") == text_chunks_a


def test_vector_mode_falls_back_to_keyword_when_lancedb_missing(tmp_path, monkeypatch):
    """lancedb 未安装（可选依赖缺失）时，vector 模式回退到 keyword 后端"""
    real_import = builtins.__import__

    def _block_lancedb(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "lancedb":
            raise ModuleNotFoundError("No module named 'lancedb'", name="lancedb")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _block_lancedb)

    store = KnowledgeStore(root=tmp_path / "knowledge", mode="vector")
    assert store.mode == "keyword", "lancedb 缺失应回退到 keyword 模式"
    assert isinstance(store._backend, KeywordKnowledgeStore)
    # 回退后 keyword 检索仍可用
    store.add_document("doc_a", "北京 上海 广州")
    assert store.search("北京")
