# NL2SQL System Architecture

> A WrenAI-inspired natural language to SQL pipeline.
> Question → Intent → Retrieval → SQL Generation → Validation → Correction → NL Answer

---

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Technology Stack](#2-technology-stack)
3. [Project Structure](#3-project-structure)
4. [Configuration (`config.py`)](#4-configuration)
5. [MDL — Model Definition Language (`mdl/schema.py`)](#5-mdl--model-definition-language)
6. [Vector Store Layer (`indexing/store.py`)](#6-vector-store-layer)
7. [Indexing Pipeline — What Happens When a New DB Comes](#7-indexing-pipeline--what-happens-when-a-new-db-comes)
8. [Retrieval Pipelines — Finding Relevant Schema](#8-retrieval-pipelines)
9. [Generation Pipeline — From Question to SQL to Answer](#9-generation-pipeline)
10. [Service Layer — Orchestration](#10-service-layer)
11. [API Layer (`api/`)](#11-api-layer)
12. [FastAPI App Wiring (`main.py`)](#12-fastapi-app-wiring)
13. [Interactive CLI (`run.py`)](#13-interactive-cli)
14. [Full Query Flow — End to End](#14-full-query-flow--end-to-end)
15. [Misspelling & Typo Handling](#15-misspelling--typo-handling)
16. [Key Thresholds & Tuning](#16-key-thresholds--tuning)
17. [Extending the System](#17-extending-the-system)

---

## 1. High-Level Overview

```
                          +-----------+
                          |  User     |
                          | (CLI/API) |
                          +-----+-----+
                                |
                    +-----------v-----------+
                    |     Question Input     |
                    +-----------+-----------+
                                |
           +--------------------v--------------------+
           |           AskService State Machine       |
           |                                          |
           |  UNDERSTANDING                           |
           |    1. Historical match? ──→ return cached |
           |    2. Retrieve SQL pairs + Instructions   |
           |    3. 2-phase DB schema retrieval         |
           |    4. Intent classification               |
           |       ├─ MISLEADING → stop                |
           |       ├─ GENERAL → stop                   |
           |       └─ TEXT_TO_SQL → continue            |
           |                                          |
           |  SEARCHING                               |
           |    [schemas already retrieved above]      |
           |                                          |
           |  GENERATING                              |
           |    1. LLM generates SQL                  |
           |    2. PostgreSQL EXPLAIN validates        |
           |       ├─ valid → FINISHED                 |
           |       └─ invalid → CORRECTING             |
           |                                          |
           |  CORRECTING (up to 3 retries)            |
           |    1. LLM diagnoses + fixes SQL          |
           |    2. Re-validate                        |
           |       ├─ valid → FINISHED                 |
           |       └─ invalid → retry or FAILED        |
           |                                          |
           +--------------------+--------------------+
                                |
                    +-----------v-----------+
                    |   Execute SQL on PG    |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    |  AnswerGenerator (LLM) |
                    |  Summarize results in  |
                    |  plain English         |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    |  Return to User:       |
                    |  - NL Answer           |
                    |  - SQL                  |
                    |  - Tables used          |
                    +------------------------+
```

---

## 2. Technology Stack

| Component        | Technology                        | Purpose                                    |
|------------------|-----------------------------------|--------------------------------------------|
| LLM              | Groq `llama-3.3-70b-versatile`    | SQL generation, intent classification, correction, NL answers |
| Embeddings       | Ollama `nomic-embed-text` (768d)  | Semantic search for table/question matching |
| Vector Store     | FAISS (`IndexFlatIP`)             | Similarity search with L2-normalized inner product |
| Target Database  | PostgreSQL via `asyncpg`          | Execute queries, validate SQL (EXPLAIN)    |
| API Framework    | FastAPI                           | REST endpoints with async lifespan         |
| Schema Validation| Pydantic v2                       | MDL models, API request/response models    |
| Template Engine  | Jinja2                            | LLM prompt templates                       |
| Caching          | `cachetools.TTLCache`             | Query results, indexing status              |
| Serialization    | `orjson`                          | Fast JSON parsing for LLM responses        |

---

## 3. Project Structure

```
NL2SQL/
├── config.py                    # All settings (Pydantic BaseSettings, reads .env)
├── main.py                      # FastAPI app + lifespan wiring
├── run.py                       # Interactive CLI (standalone engine)
│
├── mdl/
│   └── schema.py                # Pydantic models: MDL, Model, Column, Relationship, etc.
│
├── indexing/
│   ├── store.py                 # FAISSStore, FAISSStoreManager, Document dataclass
│   ├── chunkers.py              # DDLChunker, TableDescriptionChunker, ViewChunker, SqlPairsConverter
│   └── pipeline.py              # IndexingPipeline (5 parallel sub-pipelines)
│
├── retrieval/
│   ├── db_schema.py             # 2-phase table discovery + schema fetch
│   ├── historical.py            # Historical question matching (views)
│   ├── sql_pairs.py             # SQL example retrieval
│   └── instructions.py          # User instruction retrieval
│
├── generation/
│   ├── prompts.py               # All Jinja2 prompt templates
│   ├── intent.py                # IntentClassifier (TEXT_TO_SQL | MISLEADING | GENERAL)
│   ├── sql_gen.py               # SQLGenerator
│   ├── sql_correction.py        # SQLCorrector + SQLValidator
│   └── answer.py                # AnswerGenerator (SQL results → NL answer)
│
├── services/
│   ├── ask.py                   # AskService (main pipeline state machine)
│   ├── semantics.py             # SemanticsPreparationService (MDL indexing lifecycle)
│   └── sql_answer.py            # SqlAnswerService (execute SQL + generate NL answer)
│
├── api/
│   ├── models.py                # Pydantic request/response models
│   └── routes.py                # FastAPI router (9 endpoints)
│
├── utils/
│   └── helpers.py               # DDL builders, score_filter, text cleaners
│
├── faiss_indices/               # Persisted FAISS indices (.faiss + .meta.pkl)
└── .env                         # Environment variables
```

---

## 4. Configuration

**File:** `config.py`

All settings live in one `Settings` class (Pydantic `BaseSettings`), reading from `.env`.

```
# .env example
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSION=768
PG_HOST=localhost
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=postgres
PG_DATABASE=northwind
FAISS_PERSIST_DIR=./faiss_indices
LOG_LEVEL=INFO
```

**Pipeline thresholds (with defaults):**

| Setting                                    | Default | What it controls                                    |
|--------------------------------------------|---------|-----------------------------------------------------|
| `column_indexing_batch_size`               | 50      | Max columns per DDL chunk document                  |
| `table_retrieval_size`                     | 10      | Top-K tables in Phase 1 discovery                   |
| `table_column_retrieval_size`              | 100     | Over-fetch limit for column retrieval               |
| `historical_question_similarity_threshold` | 0.9     | Exact question match (very strict)                  |
| `sql_pairs_similarity_threshold`           | 0.7     | SQL example similarity                              |
| `sql_pairs_retrieval_max_size`             | 10      | Max SQL examples returned                           |
| `instructions_similarity_threshold`        | 0.7     | User instructions similarity                        |
| `instructions_retrieval_max_size`          | 10      | Max instructions returned                           |
| `max_sql_correction_retries`              | 3       | SQL validation+correction retry loops               |
| `ask_cache_maxsize`                        | 1000000 | TTLCache max entries                                |
| `ask_cache_ttl`                            | 120     | Cache time-to-live (seconds)                        |

---

## 5. MDL — Model Definition Language

**File:** `mdl/schema.py`

MDL is the schema representation format ported from WrenAI. It describes the database in a technology-agnostic way.

```
MDL (root)
├── catalog: str                    e.g. "postgresql"
├── schema: str                     e.g. "public"
├── dataSource: str                 e.g. "postgresql"
├── models: list[Model]             One per database table
│   ├── name: str                   Table name
│   ├── tableReference: str         e.g. "public.orders"
│   ├── primaryKey: str             PK column name
│   ├── columns: list[Column]
│   │   ├── name, type              Column basics
│   │   ├── isCalculated: bool      Computed column flag
│   │   ├── isHidden: bool          Hidden from DDL
│   │   ├── expression: str         Calculation expression
│   │   └── relationship: str       FK relationship ref
│   └── properties: dict            displayName, description
├── relationships: list[Relationship]
│   ├── name: str                   FK constraint name
│   ├── models: [source, target]    Two table names
│   ├── joinType: JoinType          ONE_TO_ONE | ONE_TO_MANY | MANY_TO_ONE | MANY_TO_MANY
│   └── condition: str              e.g. "orders.customer_id = customers.customer_id"
├── metrics: list[Metric]           OLAP-style aggregations
│   ├── baseObject, dimensions, measures
└── views: list[View]               Pre-defined queries
    ├── statement: str              SQL statement
    └── properties: dict            question, summary, historical_queries
```

**Where MDL comes from:**
- **`run.py --mdl path/to/mdl.json`** — Load from file
- **`run.py` (no args)** — Auto-introspects PostgreSQL via `information_schema` queries, builds MDL using `mdl/schema.py` models, saves to `auto_mdl.json`
- **`POST /v1/semantics-preparations`** — Client sends MDL JSON via API

---

## 6. Vector Store Layer

**File:** `indexing/store.py`

### Document Dataclass

```python
@dataclass
class Document:
    content: str                       # Text content (DDL chunk, description, question, etc.)
    meta: dict[str, Any]               # Metadata (type, name, project_id, ...)
    id: str                            # UUID
    embedding: Optional[list[float]]   # 768-dim vector (None for metadata-only docs)
    score: float                       # Similarity score [0.0, 1.0] (set during search)
```

### FAISSStore

One FAISS index per collection. Manages two document lists:

1. **Embedded documents** — In FAISS index + `_documents` list (position-synced)
2. **Metadata-only documents** — In `_meta_only_documents` (filter-searchable only)

**Key algorithms:**

- **Indexing:** Vectors are **L2-normalized** before insertion → inner product becomes cosine similarity
- **Search:** Raw FAISS scores are in `[-1, 1]` → normalized to `[0, 1]` via `(score + 1) / 2`
- **Over-fetching:** Fetches `top_k * 5` from FAISS, then applies metadata filter, returns best `top_k`
- **Persistence:** `.faiss` binary (FAISS index) + `.meta.pkl` (pickled document lists)
- **Deletion:** Rebuilds entire FAISS index excluding filtered docs (no incremental delete)

### FAISSStoreManager

Manages 6 named collections:

| Collection           | Content                                    | Has Embeddings? |
|----------------------|--------------------------------------------|-----------------|
| `db_schema`          | DDL chunks (TABLE, TABLE_COLUMNS, FK, METRIC, VIEW) | Yes |
| `table_descriptions` | `{name, description, columns}` per table   | Yes             |
| `view_questions`     | Historical queries for view matching        | Yes             |
| `sql_pairs`          | Question → SQL examples                    | Yes             |
| `instructions`       | User-defined SQL generation rules           | Yes             |
| `project_meta`       | Project metadata                           | No              |

---

## 7. Indexing Pipeline — What Happens When a New DB Comes

**Files:** `indexing/pipeline.py`, `indexing/chunkers.py`

When a new database is connected (or MDL is provided), the indexing pipeline converts the schema into searchable vector documents.

### Step-by-step flow

```
MDL JSON string
    │
    ▼
Parse JSON → dict
    │
    ▼
Run 5 sub-pipelines in parallel (asyncio.gather):
    │
    ├── 1. _index_db_schema ─────────────────────────────────────────
    │      DDLChunker converts MDL into documents:
    │
    │      Per table:
    │        • TABLE_COLUMNS chunks — columns batched by 50
    │          Content: {"type": "TABLE_COLUMNS", "columns": [
    │            {"type": "COLUMN", "name": "order_id", "data_type": "INTEGER",
    │             "is_primary_key": true, "comment": "-- {'description': '...'}\n  "},
    │            {"type": "FOREIGN_KEY", "constraint": "FOREIGN KEY (customer_id)
    │              REFERENCES customers(customer_id)", "comment": "-- {...}\n  "}
    │          ]}
    │        • TABLE chunk — table-level metadata
    │          Content: {"type": "TABLE", "name": "orders",
    │           "comment": "\n/* {'alias': 'Orders', 'description': '...'} */\n"}
    │
    │      Per metric:
    │        • METRIC chunk with dimensions + measures
    │
    │      Per view:
    │        • VIEW chunk with SQL statement
    │
    │      → Embed all chunks via Ollama nomic-embed-text
    │      → Store in FAISS "db_schema" collection
    │
    ├── 2. _index_table_descriptions ────────────────────────────────
    │      TableDescriptionChunker creates 1 doc per table/metric/view:
    │        Content: "{'name': 'orders', 'description': 'Customer orders',
    │                   'columns': 'order_id, customer_id, order_date, ...'}"
    │
    │      → Embed → Store in FAISS "table_descriptions" collection
    │      (This is what Phase 1 of retrieval searches against)
    │
    ├── 3. _index_view_questions ────────────────────────────────────
    │      ViewChunker creates 1 doc per view:
    │        Content: "historical_query_1 historical_query_2 question"
    │        Meta: {summary, statement (SQL), viewId}
    │
    │      → Embed → Store in FAISS "view_questions" collection
    │      (For exact question matching at 0.9 threshold)
    │
    ├── 4. _index_sql_pairs ─────────────────────────────────────────
    │      SqlPairsConverter creates 1 doc per example:
    │        Content: question text
    │        Meta: {sql, sql_pair_id}
    │
    │      → Embed → Store in FAISS "sql_pairs" collection
    │      (For in-context SQL examples at 0.7 threshold)
    │
    └── 5. _index_project_meta ──────────────────────────────────────
           Creates 1 metadata-only document:
             Meta: {project_id, data_source}
           (No embedding — filter-only)

    │
    ▼
Save all indices to disk (faiss_indices/*.faiss + *.meta.pkl)
```

### How embedding works

1. Each document's `.content` string is sent to Ollama `nomic-embed-text`
2. Returns a 768-dimensional float vector
3. Vector is L2-normalized: `faiss.normalize_L2(vectors)` — this makes inner product = cosine similarity
4. Stored in `faiss.IndexFlatIP` (inner product index, exact search)

### How chunking works per table

For a table with 120 columns and 3 foreign keys:

```
Table "orders" (120 columns, 3 FKs)
    │
    ├── TABLE_COLUMNS chunk 1: columns[0:50] + FK constraints  → 1 document, 1 embedding
    ├── TABLE_COLUMNS chunk 2: columns[50:100]                 → 1 document, 1 embedding
    ├── TABLE_COLUMNS chunk 3: columns[100:120]                → 1 document, 1 embedding
    └── TABLE chunk: {alias, description}                       → 1 document, 1 embedding
```

Column batch size (50) keeps each chunk within reasonable embedding context. Hidden columns and relationship-only columns are excluded.

---

## 8. Retrieval Pipelines

**Files:** `retrieval/db_schema.py`, `retrieval/historical.py`, `retrieval/sql_pairs.py`, `retrieval/instructions.py`

### 8.1 DBSchemaRetrieval — 2-Phase Retrieval

**Phase 1: Table Discovery** (semantic search)

```
User query: "What are the top products by revenue?"
    │
    ▼
Embed query via Ollama
    │
    ▼
Search "table_descriptions" collection:
  - Find tables whose {name, description, columns} are semantically similar
  - top_k = 10 (configurable: table_retrieval_size)
  - Filter by project_id if provided
    │
    ▼
Result: ["products", "order_details", "orders", "categories", ...]
```

**Phase 2: Schema Fetch** (metadata filter — no embedding)

```
Discovered tables: ["products", "order_details", "orders"]
    │
    ▼
Filter "db_schema" collection by:
  - meta.type == "TABLE_SCHEMA"
  - meta.name IN discovered_tables
  - meta.project_id matches (if set)
    │
    ▼
Merge TABLE + TABLE_COLUMNS documents per table
    │
    ▼
Build DDL strings via utils/helpers.py:
  build_table_ddl() → "CREATE TABLE products (\n  product_id INTEGER PRIMARY KEY, ..."
  build_metric_ddl() → for metrics
  build_view_ddl()   → for views
    │
    ▼
Output:
{
  "retrieval_results": [
    {"table_name": "products", "table_ddl": "CREATE TABLE products (...)"},
    {"table_name": "order_details", "table_ddl": "CREATE TABLE order_details (...)"},
  ],
  "has_calculated_field": false,
  "has_metric": false,
  "has_json_field": false,
}
```

### 8.2 HistoricalQuestionRetrieval

Checks if someone has asked an almost identical question before (from views).

- Searches `view_questions` collection
- Threshold: **0.9** (very strict — near-exact match only)
- Returns max 1 result with the cached SQL

### 8.3 SqlPairsRetrieval

Fetches relevant question→SQL examples for in-context learning.

- Searches `sql_pairs` collection
- Threshold: **0.7**, max **10** results
- These become `### SQL SAMPLES ###` in the LLM prompt

### 8.4 InstructionsRetrieval

Fetches user-defined rules (e.g., "always use fiscal_year instead of calendar year").

- Searches `instructions` collection
- Threshold: **0.7**, max **10** results
- These become `### USER INSTRUCTIONS ###` in the LLM prompt

---

## 9. Generation Pipeline

**Files:** `generation/intent.py`, `generation/sql_gen.py`, `generation/sql_correction.py`, `generation/answer.py`, `generation/prompts.py`

### 9.1 IntentClassifier

Classifies the user query into one of three intents:

| Intent             | Meaning                                    | What happens next        |
|--------------------|--------------------------------------------|--------------------------|
| `TEXT_TO_SQL`      | Query needs SQL generation                 | Continue pipeline        |
| `MISLEADING_QUERY` | Off-topic, unrelated to database           | Stop, return explanation |
| `GENERAL`          | Related but too vague for SQL              | Stop, ask for more info  |

The LLM also produces:
- `rephrased_question` — Rewrites follow-up questions into standalone queries using conversation history
- `reasoning` — Max 20 words explaining the classification

### 9.2 SQLGenerator

Converts the natural language query into SQL.

**What goes into the prompt:**
- Full DDL of retrieved tables (from Phase 2)
- SQL pair examples (from SqlPairsRetrieval)
- User instructions (from InstructionsRetrieval)
- Calculated field annotations (if present)
- Metric usage instructions (if present)
- The (possibly rephrased) user question

**Key SQL rules enforced via prompt:**
- SELECT only — no DML
- Double-quoted identifiers: `"table"."column"`
- Case-insensitive comparisons: `LOWER(col) = LOWER(val)` or `LOWER(col) LIKE LOWER(val)`
- CTEs preferred over subqueries
- JOIN required for multi-table queries
- DENSE_RANK for ranking problems
- No FILTER(WHERE), EXTRACT(EPOCH), INTERVAL, TO_CHAR

**Output format:** JSON `{"sql": "SELECT ..."}`

### 9.3 SQLValidator

Validates SQL by running `EXPLAIN <sql>` against PostgreSQL.

- If EXPLAIN succeeds → SQL is syntactically and semantically valid
- If EXPLAIN fails → returns the error message (e.g., `column "product_nam" does not exist`)
- No actual data is fetched — just a query plan check

### 9.4 SQLCorrector

Fixes invalid SQL using the LLM.

**What goes into the prompt:**
- The invalid SQL
- The error message from PostgreSQL EXPLAIN
- Database schemas (DDL)
- User instructions

The correction loop runs up to `max_sql_correction_retries` (default 3) times.

### 9.5 AnswerGenerator

Generates a natural-language summary from query + SQL + result data.

**Data truncation strategy (handles 100+ rows efficiently):**

1. Hard cap at `max_rows` (default 50)
2. Token budget of ~24,000 chars (~6,000 tokens) — stops adding rows when budget is exceeded
3. When data is truncated, the prompt tells the LLM: *"The data shown is a sample of N rows out of M total"*

```python
# Example: 500 rows returned from DB
rows = [...]  # 500 rows
truncated, num_used = _truncate_rows(rows, columns, max_rows=50)
# Result: maybe 42 rows that fit in 24k chars
# Prompt includes: "sample of 42 rows out of 500 total rows"
```

**LLM prompt instructs:**
- Answer in Markdown for non-technical users
- No SQL terminology
- List format: show top few, mention others are omitted
- Same language as user's question

---

## 10. Service Layer

### 10.1 AskService (`services/ask.py`)

The main orchestrator. Implements a state machine:

```
UNDERSTANDING → SEARCHING → GENERATING → CORRECTING → FINISHED/FAILED
```

**Detailed pipeline:**

```python
async def _run_pipeline(query_id, query, project_id, histories):

    # ── UNDERSTANDING ──

    # 1. Historical question match (exact match from views)
    historical = await historical_retrieval.run(query)
    if historical:
        return FINISHED  # Return cached SQL from view

    # 2. Parallel retrieval
    sql_samples, instructions = await asyncio.gather(
        sql_pairs_retrieval.run(query),
        instructions_retrieval.run(query, scope="sql"),
    )

    # 3. DB schema retrieval (2-phase)
    retrieval_result = await db_schema_retrieval.run(query, histories=histories)
    table_ddls = [doc["table_ddl"] for doc in retrieval_result["retrieval_results"]]
    table_names = [doc["table_name"] for doc in retrieval_result["retrieval_results"]]

    # 4. Intent classification
    intent_result = await intent_classifier.run(query, db_schemas=table_ddls, ...)
    if intent == "MISLEADING_QUERY": return FINISHED
    if intent == "GENERAL": return FINISHED

    # ── SEARCHING ── (tables already retrieved)

    if not table_ddls:
        return FAILED  # NO_RELEVANT_DATA

    # ── GENERATING ──

    gen_result = await sql_generator.run(
        query=rephrased_question, contexts=table_ddls,
        sql_samples=sql_samples, instructions=instructions,
    )
    sql = gen_result["sql"]

    is_valid, error = await sql_validator.validate(sql)
    if is_valid:
        return FINISHED

    # ── CORRECTING ── (up to 3 retries)

    for retry in range(max_retries):
        correction = await sql_corrector.run(
            invalid_sql=sql, error=error, contexts=table_ddls,
        )
        is_valid, error = await sql_validator.validate(correction["sql"])
        if is_valid:
            return FINISHED

    return FAILED  # All retries exhausted
```

**Caching:** Results stored in `TTLCache` keyed by `query_id`, expire after 120s.

### 10.2 SemanticsPreparationService (`services/semantics.py`)

Manages the MDL indexing lifecycle:

- `prepare(mdl_json, mdl_hash)` — Runs `IndexingPipeline.run()`, tracks status
- `get_status(mdl_hash)` — Returns `"indexing"` | `"finished"` | `"failed"`
- `delete(project_id)` — Deletes all docs for a project from all 6 stores

### 10.3 SqlAnswerService (`services/sql_answer.py`)

Orchestrates SQL execution + NL answer generation:

- Accepts `(query, sql)` and optionally pre-computed `sql_data`
- If no `sql_data` provided: executes SQL against PostgreSQL
- Passes results to `AnswerGenerator`
- Runs as background task, results polled by `query_id`

---

## 11. API Layer

**Files:** `api/routes.py`, `api/models.py`

### Endpoints

| Method   | Path                                       | Purpose                                    |
|----------|--------------------------------------------|--------------------------------------------|
| `GET`    | `/health`                                  | Health check                               |
| `POST`   | `/v1/asks`                                | Submit NL question (background)            |
| `GET`    | `/v1/asks/{query_id}/result`               | Poll for SQL result                        |
| `PATCH`  | `/v1/asks/{query_id}`                      | Stop a running query                       |
| `POST`   | `/v1/sql-answers`                         | Submit SQL answer generation (background)  |
| `GET`    | `/v1/sql-answers/{query_id}/result`        | Poll for NL answer                         |
| `POST`   | `/v1/semantics-preparations`              | Submit MDL for indexing (background)       |
| `GET`    | `/v1/semantics-preparations/{hash}/status` | Poll indexing status                       |
| `DELETE` | `/v1/semantics`                            | Delete indexed data for a project          |

### Usage pattern (async polling)

```
1. POST /v1/asks  {"query": "top 5 products by revenue"}
   → {"query_id": "abc-123"}

2. GET /v1/asks/abc-123/result  (poll)
   → {"status": "generating", ...}

3. GET /v1/asks/abc-123/result  (poll again)
   → {"status": "finished", "response": [{"sql": "SELECT ..."}], ...}

4. POST /v1/sql-answers  {"query": "...", "sql": "SELECT ..."}
   → {"query_id": "def-456"}

5. GET /v1/sql-answers/def-456/result
   → {"status": "finished", "answer": "The top 5 products by revenue are...", ...}
```

---

## 12. FastAPI App Wiring

**File:** `main.py`

The `lifespan` async context manager initializes everything on startup:

```
Startup:
  Settings()
    → ChatGroq(model, api_key, temperature=0)
    → OllamaEmbeddings(base_url, model)
    → FAISSStoreManager(dimension=768) → load_all()
    → asyncpg.create_pool(min=2, max=10)
    → IndexingPipeline(store_manager, embeddings, batch_size=50)
    → HistoricalQuestionRetrieval(threshold=0.9)
    → SqlPairsRetrieval(threshold=0.7, max=10)
    → InstructionsRetrieval(threshold=0.7, max=10)
    → DBSchemaRetrieval(table_retrieval_size=10)
    → IntentClassifier(llm)
    → SQLGenerator(llm)
    → SQLCorrector(llm)
    → SQLValidator(pg_pool)
    → AnswerGenerator(llm)
    → AskService(all retrieval + generation components)
    → SemanticsPreparationService(indexing_pipeline, store_manager)
    → SqlAnswerService(answer_generator, pg_pool)
    → Attach all to app.state.*

Shutdown:
  store_manager.save_all()
  pg_pool.close()
```

---

## 13. Interactive CLI

**File:** `run.py`

`NL2SQLEngine` mirrors `main.py`'s wiring but adds:

### Startup

```
[1/5] Initializing LLM (Groq)...
[2/5] Initializing Embeddings (Ollama)...         # Tests connection
[3/5] Initializing FAISS stores...                 # Loads from disk
[4/5] Connecting to PostgreSQL...                  # Tests connection
[5/5] Wiring services...                           # Same as main.py
```

### Database auto-introspection (when no --mdl flag)

If no existing index is found, `run.py` introspects PostgreSQL:

1. Queries `information_schema.tables` → get all tables
2. Queries `information_schema.columns` → get columns per table
3. Queries `information_schema.table_constraints` → get PKs and FKs
4. Maps PG types → MDL types via `PG_TYPE_MAP` (e.g., `character varying` → `VARCHAR`)
5. Builds `MDL` using `mdl/schema.py` Pydantic models
6. Saves `auto_mdl.json` for future reuse
7. Runs the full indexing pipeline

### Interactive commands

| Command    | Action                                          |
|------------|-------------------------------------------------|
| `exit`     | Quit                                            |
| `reindex`  | Re-introspect DB and rebuild all indices        |
| `run`      | Execute the last generated SQL, show raw results|
| `tables`   | List all indexed tables with descriptions       |
| `mdl`      | Show current MDL structure                      |
| `history`  | Show conversation history (for follow-ups)      |
| `clear`    | Clear conversation history                      |
| *(text)*   | Ask a natural language question                 |

### Question flow in run.py

```
Question> what are the top 5 products by revenue?

  Thinking...
  ↓
  AskService.ask() → poll until finished
  ↓
  Execute SQL against PostgreSQL → get (columns, rows)
  ↓
  AnswerGenerator.run(query, sql, columns, rows) → NL answer
  ↓
  Done in 3.2s

  Answer:
  ──────────────────────────────────────────────────
  Based on the data, here are the top 5 products by revenue:

  - **Cote de Blaye** — $141,396.74
  - **Thuringer Rostbratwurst** — $80,368.67
  ...
  ──────────────────────────────────────────────────

  SQL:
  --------------------------------------------------
  SELECT p.product_name, SUM(od.unit_price * od.quantity) ...
  --------------------------------------------------
  Tables used: products, order_details

  Type 'run' to see raw query results.
```

Conversation history is tracked so follow-up questions work:
```
Question> now show me the bottom 5
```
The history `[{question, sql}, ...]` is passed to `IntentClassifier` which rephrases it: *"What are the bottom 5 products by revenue?"*

---

## 14. Full Query Flow — End to End

Here is what happens from the moment a user types a question to the final answer, with every file touched:

```
User types: "what are the top 5 products by revenue?"
│
│  ┌─── run.py ───────────────────────────────────────────────────────
│  │ engine.ask(question) called
│  │   → services/ask.py: AskService.ask()
│  │     → generates query_id, spawns background task
│  │
│  │ ── UNDERSTANDING ────────────────────────────────────────────────
│  │
│  │ 1. retrieval/historical.py: HistoricalQuestionRetrieval.run()
│  │    → Embed query via Ollama
│  │    → Search "view_questions" FAISS store
│  │    → Score filter at 0.9 threshold
│  │    → No match found → continue
│  │
│  │ 2. Parallel:
│  │    retrieval/sql_pairs.py: SqlPairsRetrieval.run()
│  │      → Embed query → search "sql_pairs" → filter at 0.7
│  │      → Returns: [{question: "...", sql: "..."}]
│  │
│  │    retrieval/instructions.py: InstructionsRetrieval.run()
│  │      → Embed query → search "instructions" → filter at 0.7
│  │      → Returns: [] (none configured)
│  │
│  │ 3. retrieval/db_schema.py: DBSchemaRetrieval.run()
│  │    PHASE 1 — Table Discovery:
│  │      → Embed query → search "table_descriptions"
│  │      → Discovers: ["products", "order_details", "orders"]
│  │    PHASE 2 — Schema Fetch:
│  │      → Filter "db_schema" by discovered table names
│  │      → Merge TABLE + TABLE_COLUMNS docs per table
│  │      → utils/helpers.py: build_table_ddl()
│  │      → Returns DDL strings for each table
│  │
│  │ 4. generation/intent.py: IntentClassifier.run()
│  │    → generation/prompts.py: INTENT_CLASSIFICATION_*
│  │    → Jinja2 renders prompt with schemas + history + query
│  │    → Groq LLM → JSON response
│  │    → Intent: "TEXT_TO_SQL"
│  │    → Rephrased: "What are the top 5 products by revenue?"
│  │
│  │ ── GENERATING ───────────────────────────────────────────────────
│  │
│  │ 5. generation/sql_gen.py: SQLGenerator.run()
│  │    → generation/prompts.py: SQL_GENERATION_*
│  │    → Jinja2 renders prompt with DDLs + samples + instructions + query
│  │    → Groq LLM → JSON {"sql": "SELECT ..."}
│  │    → utils/helpers.py: clean_generation_result()
│  │
│  │ 6. generation/sql_correction.py: SQLValidator.validate()
│  │    → Runs: EXPLAIN SELECT p.product_name, SUM(od.unit_price * od.quantity) ...
│  │    → PostgreSQL says: OK
│  │    → is_valid = True
│  │
│  │ ── FINISHED ─────────────────────────────────────────────────────
│  │
│  │ Result cached in TTLCache[query_id]
│  │ run.py polls get_result() → status="finished"
│  │
│  │ ── NL ANSWER GENERATION ─────────────────────────────────────────
│  │
│  │ 7. run.py: engine.execute_sql(sql)
│  │    → asyncpg: prepare + fetch
│  │    → Returns: (["product_name", "revenue"], [{...}, {...}, ...])
│  │
│  │ 8. generation/answer.py: AnswerGenerator.run()
│  │    → _truncate_rows(): keeps rows within 24k char budget
│  │    → generation/prompts.py: SQL_ANSWER_*
│  │    → Jinja2 renders prompt with query + sql + columns + rows
│  │    → Groq LLM → Markdown answer
│  │
│  │ 9. run.py: print_answer() + print_sql()
│  └──────────────────────────────────────────────────────────────────
│
▼
User sees: NL Answer + SQL + Tables used
```

---

## 15. Misspelling & Typo Handling

**WrenAI has ZERO explicit spell-check logic.** Our system inherits the same approach. There is no fuzzy matching, Levenshtein distance, or autocorrect anywhere in the pipeline.

However, several **implicit mechanisms** provide partial tolerance:

### What works

| Misspelling Type               | Example                             | Handled By                            | Reliability |
|--------------------------------|--------------------------------------|---------------------------------------|-------------|
| Table name in user query       | "show me custmers"                  | Embedding similarity (Phase 1 search) | Medium-High |
| Column name in user query      | "what is the produt name"           | LLM sees correct DDL, maps to closest | High        |
| Table/column name in SQL output| `SELECT * FROM "custmers"`          | EXPLAIN fails → correction loop fixes | High        |

**Why these work:**
- **Embedding similarity:** The embedding model (`nomic-embed-text`) produces similar vectors for misspelled words. "custmers" and "customers" will have high cosine similarity, so Phase 1 table discovery still finds the right tables.
- **LLM intelligence:** The LLM receives the full correct DDL in its prompt. When it sees `CREATE TABLE customers (...)` and the user says "custmers", it maps to the right table.
- **SQL correction loop:** If the LLM somehow misspells a table/column in the generated SQL, PostgreSQL EXPLAIN catches it with an error like `relation "custmers" does not exist`, and the corrector fixes it.

### What does NOT work

| Misspelling Type               | Example                             | Problem                                |
|--------------------------------|--------------------------------------|----------------------------------------|
| Entity/data values in query    | "sales for Chaii" (should be "Chai")| SQL generates `WHERE product = 'Chaii'` — valid SQL, returns 0 rows, correction loop never triggers |

**The `LOWER(col) LIKE LOWER(val)` rule helps with case** but not with spelling errors in values. The SQL `WHERE LOWER(product_name) = LOWER('Chaii')` is valid SQL that returns nothing.

### Potential improvements (not yet implemented)

1. **PostgreSQL trigram similarity:** Use `pg_trgm` extension for fuzzy value matching
2. **ILIKE with wildcards:** Instruct LLM to use `ILIKE '%chai%'` for partial matches
3. **Empty-result detection:** If SQL returns 0 rows, retry with relaxed matching
4. **Value embedding index:** Index database values (product names, etc.) in FAISS for entity resolution

---

## 16. Key Thresholds & Tuning

| Threshold | Current | Effect of raising | Effect of lowering |
|-----------|---------|-------------------|--------------------|
| `historical_question_similarity_threshold` | 0.9 | Fewer cache hits, more LLM calls | More false matches, wrong SQL returned |
| `sql_pairs_similarity_threshold` | 0.7 | Fewer examples in prompt | Irrelevant examples may confuse LLM |
| `instructions_similarity_threshold` | 0.7 | Fewer instructions applied | Irrelevant instructions may conflict |
| `table_retrieval_size` | 10 | More tables = more context for LLM | May miss relevant tables |
| `max_sql_correction_retries` | 3 | More chances to fix SQL, higher latency | Faster failure, less accuracy |
| `column_indexing_batch_size` | 50 | Fewer chunks, less granular search | More chunks, slower indexing |

**Performance notes:**
- Ollama embedding is the indexing bottleneck (sequential per document batch)
- Groq LLM calls dominate query latency (1-3s each, up to 6 calls: intent + SQL gen + up to 3 corrections + answer)
- FAISS search is near-instant for typical doc counts (<100k)
- PostgreSQL EXPLAIN validation is fast (~5ms)

---

## 17. Extending the System

### Add a new database

1. Update `.env` with new PG connection details
2. Run `python run.py` — auto-introspects and indexes
3. Or provide `--mdl path/to/custom_mdl.json` for fine-tuned schema descriptions

### Add custom SQL rules

Add instructions to the `instructions` FAISS store (via API or MDL sql_pairs). These get injected into the LLM prompt as `### USER INSTRUCTIONS ###`.

### Swap the LLM

Replace `ChatGroq` with any LangChain-compatible LLM in `config.py` and `main.py`. The system uses standard `ainvoke([messages])` and `response_format={"type": "json_object"}` — ensure your LLM supports JSON mode.

### Swap the embedding model

1. Change `OLLAMA_EMBEDDING_MODEL` and `EMBEDDING_DIMENSION` in `.env`
2. Delete `faiss_indices/` directory
3. Re-index (embeddings are model-specific, old indices are incompatible)

### Add a new retrieval source

1. Create a new chunker in `indexing/chunkers.py`
2. Add a new collection name in `indexing/store.py:COLLECTION_NAMES`
3. Add a sub-pipeline in `indexing/pipeline.py`
4. Create a retriever in `retrieval/`
5. Wire into `AskService._run_pipeline()`
