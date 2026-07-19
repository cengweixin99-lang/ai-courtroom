from __future__ import annotations

from collections.abc import Sequence

from mootcourt.schemas.legal_search import LegalArticleDocument
from mootcourt.search.embeddings import EmbeddingProvider


async def embed_legal_documents(
    documents: Sequence[LegalArticleDocument],
    provider: EmbeddingProvider,
    batch_size: int,
) -> list[LegalArticleDocument]:
    embedded: list[LegalArticleDocument] = []
    for offset in range(0, len(documents), batch_size):
        batch = documents[offset : offset + batch_size]
        texts = [_embedding_text(document) for document in batch]
        vectors = await provider.embed(texts)
        if len(vectors) != len(batch):
            raise ValueError("embedding provider returned an unexpected vector count")
        for document, vector in zip(batch, vectors, strict=True):
            embedded.append(
                document.model_copy(
                    update={
                        "embedding_version": provider.version,
                        "embedding": vector,
                    }
                )
            )
    return embedded


def _embedding_text(document: LegalArticleDocument) -> str:
    # 保留完整条款，不以固定字符切块破坏否定、例外或但书。
    return f"{document.instrument_title}\n{document.article_number}\n{document.text}"
