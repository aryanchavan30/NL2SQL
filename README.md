# NL2SQL

Ask your database questions in plain English. Get back SQL, results, and a human-readable answer.

```
Question> what are the top 5 products by revenue?

  Done in 3.2s

  Answer:
  ──────────────────────────────────────────────────
  Based on the data, here are the top 5 products by revenue:

  - **Cote de Blaye** — $141,396.74
  - **Thuringer Rostbratwurst** — $80,368.67
  - **Raclette Courdavault** — $71,155.70
  - **Camembert Pierrot** — $46,825.48
  - **Tarte au sucre** — $41,919.60

  These products represent the highest revenue generators in the catalog.
  ──────────────────────────────────────────────────

  SQL:
  --------------------------------------------------
  SELECT p."product_name", SUM(od."unit_price" * od."quantity") AS revenue
  FROM "products" p
  JOIN "order_details" od ON p."product_id" = od."product_id"
  GROUP BY p."product_name"
  ORDER BY revenue DESC
  LIMIT 5
  --------------------------------------------------

  Retrieved Schema:
  ==================================================
  CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    product_name VARCHAR,
    ...
  );
  --------------------------------------------------
  CREATE TABLE order_details (
    ...
    FOREIGN KEY (product_id) REFERENCES products(product_id)
  );
  --------------------------------------------------

  Tables used: products, order_details

  Type 'run' to see raw query results.
```

---

## How It Works

```
User Question
     |
     v
[1] Intent Classification  ── Is this a valid data question?
     |                          - TEXT_TO_SQL  -> continue
     |                          - MISLEADING   -> reject
     |                          - GENERAL      -> ask for clarity
     v
[2] Schema Retrieval        ── Which tables are relevant?
     |                          Phase 1: Embed question, search table descriptions (FAISS)
     |                          Phase 2: Fetch full DDL for matching tables
     v
[3] SQL Generation          ── LLM generates SQL with schema + examples in prompt
     |
     v
[4] Validation              ── Validate via database adapter (EXPLAIN, dry-run, etc.)
     |                          - Valid   -> done
     |                          - Invalid -> correction loop (up to 3x)
     v
[5] SQL Execution           ── Run query, get rows
     |
     v
[6] NL Answer               ── LLM summarizes results in plain English (Markdown)
     |
     v
User sees: Answer + SQL + Schema used + Tables
```

---

## Prerequisites

