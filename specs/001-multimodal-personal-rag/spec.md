# Feature Specification: Multimodal Personal RAG System

**Feature Branch**: `001-multimodal-personal-rag`

**Created**: 2026-08-09

**Status**: Draft

**Input**: User description: "Build a multimodal RAG system that tracks personal documents, audio, video, web pages, and financial transactions. Ingest audio/video with faster-whisper, images/OCR with Florence-2, text/URLs with Trafilatura. Parse monetary transactions (e.g., 'paid Alex $50') directly into SQLite. Store unstructured text chunks in LanceDB Arrow tables with hybrid search (vector + BM25). Include an SLM query router to direct questions to SQL, LanceDB, or both."

## Clarifications

### Session 2026-08-09

- Q: How should the ingestion pipeline handle fallback logic when a specialized model (e.g., Florence-2 OCR/captioning or faster-whisper speech transcription) fails to process a file? → A: Option A - Log model failure, extract raw text fallback (if applicable), set file status to PARTIAL_SUCCESS in SQLite, and index partial metadata without halting the pipeline.
- Q: How should the system handle partial transcript failures during long audio/video processing (e.g., if faster-whisper halts midway or produces empty/corrupted chunks)? → A: Save successfully transcribed text chunks with timestamp markers up to failure offset, set status to PARTIAL_TRANSCRIPT in SQLite, index valid segments, and support resuming transcription from the last failed timestamp offset on retry.
- Q: How should the system handle sync errors between SQLite structured metadata and LanceDB vector tables if one database succeeds while the other fails during chunk insertion? → A: Option A - Track vector index status (UNINDEXED, INDEXED, SYNC_FAILED) in SQLite, log vector write failures, and use a background reconciliation job to re-embed and sync missing LanceDB vector records.
- Q: How should memory management enforce the 4GB RAM ceiling during large file uploads (e.g., multi-gigabyte video files or massive document batches)? → A: Option B - Reject file uploads larger than 500MB immediately with a FILE_TOO_LARGE error.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Multimodal Ingestion & Processing (Priority: P1)

As a user, I want to ingest personal text documents, web page URLs, audio/video files, and images so that all my multimodal knowledge is captured and converted into searchable text and structured metadata.

**Why this priority**: Ingestion and multimodal processing form the core data foundation. Without robust ingestion of diverse file types, downstream search and analytical querying cannot function.

**Independent Test**: Can be tested independently by submitting sample text files, web page URLs, audio snippets, and image files to the ingestion interface, then verifying that transcripts, visual descriptions, clean article text, and text chunks are generated and stored.

**Acceptance Scenarios**:

1. **Given** an audio or video file, **When** ingested into the system, **Then** speech is transcribed into accurate text segments with timestamps and indexed.
2. **Given** an image containing text or visual content, **When** ingested into the system, **Then** optical character recognition (OCR) and visual captioning extract all text and scene descriptions for indexing.
3. **Given** a web page URL or HTML document, **When** ingested into the system, **Then** main body text and metadata are extracted (with ads, menus, and boilerplate stripped) and indexed.

---

### User Story 2 - Financial Transaction Extraction & Structured Querying (Priority: P2)

As a user, I want monetary transactions mentioned in my documents, notes, or media (e.g., "paid Alex $50 for dinner") automatically parsed into structured records so I can track spending and run precise financial queries.

**Why this priority**: Enables precise relational and quantitative analysis for financial statements alongside semantic document retrieval.

**Independent Test**: Can be tested independently by feeding text containing monetary statements, then querying the structured store to verify that transaction records (amount, currency, counterparty, description, date) are accurately populated and aggregate sums can be queried.

**Acceptance Scenarios**:

1. **Given** a document or transcript line stating a monetary transaction (e.g., "paid Alex $50"), **When** processed by the ingestion pipeline, **Then** a structured financial record is saved with payee ("Alex"), amount (50.00), currency ("USD"), and source metadata.
2. **Given** a user question requesting spending totals or payment history (e.g., "How much did I pay Alex?"), **When** submitted to the system, **Then** structured queries return accurate numeric totals and itemized transactions.

