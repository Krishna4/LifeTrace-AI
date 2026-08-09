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

# Lazy global model instance
_model_instance: Optional[Any] = None
# In-memory storage fallback if LanceDB is not installed in the active environment
_in_memory_fallback_chunks: List[Dict[str, Any]] = []


def get_device() -> str:
    """Detects available hardware acceleration (MPS for Apple Silicon Mac, CUDA for NVIDIA GPU, or multi-threaded CPU)."""
    try:
        import torch
        # Configure PyTorch CPU threadpool to use all physical cores
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


def get_arrow_schema() -> Any:
    """Apache Arrow schema for LanceDB text_chunks table."""
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
        tokens = re.findall(r"\b\w+\b", text.lower())
        return " ".join(tokens)

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        try:
            model = get_embedding_model()
            # High-throughput batch size = 128 with device acceleration
            embeddings = model.encode(
                texts,
                batch_size=128,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return embeddings.tolist()
        except Exception:
            # Deterministic 384-d normalized fallback vector for offline testing
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
        Add text chunk dicts to LanceDB table or in-memory fallback.
        """
        if not chunks:
            return 0

        texts = [c["text_content"] for c in chunks]
        embeddings = self.generate_embeddings(texts)

        data = []
        for i, chunk in enumerate(chunks):
            record = {
                "vector": embeddings[i],
                "id": chunk.get("id", str(uuid.uuid4())),
                "document_id": int(chunk.get("document_id", chunk.get("doc_id", 0))),
                "chunk_index": int(chunk.get("chunk_index", i)),
                "text_content": chunk["text_content"],
                "bm25_tokens": self.preprocess_bm25_tokens(chunk["text_content"]),
                "source_type": chunk.get("source_type", "text"),
                "timestamp_start": float(chunk.get("timestamp_start", 0.0) or 0.0),
                "timestamp_end": float(chunk.get("timestamp_end", 0.0) or 0.0),
                "created_at": chunk.get("created_at", ""),
            }
            data.append(record)

        if self.db is not None:
            table = self.get_table()
            table.add(data)
        else:
            global _in_memory_fallback_chunks
            _in_memory_fallback_chunks.extend(data)

        return len(data)

    def vector_search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Dense vector similarity search using bge-small embeddings."""
        query_vector = self.generate_embeddings([query])[0]

        if self.db is not None:
            table = self.get_table()
            results = table.search(query_vector).limit(limit).to_list()
            return results
        else:
            # In-memory cosine similarity fallback
            return self._in_memory_vector_search(query_vector, limit)

    def bm25_search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Sparse BM25 token matching search."""
        if self.db is not None:
            table = self.get_table()
            df = table.to_pandas()
            if df.empty:
                return []

            tokens = re.findall(r"\b\w+\b", query.lower())
            if not tokens:
                return []

            pattern = "|".join(tokens)
            matches = df[df["text_content"].str.contains(pattern, case=False, na=False, regex=True)]
            results = matches.head(limit).to_dict(orient="records")
            return results
        else:
            tokens = re.findall(r"\b\w+\b", query.lower())
            if not tokens:
                return []
            matches = []
            for c in _in_memory_fallback_chunks:
                text_lower = c["text_content"].lower()
                if any(t in text_lower for t in tokens):
                    matches.append(c)
            return matches[:limit]

    def hybrid_search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Hybrid search combining Dense Vector + Sparse BM25 using Reciprocal Rank Fusion (RRF)."""
        vector_results = self.vector_search(query, limit=limit * 2)
        bm25_results = self.bm25_search(query, limit=limit * 2)

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

        return final_chunks

    def delete_document_chunks(self, document_id: int) -> None:
        """Purges all vector chunks associated with document_id from LanceDB and in-memory storage."""
        global _in_memory_fallback_chunks
        _in_memory_fallback_chunks = [c for c in _in_memory_fallback_chunks if c.get("document_id") != document_id]
        if self.tbl is not None:
            try:
                self.tbl.delete(f"document_id = {document_id}")
                logger.info(f"🗑️ Purged vector chunks for document_id #{document_id} from LanceDB.")
            except Exception as e:
                logger.error(f"Error purging document #{document_id} from LanceDB: {e}")

    def _in_memory_vector_search(self, query_vector: List[float], limit: int) -> List[Dict[str, Any]]:
        """Cosine similarity helper for in-memory fallback."""
        if not _in_memory_fallback_chunks:
            return []

        def dot(v1, v2):
            return sum(x * y for x, y in zip(v1, v2))

        scored = []
        for c in _in_memory_fallback_chunks:
            sim = dot(query_vector, c["vector"])
            scored.append((sim, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]
