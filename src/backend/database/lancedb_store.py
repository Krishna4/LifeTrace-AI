import os
import uuid
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import pyarrow as pa
except ImportError:
    pa = None

try:
    import lancedb
except ImportError:
    lancedb = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

DEFAULT_LANCE_DIR = os.environ.get("PERSONAL_RAG_LANCE_DIR", "lancedb_data")
MODEL_NAME = "BAAI/bge-small-en-v1.5"
VECTOR_DIM = 384

ENGLISH_STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", 
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", 
    "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", 
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", 
    "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", 
    "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", 
    "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "s", "same", "she", 
    "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves", 
    "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", 
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", 
    "why", "with", "would", "you", "your", "yours", "yourself", "yourselves", "give", "show", "tell"
}

# Lazy global model instance
_model_instance: Optional[Any] = None
# In-memory storage fallback if LanceDB is not installed in the active environment
_in_memory_fallback_chunks: List[Dict[str, Any]] = []


def get_device() -> str:
    """Detects available hardware acceleration (MPS for Apple Silicon Mac, CUDA for NVIDIA GPU, or multi-threaded CPU)."""
    try:
        import torch
        if hasattr(torch, "set_num_threads"):
            cores = os.cpu_count() or 4
            torch.set_num_threads(cores)

        if torch.backends.mps.is_available():
            return "mps"
        elif torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def get_embedding_model() -> Any:
    global _model_instance
    if SentenceTransformer is None:
        raise ImportError("sentence_transformers module not available")
    if _model_instance is None:
        device = get_device()
        logger.info(f"⚡ Initializing SentenceTransformer '{MODEL_NAME}' on hardware device: '{device}'")
        try:
            _model_instance = SentenceTransformer(MODEL_NAME, device=device)
        except Exception:
            _model_instance = SentenceTransformer("all-MiniLM-L6-v2", device=device)
    return _model_instance


def extract_search_tokens(query: str) -> List[str]:
    """Extracts non-stopword normalized tokens for high-precision BM25 matching."""
    raw_tokens = re.findall(r"\b[A-Za-z0-9_]{2,}\b", query.lower())
    filtered = [t for t in raw_tokens if t not in ENGLISH_STOP_WORDS]
    return filtered if filtered else raw_tokens


def get_arrow_schema() -> Any:
    """Apache Arrow schema for LanceDB text_chunks table with metadata & tenant fields."""
    if pa is None:
        return None
    return pa.schema([
        ("vector", pa.list_(pa.float32(), VECTOR_DIM)),
        ("id", pa.string()),
        ("document_id", pa.int64()),
        ("chunk_index", pa.int32()),
        ("text_content", pa.string()),
        ("bm25_tokens", pa.string()),
        ("source_type", pa.string()),
        ("username", pa.string()),
        ("is_secure", pa.int32()),
        ("source_file", pa.string()),
        ("doc_type", pa.string()),
        ("timestamp_start", pa.float32()),
        ("timestamp_end", pa.float32()),
        ("created_at", pa.string()),
    ])