---

### User Story 3 - SLM Query Routing & Hybrid Search (Priority: P3)

As a user, I want to ask natural language questions and have an intelligent query router automatically direct my question to structured financial data, unstructured document search, or both, delivering a unified answer.

**Why this priority**: Delivers a seamless user experience that bridges quantitative analytical queries and conceptual semantic document retrieval without forcing the user to manually select search engines.

**Independent Test**: Can be tested independently by executing a set of test queries (financial, conceptual, and hybrid) and asserting that the router selects the appropriate target engine and returns relevant results.

**Acceptance Scenarios**:

1. **Given** a quantitative financial query, **When** processed by the query router, **Then** the router selects the structured relational engine and returns exact numerical results.
2. **Given** a conceptual document query, **When** processed by the query router, **Then** the router selects the hybrid vector/keyword engine and returns relevant document chunks.
3. **Given** a complex query requiring both financial numbers and document context, **When** processed by the query router, **Then** the router triggers hybrid execution across both engines and synthesizes a combined result.

### User Story 4 - Semantic Indexing & Agentic AI Retrieval Patterns (Priority: P2)

As a user, I want my documents indexed using semantic boundaries and retrieved using agentic AI patterns (multi-step query decomposition, iterative re-ranking, and self-correcting query reformulation) so that complex questions produce precise, synthesized answers rather than raw text dumps.

**Why this priority**: Enhances chunk context quality during indexing and equips the retrieval pipeline with autonomous reasoning steps to solve multi-part or ambiguous queries accurately.

**Independent Test**: Can be tested by submitting complex multi-part queries (e.g. "Find all expenses paid to Dammaiguda vendors and summarize the matching application forms"), verifying that the retriever decomposes the question, executes sub-queries, reformulates terms if initial context is incomplete, and synthesizes a direct natural language answer.

**Acceptance Scenarios**:

1. **Given** long unstructured text or documents during ingestion, **When** processed by the indexing pipeline, **Then** text is split along semantic paragraph and sentence boundaries (semantic chunking) with embedded semantic tags rather than static character cuts.
2. **Given** a complex or multi-part user question, **When** processed by the agentic retrieval engine, **Then** the engine decomposes the question into targeted sub-queries, executes them across SQL and Vector stores, and combines the retrieved contexts.
3. **Given** initial retrieval results with low relevance or missing terms, **When** detected by the agentic retrieval loop, **Then** the engine autonomously reformulates search terms and expands context limits before synthesizing the final answer.

---

### Edge Cases

