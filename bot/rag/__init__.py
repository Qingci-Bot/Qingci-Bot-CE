# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see LICENSE

"""轻量知识库（RAG）模块 - 基于本地文件的关键词检索"""

from .knowledge import KnowledgeChunk, KnowledgeStore

__all__ = ["KnowledgeChunk", "KnowledgeStore"]
