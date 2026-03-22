# Architecture

**Analysis Date:** 2026-03-22

## Pattern Overview

**Overall:** Distributed multi-agent prediction system with client-server architecture

**Key Characteristics:**
- **Windows Agent (JV-Link SDK)**: Data acquisition from JRA-VAN via Windows COM interface (Parallels VM)
- **Mac Backend (FastAPI)**: Index calculation engines (14 agents) + REST API + state management
- **PostgreSQL (VPS)**: Persistent storage of race data, entries, results, and calculated indices
- **Command Queue Pattern**: Windows Agent polls FastAPI for commands; reports status asynchronously
- **Index Agent Pattern**: Each index calculation (speed, aptitude, jockey, pace, etc.) is an independent Agent inheriting from `IndexCalculator`

## Layers

**Windows Agent (Data Acquisition):**
- Purpose: Extract JRA-VAN raw data via JV-Link SDK, cache locally, POST to Mac backend
- Location: `windows-agent/jvlink_agent.py`
- Contains: COM interface calls (`pywin32`), JVRead loops, local JSONL caching, HTTP retry queue
- Depends on: JV-Link SDK, .env config (JRAVAN_SID, BACKEND_URL)
- Used by: Triggered by Mac-side command queue (`GET /api/agent/command`)

**Backend HTTP Layer (FastAPI):**
- Purpose: Receive data from Windows Agent, coordinate index calculation, expose REST API
- Location: `backend/src/main.py`
- Contains: CORS middleware, health endpoint, three routers (import, changes, races, agent)
- Depends on: SQLAlchemy Session, index calculators, importers
- Routes:
  - `POST /api/import/*`: Receive RA/SE/odds data from Windows Agent
  - `POST /api/changes/notify`: Detect race entry changes (scratch, jockey change)
  - `GET /api/agent/command`: Agent polling for commands
  - `POST /api/agent/status`: Agent status reporting
  - `GET /api/races/*`: Query race/entry/result data

**Data Import Layer:**
- Purpose: Parse JV-Link raw strings into DB inserts/updates (idempotent)
- Location: `backend/src/importers/`
  - `jvlink_parser.py`: Parse RA/SE/O1-O5/AV/JC binary strings with SJIS encoding
  - `race_importer.py`: UPSERT Race, RaceEntry, RaceResult into PostgreSQL
  - `odds_importer.py`: UPSERT OddsHistory
  - `change_handler.py`: Handle race entry changes, trigger recalculation
- Depends on: SQLAlchemy dialects.postgresql.insert (UPSERT), models
- Used by: import_router (`POST /api/import/races`, etc.)

**Index Calculation Agents:**
- Purpose: Calculate 9 index types independently; composite agent combines them
- Location: `backend/src/indices/`
  - `base.py`: Abstract `IndexCalculator` base class
  - `speed.py`: Speed index (0.30 weight) - past race timing comparison
  - `last3f.py`: Last 3F index (0.12 weight) - finishing acceleration
  - `course_aptitude.py`: Course aptitude (0.13 weight) - course/distance/surface match
  - `frame_bias.py`: Frame/position bias (0.06 weight) - post/placement advantage
  - `jockey.py`: Jockey/trainer index (0.08 weight) - rider performance
  - `pace.py`: Pace index (0.08 weight) - race tempo/position flow
  - `rotation.py`: Rotation index (0.05 weight) - recent performance trend
  - `composite.py`: Weighted sum agent (combines above + defaults for unimplemented)
- Depends on: IndexCalculator base, database queries (Race, RaceEntry, RaceResult)
- Used by: agent_router (`POST /api/agent/calculate`)

**Database Layer:**
- Purpose: Persistent storage with schema `keiba` on VPS PostgreSQL
- Location: `backend/src/db/`
  - `models.py`: 13 SQLAlchemy models (Horse, Jockey, Race, RaceEntry, RaceResult, CalculatedIndex, etc.)
  - `session.py`: Engine, SessionLocal factory, Alembic integration
- Schema migrations: `backend/alembic/versions/`
- Tables: horses, jockeys, trainers, races, race_entries, race_results, calculated_indices, odds_history, entry_changes, etc.

**Configuration:**
- Location: `backend/src/config.py`
- Reads from `.env` via Pydantic BaseSettings
- Key settings: db_* (PostgreSQL connection), api_* (port/host), bet_* (safety limits), jravan_sid

## Data Flow

**Initial Data Intake (Setup Mode):**

1. Mac operator: `POST /api/agent/command {"action": "setup"}`
2. Windows Agent: `GET /api/agent/command` polls, receives setup action
3. Agent: `JVOpen(option=3)` retrieves historical data (RA/SE records)
4. Agent: Parses with `jvlink_parser.parse_ra()` / `parse_se()`
5. Agent: `POST /api/import/races` sends {records: [{...}]}
6. FastAPI: `RaceImporter.import_records()` UPSERTs Race, RaceEntry, RaceResult
7. Result: PostgreSQL keiba schema populated

**Daily Data Intake:**

1. Windows Agent: `jvlink_agent.py --mode daily` runs
2. Agent: `JVOpen(option=1/2)` retrieves today's RA/SE
3. Agent: Parses and POSTs same way as setup
4. Concurrent: JVRTOpen fetches odds, scratches, jockey changes
5. Agent: POSTs changes via `POST /api/changes/notify` (triggers recalc)