- **Poor Media Quality**: How does the system handle noisy audio, low-resolution images, or corrupted web pages during ingestion?
- **Ambiguous Transactions**: How does the system resolve ambiguous monetary statements (e.g., "Alex owes $50" vs. "paid Alex $50") or potential duplicate transaction mentions across multiple documents?
- **Routing Ambiguity**: How does the query router handle ambiguous questions that could fit either structured or unstructured search paths?
- **Resource Constraints**: How does the ingestion pipeline enforce the 4GB total RAM boundary when processing large media files or running concurrent model inference?
- **Agentic Loop Bounds**: How does the agentic retrieval loop prevent infinite reformulation loops when no matching document context exists in the store?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST ingest text files, web page URLs, audio/video files, and image files into a unified processing pipeline.
- **FR-002**: System MUST transcribe audio and video inputs into text segments with timestamp markers.
- **FR-003**: System MUST perform optical character recognition (OCR) and visual captioning on image files to extract embedded text and visual context.
- **FR-004**: System MUST extract main article text and metadata from web URLs while stripping boilerplate markup, navigation, and advertisement content.
- **FR-005**: System MUST parse explicit and implicit monetary statements (e.g., "paid Alex $50", "spent $12.50 on lunch") into structured transaction records.
- **FR-006**: System MUST store structured transaction records, document metadata, and entity relationships in relational database tables.
- **FR-007**: System MUST split unstructured text into chunks and store dense embeddings alongside columnar text tables.
- **FR-008**: System MUST support hybrid search combining dense vector similarity search and sparse BM25 keyword matching with reciprocal rank fusion.
- **FR-009**: System MUST include a Small Language Model (SLM) query router to classify incoming questions and route them to structured SQL storage, vector search, or both.
- **FR-010**: System MUST enforce a maximum system memory footprint of 4GB RAM during concurrent ingestion, model inference, and query execution.
- **FR-011**: System MUST log model extraction failures, attempt raw text fallback extraction where applicable, mark record status as `PARTIAL_SUCCESS` in SQLite, and continue indexing available metadata without halting ingestion pipeline execution.
- **FR-012**: System MUST save successful audio/video transcript chunks up to any failure point, record transcript status as `PARTIAL_TRANSCRIPT` with error timestamp offset in SQLite, index valid chunks, and support resuming transcription from the saved offset on retry.
- **FR-013**: System MUST track vector indexing state (`UNINDEXED`, `INDEXED`, `SYNC_FAILED`) in SQLite metadata, log dual-store sync failures, and run a background reconciliation task to re-embed and sync missing LanceDB vector rows.
- **FR-014**: System MUST validate incoming file upload sizes prior to ingestion and reject any individual file exceeding 500MB with a `FILE_TOO_LARGE` error to enforce memory stability.
- **FR-015**: System MUST perform semantic chunking during indexing, preserving sentence structures and semantic paragraph boundaries before generating dense vector embeddings into LanceDB.
- **FR-016**: System MUST implement Agentic AI retrieval patterns, including multi-step query decomposition for complex questions, self-correction/query reformulation upon low confidence retrieval, and SLM-driven generative answer synthesis with context references.

### Key Entities

- **Source Document**: Represents an ingested artifact (file, URL, audio, image), containing source URI, document type, ingestion timestamp, and processing status.
- **Text Chunk**: Represents a semantically chunked section of transcribed speech, OCR output, web text, or document content, containing text string, chunk index, document reference, dense embedding vector, semantic tags, and sparse term frequencies.
- **Financial Transaction**: Represents a parsed monetary transaction, containing transaction ID, source document reference, counterparty/entity name, numeric amount, currency code, transaction type (expense, income, transfer), and timestamp.
- **Query Route & Agent State**: Represents an agentic query routing decision and execution trace, containing original user query, sub-query decomposition steps, target storage engines (`SQL`, `VECTOR`, or `HYBRID`), extracted SQL conditions or vector search terms, reformulation iteration count, and routing confidence score.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: System successfully ingests, processes, and indexes text, web, audio, and image artifacts with at least a 95% completion rate.
- **SC-002**: Financial transaction extraction achieves at least 90% accuracy in identifying monetary amounts, counterparties, and transaction direction from test content.
- **SC-003**: Hybrid search (vector + BM25 keyword matching) achieves a Top-5 retrieval recall rate of at least 85% for document reference queries.
- **SC-004**: The SLM Query Router accurately classifies test queries into SQL, Vector, or Hybrid execution paths with at least 90% accuracy.
- **SC-005**: System total memory footprint stays strictly within the 4GB RAM constraint during full pipeline benchmarks.
- **SC-006**: Search queries return unified answers in under 3 seconds under typical single-user query workloads.
- **SC-007**: Agentic retrieval patterns (query decomposition, self-correcting query reformulation, and SLM answer synthesis) achieve at least 90% answer synthesis accuracy on multi-part complex document queries.

## Assumptions

- Target media and documents are primarily in English or standard supported formats.
- Heavy multimodal models (speech, vision, LLM router) execute sequentially or lazily to respect the 4GB RAM ceiling.
- Web URL fetching respects standard web protocols and handles common text/HTML content types.
- Standard financial expressions specify monetary values in numeric or spelled-out formats with currency symbols or standard currency codes.
- Agentic query reformulation loops are bounded to a maximum of 2 iterations to maintain fast response latency (< 3s).
