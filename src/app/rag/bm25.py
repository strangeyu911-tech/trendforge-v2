"""纯 Python BM25 检索：零重依赖，Demo 规模（数百 chunk）足够

V1 用 ChromaDB + MiniLM 导致镜像 40MB、编译依赖重；V2 以此替代。
接口保持可插拔：未来换向量后端时业务代码无感知。
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]")


def tokenize(text: str) -> list[str]:
    """英文按词，中文按单字（bigram 增强召回）"""
    text = text.lower()
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        tokens.append(tok)
    # 中文单字相邻组 bigram，提升短语召回
    cjk = [t for t in tokens if len(t) == 1 and "一" <= t <= "鿿"]
    tokens.extend(a + b for a, b in zip(cjk, cjk[1:]))
    return tokens


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs: list[list[str]] = []
        self.doc_len: list[int] = []
        self.avgdl = 0.0
        self.df: Counter = Counter()
        self.n = 0

    def fit(self, documents: list[str]) -> "BM25":
        self.docs = [tokenize(d) for d in documents]
        self.n = len(self.docs)
        self.doc_len = [len(d) for d in self.docs]
        self.avgdl = sum(self.doc_len) / max(self.n, 1)
        self.df = Counter()
        for d in self.docs:
            for tok in set(d):
                self.df[tok] += 1
        self._tfs = [Counter(d) for d in self.docs]
        return self

    def _idf(self, tok: str) -> float:
        df = self.df.get(tok, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> list[float]:
        q_tokens = tokenize(query)
        out: list[float] = []
        for i, tf in enumerate(self._tfs):
            score = 0.0
            dl_norm = self.k1 * (1 - self.b + self.b * self.doc_len[i] / max(self.avgdl, 1e-9))
            for tok in q_tokens:
                f = tf.get(tok, 0)
                if f:
                    score += self._idf(tok) * f * (self.k1 + 1) / (f + dl_norm)
            out.append(score)
        return out

    def top_k(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        s = self.scores(query)
        ranked = sorted(enumerate(s), key=lambda x: x[1], reverse=True)
        return [(i, v) for i, v in ranked[:k] if v > 0]