| Service      | What                                           | Setup                                                |
|--------------|------------------------------------------------|------------------------------------------------------|
| **LLM**          | One of: Groq, OpenAI, Azure OpenAI, or Ollama  | API providers: get an API key; Ollama: install + `ollama pull llama3.2` |
| **Embeddings**   | One of: Ollama (local), OpenAI, or Azure OpenAI | Ollama: install + `ollama pull nomic-embed-text`; OpenAI/Azure: API key |
| **Vector Store** | FAISS (default) or Qdrant                      | FAISS: zero setup; Qdrant: `docker run -d -p 6333:6333 qdrant/qdrant` |
| Database         | Target database to query                       | PostgreSQL, MySQL, MSSQL, Snowflake, BigQuery, or Databricks |
| Python           | 3.12+                                          | [python.org](https://www.python.org/downloads/)      |

**Default providers:** Groq (LLM) + Ollama (embeddings) + FAISS (vector store). Set `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, and `VECTOR_STORE_PROVIDER` in `.env` to switch.

---

## Quick Start

### 1. Clone and install

```bash
cd NL2SQL
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
# LLM provider: groq (default) | openai | azure_openai
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here

# Embedding provider: ollama (default) | openai | azure_openai
EMBEDDING_PROVIDER=ollama
EMBEDDING_DIMENSION=768

# Database (adjust to your DB)
DB_TYPE=postgresql          # postgresql | mysql | mssql | snowflake | bigquery | databricks
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres
DB_DATABASE=northwind
DB_SCHEMA=public

# For cloud databases, use a full connection string instead:
# DB_CONNECTION_STRING=snowflake://user:pass@account/db/schema?warehouse=WH&role=ROLE
```

See `.env.example` for all provider-specific variables (OpenAI, Azure OpenAI, etc.).

> **Note:** Legacy `PG_*` env vars (`PG_HOST`, `PG_PORT`, etc.) still work as fallback for PostgreSQL.

### 3. Start Ollama (if using Ollama embeddings)

```bash
ollama serve
# In another terminal:
ollama pull nomic-embed-text
```

> Skip this step if using `EMBEDDING_PROVIDER=openai` or `EMBEDDING_PROVIDER=azure_openai`.

### 4. Run

**Interactive CLI (recommended for trying it out):**

```bash
python run.py
```

On first run, it auto-introspects your database, builds the schema (MDL), enriches table/column descriptions via the LLM, and indexes everything. Then you can ask questions.

**API server:**

```bash
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Server starts at `http://localhost:8000`. Swagger docs at `/docs`.

---

## Interactive CLI (`run.py`)

### Startup modes

```bash
# Auto-introspect database (first run or no cached index)
python run.py

# Load a specific MDL schema file
python run.py --mdl path/to/mdl.json

# Reuses cached index from previous run (if faiss_indices/ exists)
python run.py
```

### Commands

| Command    | Description                                      |
|------------|--------------------------------------------------|
| *(text)*   | Ask a natural language question                  |
| `run`      | Execute the last generated SQL, show raw results |
| `reindex`  | Re-introspect the database and rebuild index     |
| `tables`   | Show all indexed tables with descriptions        |
| `mdl`      | Show the current MDL schema summary              |
| `history`  | Show conversation history (for follow-ups)       |
| `clear`    | Clear conversation history                       |
| `addpair`  | Interactively add a question→SQL example pair    |
| `pairs`    | List all stored SQL pair examples                |
| `exit`     | Quit                                             |

### Follow-up questions

The CLI tracks conversation history, so follow-up questions work naturally:

```
Question> what are the top 5 products by revenue?
  ...

Question> now show me the bottom 5
  (Automatically rephrased to: "What are the bottom 5 products by revenue?")
  ...
```

### What you see per question

1. **NL Answer** — Human-readable summary in Markdown
2. **SQL** — The generated query
3. **Retrieved Schema** — The DDL the LLM received as context
4. **Tables used** — Which tables were involved
5. **Rephrased question** — If the intent classifier rewrote your question

---

## API Reference

All endpoints use JSON. Async operations return a `query_id` for polling.

### Health Check

```
GET /health
```
```json
{"status": "ok"}
```

### Ask — Natural Language to SQL

**Submit a question:**

```
POST /v1/asks
```
```json
{
  "query": "What are the top 5 products by revenue?",
  "project_id": "default",
  "histories": [
    {
      "question": "show all products",
      "sql": "SELECT * FROM products",
      "answer": "There are 77 products in total."
    }
  ]
}
```
```json
{"query_id": "abc-123"}
```

**Poll for result:**

```
GET /v1/asks/{query_id}/result
```
```json
{
  "status": "finished",
  "type": "TEXT_TO_SQL",
  "rephrased_question": "What are the top 5 products by revenue?",
  "intent_reasoning": "User asks for product ranking by revenue",
  "retrieved_tables": ["products", "order_details"],
  "response": [
    {"sql": "SELECT ...", "type": "llm"}
  ],
  "error": null
}
```

Status transitions: `understanding` -> `searching` -> `generating` -> `correcting` -> `finished` / `failed`

**Stop a running query:**

```
PATCH /v1/asks/{query_id}
```

### SQL Answer — Generate NL Summary

**Submit SQL for answer generation:**

```
POST /v1/sql-answers
```
```json
{
  "query": "What are the top 5 products by revenue?",
  "sql": "SELECT p.product_name, SUM(od.unit_price * od.quantity) AS revenue FROM ...",
  "project_id": "default"
}
```

Optionally pass pre-fetched data to skip server-side SQL execution:

```json
{
  "query": "...",
  "sql": "...",
  "sql_data": {
    "columns": ["product_name", "revenue"],
    "data": [
      {"product_name": "Cote de Blaye", "revenue": 141396.74},
      {"product_name": "Thuringer Rostbratwurst", "revenue": 80368.67}
    ]
  }
}
```

**Poll for answer:**

```
GET /v1/sql-answers/{query_id}/result
```
```json
{
  "status": "finished",
  "answer": "Based on the data, the top 5 products by revenue are:\n\n- **Cote de Blaye** — $141,396.74\n...",
  "num_rows_used": 5,
  "total_rows": 5,
  "error": null
}
```

### Semantics Preparation — Index a Schema

**Submit MDL for indexing:**

```
POST /v1/semantics-preparations
```
```json
{
  "mdl": "{\"models\": [...], \"relationships\": [...], ...}",
  "mdl_hash": "abc123",
  "project_id": "default",
  "sql_pairs": [
    {"question": "total revenue", "sql": "SELECT SUM(amount) FROM orders"}
  ]
}
```

**Poll indexing status:**

```
GET /v1/semantics-preparations/{mdl_hash}/status
```
```json
{"status": "finished"}
```

**Delete indexed data:**

```
DELETE /v1/semantics
```
```json
{"project_id": "default"}
```

---

## Configuration Reference

All settings are in `config.py` and read from `.env`. Every setting has a sensible default.

### Provider Settings

| Variable                 | Default                     | Description                     |
|--------------------------|-----------------------------|---------------------------------|
| `LLM_PROVIDER`           | `groq`                      | LLM provider: `groq`, `openai`, `azure_openai`, `ollama` |
| `LLM_MODE`               | `api`                       | `local` = concurrent MDL enrichment (4 tables at once, for Ollama/local models); `api` = sequential (for Groq/OpenAI/Azure, avoids rate limits) |
| `EMBEDDING_PROVIDER`     | `ollama`                    | Embedding provider: `ollama`, `openai`, `azure_openai` |
| `EMBEDDING_DIMENSION`    | `768`                       | Vector dimensionality (768 for nomic-embed-text, 1536 for OpenAI text-embedding-3-small) |
| `VECTOR_STORE_PROVIDER`  | `faiss`                     | Vector store: `faiss` (local files) or `qdrant` (Docker/cloud server) |

### Groq Settings (when `LLM_PROVIDER=groq`)

| Variable        | Default                    | Description        |
|-----------------|----------------------------|--------------------|
| `GROQ_API_KEY`  | *(required)*               | Groq API key       |
| `GROQ_MODEL`    | `llama-3.3-70b-versatile`  | Groq model name    |

### Ollama Settings (when `LLM_PROVIDER=ollama` or `EMBEDDING_PROVIDER=ollama`)

| Variable                 | Default                   | Description              |
|--------------------------|---------------------------|--------------------------|
| `OLLAMA_BASE_URL`        | `http://localhost:11434`  | Ollama server URL        |
| `OLLAMA_LLM_MODEL`       | `llama3.2`                | LLM model name (when `LLM_PROVIDER=ollama`). Uses Ollama's OpenAI-compatible endpoint internally. |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text`        | Embedding model name (when `EMBEDDING_PROVIDER=ollama`) |

### Qdrant Settings (when `VECTOR_STORE_PROVIDER=qdrant`)

| Variable          | Default                    | Description                              |
|-------------------|----------------------------|------------------------------------------|
| `QDRANT_URL`      | `http://localhost:6333`    | Qdrant server URL                        |
| `QDRANT_API_KEY`  | *(empty)*                  | Qdrant API key (leave empty for local Docker) |

### OpenAI Settings (when `LLM_PROVIDER=openai` or `EMBEDDING_PROVIDER=openai`)

| Variable                 | Default                     | Description                |
|--------------------------|-----------------------------|----------------------------|
| `OPENAI_API_KEY`         | *(required)*                | OpenAI API key             |
| `OPENAI_MODEL`           | `gpt-4o`                    | Chat model name            |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small`    | Embedding model name       |

### Azure OpenAI Settings (when provider is `azure_openai`)

| Variable                           | Default       | Description                 |
|------------------------------------|---------------|-----------------------------|
| `AZURE_OPENAI_API_KEY`             | *(required)*  | Azure OpenAI API key        |
| `AZURE_OPENAI_ENDPOINT`            | *(required)*  | Azure OpenAI endpoint URL   |
| `AZURE_OPENAI_API_VERSION`         | `2024-06-01`  | API version                 |
| `AZURE_OPENAI_LLM_DEPLOYMENT`      | *(required)*  | LLM deployment name         |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | *(required)* | Embedding deployment name   |

### Database Settings

| Variable               | Default           | Description                     |
|------------------------|-------------------|---------------------------------|
| `DB_TYPE`              | `postgresql`      | Database type (`postgresql`, `mysql`, `mssql`, `snowflake`, `bigquery`, `databricks`) |
| `DB_HOST`              | `localhost`       | Database host                   |
| `DB_PORT`              | `5432`            | Database port                   |
| `DB_USER`              | `postgres`        | Database user                   |
| `DB_PASSWORD`          | `postgres`        | Database password               |
| `DB_DATABASE`          | `northwind`       | Database name                   |
| `DB_SCHEMA`            | `public`          | Schema name (e.g., `public`, `dbo`) |
| `DB_CONNECTION_STRING` | *(empty)*         | Full connection string override (required for Snowflake/BigQuery/Databricks) |
| `FAISS_PERSIST_DIR`    | `./faiss_indices` | Directory for persisted indices |
| `LOG_LEVEL`            | `INFO`            | Logging level                   |

> **Legacy:** `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DATABASE` still work as fallback when `DB_*` vars are not set.
>
> **Switching embedding providers:** Different models produce different vector dimensions. When changing `EMBEDDING_PROVIDER`, update `EMBEDDING_DIMENSION` and delete `faiss_indices/` to re-index.

### Pipeline Behavior

| Variable          | Default   | Description |
|-------------------|-----------|-------------|
| `INTENT_OVERRIDE` | *(empty)* | Set to `SQL` to force all queries through as `TEXT_TO_SQL`. The LLM still runs for question rephrasing; only the intent classification result is overridden. Useful when your application is purely data-query focused and you never want `MISLEADING_QUERY` / `GENERAL` rejections. |

### Pipeline Tuning

| Variable                                    | Default | Description                                       |
|---------------------------------------------|---------|---------------------------------------------------|
| `COLUMN_INDEXING_BATCH_SIZE`               | `15`    | Max columns per DDL chunk                         |
| `TABLE_RETRIEVAL_SIZE`                     | `10`    | Top-K tables retrieved in semantic search         |
| `TABLE_COLUMN_RETRIEVAL_SIZE`              | `100`   | Over-fetch limit for column-level retrieval       |
| `HISTORICAL_QUESTION_SIMILARITY_THRESHOLD` | `0.9`   | Threshold for exact question matching             |
| `SQL_PAIRS_SIMILARITY_THRESHOLD`           | `0.7`   | Threshold for SQL example retrieval               |
| `SQL_PAIRS_RETRIEVAL_MAX_SIZE`             | `10`    | Max SQL examples in prompt                        |
| `INSTRUCTIONS_SIMILARITY_THRESHOLD`        | `0.7`   | Threshold for user instruction retrieval          |
| `INSTRUCTIONS_RETRIEVAL_MAX_SIZE`          | `10`    | Max instructions in prompt                        |
| `MAX_SQL_CORRECTION_RETRIES`              | `3`     | Max SQL validation + correction attempts          |
| `ASK_CACHE_MAXSIZE`                        | `1000000` | Max cached query results                        |
| `ASK_CACHE_TTL`                            | `120`   | Cache expiry in seconds                           |

---

## Project Structure

```
NL2SQL/
├── config.py                  Settings (Pydantic BaseSettings, reads .env)
├── providers.py               LLM + Embedding + StoreManager factory functions
├── main.py                    FastAPI app + lifespan wiring
├── run.py                     Interactive CLI
├── requirements.txt           Python dependencies
│
├── mdl/
│   ├── schema.py              MDL Pydantic models (Model, Column, Relationship, etc.)
│   ├── adapter.py             DatabaseAdapter ABC, SyncDatabaseAdapter, create_adapter() factory
│   ├── enrichment.py          LLM-powered description enrichment for tables and columns
│   └── adapters/
│       ├── postgresql.py      PostgreSQL (asyncpg, EXPLAIN validation)
│       ├── mysql.py           MySQL (aiomysql, EXPLAIN validation)
│       ├── mssql.py           MSSQL (aioodbc, SET NOEXEC validation)
│       ├── snowflake.py       Snowflake (sync driver, EXPLAIN validation)
│       ├── bigquery.py        BigQuery (sync driver, dry-run validation)
│       └── databricks.py      Databricks (sync driver, EXPLAIN validation)
│
├── indexing/
│   ├── store.py               FAISSStore, FAISSStoreManager, Document dataclass
│   ├── qdrant_store.py        QdrantStore, QdrantStoreManager (drop-in Qdrant backend)
│   ├── chunkers.py            DDLChunker, TableDescriptionChunker, ViewChunker, SqlPairsConverter
│   └── pipeline.py            IndexingPipeline (5 parallel sub-pipelines)
│
├── retrieval/
│   ├── db_schema.py           2-phase schema retrieval (table discovery + DDL fetch)
│   ├── historical.py          Historical question matching from views
│   ├── sql_pairs.py           SQL example retrieval for in-context learning
│   └── instructions.py        User instruction retrieval
│
├── generation/
│   ├── prompts.py             All Jinja2 prompt templates
│   ├── intent.py              Intent classifier (TEXT_TO_SQL / MISLEADING / GENERAL)
│   ├── sql_gen.py             SQL generator
│   ├── sql_correction.py      SQL corrector + validator (adapter-based)
│   └── answer.py              NL answer generator (SQL results -> plain English)
│
├── services/
│   ├── ask.py                 AskService — main pipeline state machine
│   ├── semantics.py           SemanticsPreparationService — MDL indexing lifecycle
│   └── sql_answer.py          SqlAnswerService — SQL execution + NL answer
│
├── api/
│   ├── models.py              Pydantic request/response models
│   └── routes.py              FastAPI router (9 endpoints)
│
├── utils/
│   └── helpers.py             DDL builders, score filtering, text cleaners
│
├── faiss_indices/             Persisted FAISS indices (auto-created)
└── ARCHITECTURE.md            Detailed architecture documentation
```

---

## Connecting a Database

The system supports **6 database types**: PostgreSQL, MySQL, MSSQL, Snowflake, BigQuery, and Databricks.

### Option A: Auto-introspection (easiest)

1. Set `DB_TYPE` and connection details in `.env`:
   ```env
   # PostgreSQL example
   DB_TYPE=postgresql
   DB_HOST=localhost
   DB_PORT=5432
   DB_USER=postgres
   DB_PASSWORD=postgres
   DB_DATABASE=northwind
   DB_SCHEMA=public

   # MySQL example
   DB_TYPE=mysql
   DB_HOST=localhost
   DB_PORT=3306
   DB_USER=root
   DB_PASSWORD=secret
   DB_DATABASE=northwind
   DB_SCHEMA=northwind    # MySQL uses database name as schema

   # Snowflake example (requires full connection string)
   DB_TYPE=snowflake
   DB_CONNECTION_STRING=snowflake://user:pass@account/db/schema?warehouse=WH&role=ROLE
   DB_SCHEMA=PUBLIC
   ```
2. Delete `faiss_indices/` if switching databases
3. Run `python run.py` — it introspects the database automatically, then **enriches** all table and column descriptions via the LLM
4. Generates `mdl/<db_database>_mdl.json` (e.g., `mdl/northwind_mdl.json`) with enriched descriptions — you can edit and reload later

#### What happens during auto-introspection

```
1. Adapter queries information_schema (or equivalent) for tables, columns, PKs, FKs
2. Builds raw MDL with generic descriptions ("Table orders", empty column descriptions)
3. LLM enrichment: for each table, fetches 5 sample rows, asks the LLM to generate
   meaningful business-context descriptions for the table and every column
4. Saves enriched MDL to mdl/<db_database>_mdl.json
5. Indexes the enriched MDL into FAISS for semantic search
```

This enrichment step dramatically improves retrieval quality — e.g., "highest selling product" correctly retrieves `products` + `order_details` instead of 10 irrelevant tables.

### Option B: Custom MDL file

Create a JSON file describing your schema:

```json
{
  "catalog": "postgresql",
  "schema": "public",
  "dataSource": "postgresql",
  "models": [
    {
      "name": "users",
      "tableReference": "public.users",
      "primaryKey": "id",
      "columns": [
        {"name": "id", "type": "INTEGER", "properties": {"description": "Unique user identifier"}},
        {"name": "email", "type": "VARCHAR", "properties": {"description": "User email address"}},
        {"name": "created_at", "type": "TIMESTAMP", "properties": {"description": "Account creation timestamp"}}
      ],
      "properties": {
        "displayName": "Users",
        "description": "Application users and their accounts"
      }
    }
  ],
  "relationships": [
    {
      "name": "fk_orders_users",
      "models": ["orders", "users"],
      "joinType": "MANY_TO_ONE",
      "condition": "orders.user_id = users.id"
    }
  ],
  "metrics": [],
  "views": []
}
```

Good descriptions in `properties.description` (on both models and columns) significantly improve retrieval accuracy. When using auto-introspection, these are generated automatically by the LLM. When providing a custom MDL, write them yourself.

Load it:

```bash
python run.py --mdl my_schema.json
```

### Option C: Via API

```bash
# Index schema
curl -X POST http://localhost:8000/v1/semantics-preparations \
  -H "Content-Type: application/json" \
  -d '{"mdl": "{...}", "mdl_hash": "abc", "project_id": "myproject"}'

# Check status
curl http://localhost:8000/v1/semantics-preparations/abc/status

# Ask questions
curl -X POST http://localhost:8000/v1/asks \
  -H "Content-Type: application/json" \
  -d '{"query": "how many users signed up last month?", "project_id": "myproject"}'
```

---

## How Indexing Works

When a schema is indexed, the pipeline creates 6 vector collections:

| Collection           | What gets indexed                                               | Used for                          |
|----------------------|-----------------------------------------------------------------|-----------------------------------|
| `db_schema`          | DDL chunks — columns (batched by 15), FKs, table metadata      | Phase 2: fetch full DDL           |
| `table_descriptions` | One doc per table: `{name, description, columns}`               | Phase 1: semantic table discovery |
| `view_questions`     | Historical queries from views                                   | Exact question matching (0.9)     |
| `sql_pairs`          | Question → SQL examples                                         | In-context learning (0.7)         |
| `instructions`       | User-defined SQL generation rules                               | Custom rules in prompts           |
| `project_meta`       | Project metadata (no embeddings)                                | Multi-tenant filtering            |

Each document's text content is embedded via the configured embedding provider (default: Ollama `nomic-embed-text`, 768 dimensions) and stored in the configured vector store.

**FAISS** (`VECTOR_STORE_PROVIDER=faiss`): L2-normalized inner product index, persisted to `.faiss` + `.meta.pkl` files in `faiss_indices/`.

**Qdrant** (`VECTOR_STORE_PROVIDER=qdrant`): Cosine similarity, persisted server-side. Start with:
```bash
docker run -d -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant
```

Set `EMBEDDING_DIMENSION` to match your model (768 for nomic-embed-text, 1536 for OpenAI text-embedding-3-small). When changing `VECTOR_STORE_PROVIDER` or `EMBEDDING_PROVIDER`, re-index from scratch.

---

## How Query Answering Works

### Pipeline stages

**1. Historical Match** (short-circuit)
- Search `view_questions` for near-identical questions (threshold 0.9)
- If found, return cached SQL immediately — no LLM call needed

**2. Context Retrieval** (parallel)
- SQL pairs retrieval — find relevant question->SQL examples (threshold 0.7)
- Instructions retrieval — find relevant user rules (threshold 0.7)
- DB schema retrieval — 2-phase:
  - *Phase 1:* Embed user question, search `table_descriptions` by cosine similarity, get top 10 table names
  - *Phase 2:* Fetch all DDL chunks for those tables from `db_schema` by metadata filter

**3. Intent Classification**
- LLM classifies the question given the schema context
- `TEXT_TO_SQL` — proceed to SQL generation
- `MISLEADING_QUERY` — question is off-topic, stop
- `GENERAL` — question is too vague, stop
- Also rephrases follow-up questions into standalone queries using conversation history (question + NL answer + SQL per turn)
- Set `INTENT_OVERRIDE=SQL` to force `TEXT_TO_SQL` while still running the LLM for rephrasing

**4. SQL Generation**
- LLM generates SQL with the full prompt: DDL + SQL examples + instructions + question
- Enforces rules: SELECT only, quoted identifiers, case-insensitive comparisons, CTEs over subqueries, etc.

**5. Validation + Correction**
- Validates SQL via the database adapter (EXPLAIN for PostgreSQL/MySQL/Snowflake/Databricks, SET NOEXEC for MSSQL, dry-run for BigQuery)
- If valid, done
- If invalid, LLM corrects the SQL using the error message
- Retries up to 3 times

**6. NL Answer Generation**
- Executes the valid SQL on the database
- Truncates results to fit token budget (~6,000 tokens for data, max 50 rows)
- LLM summarizes results in plain English Markdown for non-technical users

---

## Handling Large Results

When SQL returns many rows (100+), the `AnswerGenerator` uses a dual truncation strategy:

1. **Row cap** — Max 50 rows sent to the LLM
2. **Token budget** — Stops adding rows when estimated data exceeds ~24,000 characters (~6,000 tokens)

When truncation occurs:
- The LLM prompt notes: *"This is a sample of N rows out of M total"*
- The CLI output shows: `(Based on N of M rows)`
- The API response includes `num_rows_used` and `total_rows`

The LLM is instructed to summarize based on the sample and mention that more results exist.

---

## Troubleshooting

### "Cannot connect to embedding provider"

**Ollama** (`EMBEDDING_PROVIDER=ollama`):
```bash
ollama serve
ollama pull nomic-embed-text
curl http://localhost:11434/api/embeddings -d '{"model": "nomic-embed-text", "prompt": "test"}'
```

**OpenAI / Azure OpenAI**: Check that your API key is valid and the model/deployment exists.

### "Cannot connect to database"

Check your `.env` matches your DB setup. For PostgreSQL:

```bash
psql -h localhost -p 5432 -U postgres -d northwind -c "SELECT 1"
```

For other databases, verify `DB_TYPE`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_DATABASE` (or `DB_CONNECTION_STRING` for cloud DBs).

### Wrong tables retrieved

The system finds tables by semantic similarity of their descriptions. With auto-introspection, the LLM enrichment step generates descriptions automatically, which usually gives good results.

If you still need to tune, edit `mdl/<db_database>_mdl.json` and improve descriptions:

```json
{
  "name": "order_details",
  "properties": {
    "displayName": "Order Details",
    "description": "Line items for each order, including product, quantity, unit price, and discount. Join with orders and products for revenue analysis."
  }
}
```

Then reload: `python run.py --mdl mdl/northwind_mdl.json`

### Cannot connect to Qdrant

```bash
# Check container is running
docker ps --filter "publish=6333"

# Start if stopped
docker start qdrant

# Verify
curl http://localhost:6333/healthz
```

If using `VECTOR_STORE_PROVIDER=qdrant` and Qdrant is down, the service won't start.

### SQL generation errors

If the LLM generates invalid SQL repeatedly:
- Check that `table_retrieval_size` (default 10) is high enough — increase it if your query spans many tables
- Add SQL pair examples to your MDL for common query patterns
- Add user instructions for database-specific conventions

### Stale index

If your database schema changed:

```
Question> reindex
```

Or for a full reset:
- **FAISS:** delete `faiss_indices/` and restart
- **Qdrant:** delete collections via `http://localhost:6333/dashboard` or restart with fresh storage

---

## Architecture Deep Dive

See [ARCHITECTURE.md](ARCHITECTURE.md) for:

- Component-level data flow diagrams
- Every file explained with classes, methods, and algorithms
- FAISS internals (L2 normalization, over-fetching, persistence)
- Chunking strategy details
- Prompt template documentation
- Threshold tuning guide
- Misspelling/typo analysis
- Extension guide (swap LLM, swap embeddings, add retrieval sources)
