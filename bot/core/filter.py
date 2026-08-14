"""敏感词过滤器

从词库文件加载敏感词（一行一词，忽略空行与 `#` 注释行），
提供命中检测与打码替换能力：
- check(text): 返回首个命中词（无命中返回 None）
- mask(text, char="*"): 将命中词替换为等长打码字符

实现要点：
- 词库预编译为单个大正则（re.escape + "|" 连接），词库变更时重编译缓存
- 空词库时 check 直接返回 None、mask 原样返回，不产生正则开销
- 词库文件不存在视为空词库（不抛异常，仅记录日志）
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger("qingci-bot.filter")


class SensitiveFilter:
    """敏感词过滤器"""

    def __init__(self, words_file: str | Path):
        self._words_file = Path(words_file)
        self._words: list[str] = []
        # 预编译的合并正则，空词库时为 None
        self._pattern: re.Pattern[str] | None = None
        self.reload()

    @property
    def words(self) -> list[str]:
        """当前词库列表（副本）"""
        return list(self._words)

    def reload(self) -> None:
        """重新加载词库并重编译正则

        词库格式：一行一词，忽略空行与 `#` 开头的注释行；
        文件不存在视为空词库。
        """
        words: list[str] = []
        if not self._words_file.exists():
            logger.info(f"敏感词库不存在，视为空词库: {self._words_file}")
        else:
            try:
                text = self._words_file.read_text(encoding="utf-8")
            except Exception:
                logger.exception(f"读取敏感词库失败，视为空词库: {self._words_file}")
                text = ""
            for line in text.splitlines():
                word = line.strip()
                if not word or word.startswith("#"):
                    continue
                words.append(word)

        self._words = words
        # 词库变更：重编译合并正则缓存
        if words:
            self._pattern = re.compile("|".join(re.escape(w) for w in words))
            logger.info(f"敏感词库已加载: {len(words)} 个词 ({self._words_file})")
        else:
            self._pattern = None
            # 空词库时过滤形同虚设，明确提示维护人员补充词库
            logger.warning(f"敏感词库为空，过滤不会生效，请编辑 {self._words_file}")

    def check(self, text: str) -> str | None:
        """检测文本中的敏感词，返回首个命中词；无命中或空词库返回 None"""
        if self._pattern is None or not text:
            return None
        match = self._pattern.search(text)
        return match.group(0) if match else None

    def mask(self, text: str, char: str = "*") -> str:
        """将文本中的敏感词替换为等长打码字符；空词库时原样返回"""
        if self._pattern is None or not text:
            return text
        return self._pattern.sub(lambda m: char * len(m.group(0)), text)
