"""Local lexical RAG using SQLite FTS5/BM25 and LangChain's retriever interface."""

import re
from collections import OrderedDict
from urllib.parse import urlsplit

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.database import connection
from app.matching import query_evidence


class OfferRetriever(BaseRetriever):
    region: str = "czechia"
    k: int = 6

    def _get_relevant_documents(self, query, *, run_manager):
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)[:12]
        if not tokens:
            return []
        match = " OR ".join('"' + token + '"' for token in tokens)
        with connection() as db:
            rows = db.execute(
                """SELECT title,content,url,checked_at FROM offer_documents
                WHERE offer_documents MATCH ? AND (?='global' OR region=?) ORDER BY bm25(offer_documents),url LIMIT ?""",
                (match, self.region, self.region, min(300, max(self.k, self.k * 20))),
            ).fetchall()
        # Filter before top-k: high-scoring accessories must not crowd out the
        # actual known product. Spread seeds across merchants instead of letting
        # one large category monopolize all refresh slots.
        stores = OrderedDict()
        for row in rows:
            if query_evidence(query, row["title"]):
                host = (urlsplit(row["url"]).hostname or "").removeprefix("www.")
                stores.setdefault(host, []).append(row)
        selected = []
        while stores and len(selected) < self.k:
            for host in list(stores):
                selected.append(stores[host].pop(0))
                if not stores[host]:
                    del stores[host]
                if len(selected) >= self.k:
                    break
        return [
            Document(
                page_content=row["content"],
                metadata={"url": row["url"], "checked_at": row["checked_at"], "title": row["title"]},
            )
            for row in selected
        ]