**Index Calculation Flow:**

1. Data arrives and is imported
2. Composite agent is triggered (manual or auto)
3. CompositeIndexCalculator iterates over all horse_ids in race
4. For each agent type (speed, aptitude, etc.):
   - Single-horse: `calculate(race_id, horse_id)` queries history
   - Batch: `calculate_batch(race_id)` returns {horse_id: index_value}
5. Composite: Weighted sum with `INDEX_WEIGHTS` dict
6. UPSERT into `calculated_indices` table with version tracking

**Real-time Change Detection:**

1. Windows Agent detects via JVRTOpen (scratch/jockey change)
2. Agent: `POST /api/changes/notify` with change_type and raw data
3. ChangeHandler: Parses and identifies affected race_id/horse_id
4. ChangeHandler: Marks entry_changes and triggers selective recalc
   - Scratch: Recalc all horses in race
   - Jockey change: Recalc jockey + pace indices
   - Weight change: Recalc speed index only

**State Management:**

- Agent status: In-memory dict in `agent_router.py` (updated via `POST /api/agent/status`)
- Command queue: In-memory deque (FIFO) in `agent_router.py`
- Index versions: Tracked in `calculated_indices.version` for re-runs
- Cache: Windows Agent stores raw JVRead output in local JSONL (pending queue for retry)

## Key Abstractions

**IndexCalculator (Abstract Base):**
- Purpose: Standardize all index agents with single interface
- Location: `backend/src/indices/base.py`
- Methods:
  - `calculate(race_id: int, horse_id: int) -> float`: Single horse
  - `calculate_batch(race_id: int) -> dict[int, float]`: All horses in race
  - `recalculate(race_id: int, version: int)`: Re-run with version tracking
- Examples: `SpeedIndexCalculator`, `CourseAptitudeCalculator`, `FrameBiasCalculator`
- Pattern: Each agent queries database, applies proprietary algorithm, returns 0-100 index score

**Importer Pattern:**
- Purpose: Make data intake idempotent (duplicate-safe)
- Examples: `RaceImporter.import_records()`, `OddsImporter.import_records()`
- Implementation: SQLAlchemy `insert().on_conflict_do_update()` with jravan_*_id uniqueness
- Result: Safe to re-run multiple times without corrupting data

**JV-Link Parser Pattern:**
- Purpose: Convert raw SJIS binary strings to Python dicts (field extraction)
- Location: `backend/src/importers/jvlink_parser.py`
- Conventions:
  - Helper functions: `_s()` (string), `_i()` (int), `_decode()` (SJIS→UTF-8)
  - Byte positions: 1-indexed per JVDF spec, converted to 0-indexed Python slicing
  - Time fields: MSST (4B: M+SS+T) and SST (3B: SS+T) → 0.1sec-unit integers
  - Returns: `dict[str, Any]` with extracted fields, None if invalid

## Entry Points

**Windows Agent:**
- Location: `windows-agent/jvlink_agent.py --mode {setup|daily|realtime|retry}`
- Triggers: Manual invocation or via `/api/agent/command` polling
- Responsibilities: JVRead loops, local caching, HTTP POST to FastAPI, retry queue management

**FastAPI Server:**
- Location: `backend/src/main.py` + `uvicorn` via Docker or direct
- Triggers: HTTP server startup
- Responsibilities: Route incoming data, coordinate agents, expose REST API

**Index Calculation (On-Demand):**
- Location: `backend/src/api/agent_router.py` → `CompositeIndexCalculator`
- Triggers: Manual CLI call or scheduled (APScheduler planned for MS3+)
- Responsibilities: Invoke all 9 agents, aggregate indices, persist results

## Error Handling

**Strategy:** Graceful degradation with logging; never crash on bad data

**Patterns:**

- **Parser errors**: Return `None` if record is malformed; skip silently
  ```python
  def parse_ra(data: str) -> dict[str, Any] | None:
      if len(data) < MIN_LENGTH:
          return None  # Skip bad record
  ```

- **Database errors**: UPSERT on conflict (idempotent); log duplicates
  ```python
  insert_stmt = insert(Race).values(...).on_conflict_do_update(...)
  ```

- **Missing data**: Return neutral value (50.0) for unimplemented indices
  ```python
  # In CompositeIndexCalculator: pedigree_index = 50.0  # Not yet implemented
  ```

- **API errors**: Return HTTP status codes (401/400/500) with descriptive messages

## Cross-Cutting Concerns

**Logging:**
- Framework: Python `logging` module
- Handlers: Console + file (`logs/kiseki.log`)
- Configuration: `backend/src/config.py` (log_level, log_file)

**Validation:**
- Pydantic models for API input (CommandRequest, AgentStatusReport, etc.)
- Type hints throughout (Python strict mode ready)
- Database constraints: UNIQUE, FOREIGN KEY, CHECK via SQLAlchemy

**Authentication:**
- Simple header-based: `X-API-Key` for Windows Agent ↔ Mac communication
- Fallback: Development mode (empty key) skips check
- Location: `backend/src/api/import_router.py`, `agent_router.py` (verify_api_key)

**Database Schema Management:**
- Tool: Alembic migrations
- Location: `backend/alembic/versions/`
- Workflow: Always DDL via `alembic upgrade head`; never hand-edit schema

---

*Architecture analysis: 2026-03-22*
