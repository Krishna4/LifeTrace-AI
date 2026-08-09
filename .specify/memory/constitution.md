<!--
SYNC IMPACT REPORT
==================
Version Change: 1.0.0 -> 1.1.0
Bump Rationale: Added strict low-resource memory constraints (max 4GB RAM), explicit multimodal tech stack and model choices (SQLite, LanceDB, bge-small-en-v1.5, faster-whisper tiny, Florence-2-base, local Qwen-2.5-1.5B via Ollama), and dynamic query routing principles.
Modified Principles:
  - I. Language & Runtime Environment (Python 3.11+) -> II. Language & Runtime Baseline (Python 3.11+)
  - II. Strict Schema Validation (Pydantic v2) -> III. Strict Data Contract & Schema Validation (Pydantic v2)
  - III. Test-Driven Development (TDD - NON-NEGOTIABLE) -> V. Test-Driven Development (TDD - NON-NEGOTIABLE)
Added Principles:
  - I. Resource & Memory Constraint (Max 4GB RAM Limit)
  - IV. Dynamic Query Routing (SQL vs. Vector)
Added Sections:
  - Technical Stack & Model Specifications
  - Development & Quality Gates
  - Governance
Removed Sections: None
Follow-up TODOs: None
-->

# PersonalRag Constitution

## Core Principles

### I. Resource & Memory Constraint (Max 4GB RAM Limit)
Total runtime RAM usage across all active services, model runtimes, vector stores, and processing pipelines MUST NOT exceed 4GB. Models MUST be quantized, execution pipelines MUST stream large data chunks, and model weights MUST be loaded lazily or sequentially where necessary to strictly observe this ceiling. Memory allocations MUST be benchmarked and validated during testing.

### II. Language & Runtime Baseline (Python 3.11+)
The PersonalRag codebase MUST target Python 3.11+ exclusively. Features or syntax incompatible with Python 3.11+ syntax and runtime MUST NOT be used. Async capabilities, modern typing constructs (`type` statements, `Self`), and memory efficiency enhancements in Python 3.11+ MUST be fully leveraged across all modules.

### III. Strict Data Contract & Schema Validation (Pydantic v2)
All data contracts, configuration objects, ingestion models, search results, and API payloads MUST enforce Pydantic v2 schemas. Untyped dictionaries, raw JSON manipulation, or unvalidated payloads at system boundaries are strictly prohibited. Schemas MUST be strictly typed and immutable (`frozen=True`) where possible.

### IV. Dynamic Query Routing (SQL vs. Vector)
The system MUST implement intelligent, dynamic query routing. Structured or analytical metadata queries MUST route to SQLite, while semantic and visual similarity searches MUST route to LanceDB vector storage. Hybrid queries MUST be explicitly orchestrated using strongly typed intermediate schema representations.

### V. Test-Driven Development (TDD - NON-NEGOTIABLE)
Test-Driven Development is mandatory across all modules. Tests MUST be written and verified to fail prior to writing production code. The Red-Green-Refactor cycle MUST be strictly enforced: Write failing test -> Verify failure -> Implement minimal code to pass -> Refactor. No task or pull request shall be approved without clean unit, integration, and routing test coverage.

## Technical Stack & Model Specifications

### Storage & Runtime Stack
- **Runtime Environment**: Python >= 3.11.
- **Structured Data Storage**: SQLite for relational metadata, structured indexing, and analytical querying.
- **Vector Storage Engine**: LanceDB for embedded multimodal vector storage and fast similarity retrieval.
- **Validation & Quality**: Pydantic v2 for data contracts, `ruff` for linting/formatting, `mypy` (strict mode) for static type checking.

### Multimodal Model Specifications
- **Text Embeddings**: `bge-small-en-v1.5` (lightweight 384-dimensional dense text embeddings).
- **Audio Transcription**: `faster-whisper` (`tiny` model variant for fast, low-memory speech-to-text).
- **Vision Language Model**: `Florence-2-base` (compact vision model for image captioning and visual grounding).
- **Local LLM / Reasoning**: `Qwen-2.5-1.5B` served locally via Ollama.

## Development & Quality Gates

### Quality Gate Requirements
- **Memory Ceiling Gate**: Automated benchmark tests MUST verify that peak RAM usage remains <= 4GB during peak multimodal ingestion and hybrid retrieval runs.
- **Query Routing Gate**: Router unit tests MUST validate correct routing decisions (SQL, Vector, or Hybrid) across predefined query test datasets.
- **TDD & Code Quality Gate**: Formatting (`ruff`), type checking (`mypy --strict`), and 100% passing test execution MUST complete successfully before code integration.

## Governance

This Constitution supersedes all informal team habits, unwritten guidelines, and ad-hoc development practices.

### Amendment & Versioning Policy
- Versioning follows Semantic Versioning (`MAJOR.MINOR.PATCH`):
  - **MAJOR**: Backward-incompatible governance changes, principle removals, or major architectural shifts.
  - **MINOR**: Addition of new core principles, technical stack additions, or expanded quality gates.
  - **PATCH**: Clarifications, formatting fixes, or wording adjustments without semantic alteration.
- All amendments MUST update the `Sync Impact Report`, `LAST_AMENDED_DATE`, and bump `CONSTITUTION_VERSION` accordingly.

**Version**: 1.1.0 | **Ratified**: 2026-08-09 | **Last Amended**: 2026-08-09
