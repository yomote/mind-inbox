"""
RAG (Retrieval-Augmented Generation) module.

Azure AI Search 差し替えポイント:
    from azure.search.documents.aio import SearchClient
    from azure.search.documents.models import VectorizedQuery
    client = SearchClient(endpoint=..., index_name=..., credential=...)
    results = await client.search(search_text=query, vector_queries=[...])
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    content: str
    source: str
    score: float


async def retrieve(query: str, top_k: int = 3) -> list[RetrievalResult]:
    """Retrieve relevant documents for the given query."""
    logger.debug("RAG retrieve: query=%r top_k=%d", query, top_k)

    # Stub — replace with Azure AI Search vector search
    return [
        RetrievalResult(
            content=f"[stub] Document relevant to: {query}",
            source="stub://knowledge-base/doc1",
            score=0.9,
        )
    ]