class LanceDBStore:
    def __init__(self, db_dir: str = DEFAULT_LANCE_DIR):
        self.db_dir = db_dir
        self.table_name = "text_chunks"
        if lancedb is not None and pa is not None:
            os.makedirs(self.db_dir, exist_ok=True)
            self.db = lancedb.connect(self.db_dir)
            self._init_table()
        else:
            self.db = None
            logger.warning("lancedb or pyarrow module missing in runtime. Using in-memory vector store fallback.")

    def _init_table(self) -> None:
        """Initialize LanceDB table if not present."""
        if self.db is not None:
            try:
                table_names = self.db.table_names()
                if self.table_name not in table_names:
                    schema = get_arrow_schema()
                    if schema is not None:
                        self.db.create_table(self.table_name, schema=schema)
            except Exception as e:
                logger.debug(f"Table init deferred: {e}")

    def get_table(self) -> Any:
        if self.db is None:
            return None
        try:
            return self.db.open_table(self.table_name)
        except Exception:
            schema = get_arrow_schema()
            return self.db.create_table(self.table_name, schema=schema)

    def preprocess_bm25_tokens(self, text: str) -> str:
        """Tokenize text into normalized space-separated tokens for BM25 matching."""
        tokens = extract_search_tokens(text)
        return " ".join(tokens)

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        try:
            model = get_embedding_model()
            embeddings = model.encode(
                texts,
                batch_size=128,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return embeddings.tolist()
        except Exception:
            results = []
            for text in texts:
                vec = [0.0] * VECTOR_DIM
                words = re.findall(r"\b\w+\b", text.lower())
                for word in words:
                    idx = abs(hash(word)) % VECTOR_DIM
                    vec[idx] += 1.0
                norm = (sum(x * x for x in vec) ** 0.5) or 1.0
                results.append([x / norm for x in vec])
            return results

    def add_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        """
        Add text chunk dicts to LanceDB table or in-memory fallback with metadata.
        """
        if not chunks:
            return 0

        texts = [c["text_content"] for c in chunks]
        embeddings = self.generate_embeddings(texts)

        data = []
        for i, chunk in enumerate(chunks):
            record = {
                "vector": embeddings[i],
                "id": str(chunk.get("id", uuid.uuid4())),
                "document_id": int(chunk.get("document_id", chunk.get("doc_id", 0))),
                "chunk_index": int(chunk.get("chunk_index", i)),
                "text_content": str(chunk["text_content"]),
                "bm25_tokens": self.preprocess_bm25_tokens(str(chunk["text_content"])),
                "source_type": str(chunk.get("source_type", "text")),
                "username": str(chunk.get("username", "default_user")),
                "is_secure": int(1 if chunk.get("is_secure") else 0),
                "source_file": str(chunk.get("source_file", "")),
                "doc_type": str(chunk.get("doc_type", chunk.get("source_type", "text"))),
                "timestamp_start": float(chunk.get("timestamp_start", 0.0) or 0.0),
                "timestamp_end": float(chunk.get("timestamp_end", 0.0) or 0.0),
                "created_at": str(chunk.get("created_at", "")),
            }
            data.append(record)

        if self.db is not None:
            table = self.get_table()
            try:
                table.add(data)
            except Exception as e:
                logger.warning(f"LanceDB table add error: {e}. Attempting schema recreation...")
                schema = get_arrow_schema()
                table = self.db.create_table(self.table_name, schema=schema, mode="overwrite")
                table.add(data)
        else:
            global _in_memory_fallback_chunks
            _in_memory_fallback_chunks.extend(data)

        return len(data)

    def _build_filter_expr(self, username: Optional[str] = None, include_secure: bool = True) -> Optional[str]:
        """Build SQL WHERE filter for LanceDB query."""
        filters = []
        if username:
            safe_user = username.replace("'", "''")
            filters.append(f"username = '{safe_user}'")
        if not include_secure:
            filters.append("is_secure = 0")
        return " AND ".join(filters) if filters else None

    def vector_search(
        self,
        query: str,
        limit: int = 5,
        username: Optional[str] = None,
        include_secure: bool = True,
    ) -> List[Dict[str, Any]]:
        """Dense vector similarity search with user isolation filtering."""
        query_vector = self.generate_embeddings([query])[0]

        if self.db is not None:
            try:
                table = self.get_table()
                search_builder = table.search(query_vector)
                filter_expr = self._build_filter_expr(username, include_secure)
                if filter_expr:
                    search_builder = search_builder.where(filter_expr)
                results = search_builder.limit(limit).to_list()
                return results
            except Exception as e:
                logger.warning(f"LanceDB vector search error: {e}. Falling back to in-memory filter...")

        return self._in_memory_vector_search(query_vector, limit, username, include_secure)

    def bm25_search(
        self,
        query: str,
        limit: int = 5,
        username: Optional[str] = None,
        include_secure: bool = True,
    ) -> List[Dict[str, Any]]:
        """Sparse BM25 token matching search with stopword filtering and relevance scoring."""
        tokens = extract_search_tokens(query)
        if not tokens:
            return []

        if self.db is not None:
            try:
                table = self.get_table()
                df = table.to_pandas()
                if df.empty:
                    return []

                # Apply metadata filters
                if username and "username" in df.columns:
                    df = df[df["username"] == username]
                if not include_secure and "is_secure" in df.columns:
                    df = df[df["is_secure"] == 0]

                if df.empty:
                    return []

                # Compute keyword matching score
                def score_row(row_text: str) -> float:
                    text_lower = str(row_text).lower()
                    score = 0.0
                    for tok in tokens:
                        if tok in text_lower:
                            score += 1.0 + (text_lower.count(tok) ** 0.5)
                    return score

                df["bm25_score"] = df["text_content"].apply(score_row)
                matches = df[df["bm25_score"] > 0].sort_values(by="bm25_score", ascending=False)
                results = matches.head(limit).to_dict(orient="records")
                return results
            except Exception as e:
                logger.warning(f"LanceDB BM25 search error: {e}. Falling back to in-memory...")

        # In-memory fallback
        scored = []
        for c in _in_memory_fallback_chunks:
            if username and c.get("username") != username:
                continue
            if not include_secure and c.get("is_secure", 0) != 0:
                continue

            text_lower = str(c.get("text_content", "")).lower()
            score = 0.0
            for t in tokens:
                if t in text_lower:
                    score += 1.0 + (text_lower.count(t) ** 0.5)
            if score > 0:
                scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def hybrid_search(
        self,
        query: str,
        limit: int = 5,
        username: Optional[str] = None,
        include_secure: bool = True,
    ) -> List[Dict[str, Any]]:
        """Hybrid search combining Dense Vector + Sparse BM25 with Reciprocal Rank Fusion (RRF) and tenant isolation."""
        vector_results = self.vector_search(query, limit=limit * 2, username=username, include_secure=include_secure)
        bm25_results = self.bm25_search(query, limit=limit * 2, username=username, include_secure=include_secure)

        scores: Dict[str, float] = {}
        chunk_map: Dict[str, Dict[str, Any]] = {}

        for rank, item in enumerate(vector_results):
            cid = str(item.get("id"))
            scores[cid] = scores.get(cid, 0.0) + (1.0 / (60 + rank + 1))
            chunk_map[cid] = item

        for rank, item in enumerate(bm25_results):
            cid = str(item.get("id"))
            scores[cid] = scores.get(cid, 0.0) + (1.0 / (60 + rank + 1))
            chunk_map[cid] = item

        sorted_ids = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
        top_ids = sorted_ids[:limit]

        final_chunks = []
        for cid in top_ids:
            chunk = chunk_map[cid]
            chunk["rrf_score"] = scores[cid]
            final_chunks.append(chunk)

        logger.info(
            f"🔍 [Hybrid Retrieval] Query: '{query}' | User: '{username}' | "
            f"Vector hits: {len(vector_results)} | BM25 hits: {len(bm25_results)} | Top Fused: {len(final_chunks)}"
        )
        return final_chunks

    def delete_document_chunks(self, document_id: int) -> None:
        """Purges all vector chunks associated with document_id from LanceDB and in-memory storage."""
        global _in_memory_fallback_chunks
        _in_memory_fallback_chunks = [c for c in _in_memory_fallback_chunks if c.get("document_id") != document_id]
        if self.db is not None:
            try:
                table = self.get_table()
                if table is not None:
                    table.delete(f"document_id = {document_id}")
                    logger.info(f"🗑️ Purged vector chunks for document_id #{document_id} from LanceDB.")
            except Exception as e:
                logger.error(f"Error purging document #{document_id} from LanceDB: {e}")

    def _in_memory_vector_search(
        self,
        query_vector: List[float],
        limit: int,
        username: Optional[str] = None,
        include_secure: bool = True,
    ) -> List[Dict[str, Any]]:
        """Cosine similarity helper for in-memory fallback with metadata filters."""
        if not _in_memory_fallback_chunks:
            return []

        def dot(v1, v2):
            return sum(x * y for x, y in zip(v1, v2))

        scored = []
        for c in _in_memory_fallback_chunks:
            if username and c.get("username") != username:
                continue
            if not include_secure and c.get("is_secure", 0) != 0:
                continue

            sim = dot(query_vector, c["vector"])
            scored.append((sim, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]
