"""
Knowledge base loading, chunking and BM25 retrieval — the "R" in RAG.

Why BM25 instead of embeddings?
    * Zero extra dependencies or API calls, so the demo setup is just `pip install anthropic`.
    * Fully deterministic, so Lab 1 results are repeatable on stage.
    * The lesson is the same for any retriever: chunking strategy and top_k decide
      whether the answer ever reaches the model.

Lab 1 planted bug (see settings.py): with `chunk_size_words=80, top_k=2` the
carry-forward rule in leave_policy.md lands in a chunk that contains none of the
words a user asks with ("carry", "forward", "unused"), so BM25 never returns it.
The LLM then answers from the wrong passage or says "I don't know".

Run `python -m helpdesk_agent.knowledge_base` to print the retrieval results for a
sample question under both the buggy and fixed settings — no API key needed.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "knowledge_base"

# Very small English stop-word list: removes words that carry no retrieval signal.
_STOP_WORDS = frozenset(
    """a an and are as at be by can do does for from has have how i if in is it its
    me my of on or our so that the their them then there these they this to was
    we what when where which who will with you your""".split()
)


@dataclass(frozen=True)
class PolicyChunk:
    """One retrievable passage of a policy document."""

    chunk_id: str        # e.g. "leave_policy.md#3" — shown as the citation
    source_document: str  # file name, e.g. "leave_policy.md"
    text: str


def tokenize(text: str) -> list[str]:
    """Lower-case, split on non-alphanumerics, drop stop words.

    "Carry-Forward" → ["carry", "forward"]; "$300" → ["300"].
    """
    return [tok for tok in re.findall(r"[a-z0-9]+", text.lower()) if tok not in _STOP_WORDS]


def chunk_document(
    document_name: str, document_text: str, chunk_size_words: int, chunk_overlap_words: int
) -> list[PolicyChunk]:
    """Split a document into fixed-size, overlapping word windows.

    This deliberately naive splitter ignores headings and paragraphs — which is
    exactly how many real RAG pipelines start out, and why chunk size matters.
    """
    if chunk_overlap_words >= chunk_size_words:
        raise ValueError("chunk_overlap_words must be smaller than chunk_size_words")

    words = document_text.split()
    step = chunk_size_words - chunk_overlap_words
    chunks: list[PolicyChunk] = []
    for chunk_index, start in enumerate(range(0, len(words), step)):
        window = words[start : start + chunk_size_words]
        if not window:
            break
        chunks.append(
            PolicyChunk(
                chunk_id=f"{document_name}#{chunk_index}",
                source_document=document_name,
                text=" ".join(window),
            )
        )
        if start + chunk_size_words >= len(words):
            break  # last window already reached the end of the document
    return chunks


class BM25Retriever:
    """Classic Okapi BM25 ranking over a list of chunks.

    score(q, d) = Σ_t IDF(t) · tf(t,d)·(k1+1) / (tf(t,d) + k1·(1 − b + b·|d|/avgdl))
    """

    def __init__(self, chunks: list[PolicyChunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self._chunk_term_counts = [Counter(tokenize(c.text)) for c in chunks]
        self._chunk_lengths = [sum(tc.values()) for tc in self._chunk_term_counts]
        self._average_chunk_length = sum(self._chunk_lengths) / max(len(chunks), 1)

        # Document frequency: in how many chunks does each term appear?
        document_frequency: Counter[str] = Counter()
        for term_counts in self._chunk_term_counts:
            document_frequency.update(term_counts.keys())
        total_chunks = len(chunks)
        self._idf = {
            term: math.log(1 + (total_chunks - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }

    def search(self, query: str, top_k: int) -> list[tuple[PolicyChunk, float]]:
        """Return the `top_k` highest-scoring chunks with their BM25 scores."""
        query_terms = tokenize(query)
        scored: list[tuple[PolicyChunk, float]] = []
        for chunk, term_counts, length in zip(
            self.chunks, self._chunk_term_counts, self._chunk_lengths
        ):
            score = 0.0
            for term in query_terms:
                tf = term_counts.get(term, 0)
                if tf == 0:
                    continue
                normaliser = self.k1 * (1 - self.b + self.b * length / self._average_chunk_length)
                score += self._idf.get(term, 0.0) * tf * (self.k1 + 1) / (tf + normaliser)
            if score > 0:
                scored.append((chunk, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]


def load_policy_documents() -> dict[str, str]:
    """Read every markdown file in knowledge_base/ → {file name: text}."""
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(KNOWLEDGE_BASE_DIR.glob("*.md"))
    }


@lru_cache(maxsize=16)
def build_retriever(chunk_size_words: int, chunk_overlap_words: int) -> BM25Retriever:
    """Build (and memoise) a retriever for one chunking configuration."""
    all_chunks: list[PolicyChunk] = []
    for document_name, document_text in load_policy_documents().items():
        all_chunks.extend(
            chunk_document(document_name, document_text, chunk_size_words, chunk_overlap_words)
        )
    return BM25Retriever(all_chunks)


# ------------------------------------------------------------------------------
# Offline demo: `python -m helpdesk_agent.knowledge_base`
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    sample_question = "How many unused annual leave days can I carry forward to next year?"
    for label, size, overlap, k in [("BUGGY", 80, 0, 2), ("FIXED", 400, 40, 4)]:
        retriever = build_retriever(size, overlap)
        print(f"\n=== {label}: chunk_size={size}, overlap={overlap}, top_k={k} "
              f"({len(retriever.chunks)} chunks) ===")
        for chunk, score in retriever.search(sample_question, k):
            has_answer = "maximum of 5 days" in chunk.text
            print(f"  {score:5.2f}  {chunk.chunk_id:<40} contains answer: {has_answer}")
