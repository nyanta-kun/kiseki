# Codebase Structure

**Analysis Date:** 2026-03-22

## Directory Layout

```
kiseki/
├── backend/                         # Python FastAPI backend
│   ├── src/
│   │   ├── main.py                  # FastAPI app entry point
│   │   ├── config.py                # Pydantic settings (.env loader)
│   │   ├── __init__.py
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── agent_router.py      # Windows Agent command/status endpoints
│   │   │   ├── import_router.py     # Data import endpoints (RA/SE/odds)
│   │   │   └── races.py             # Race/entry/result query endpoints
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── models.py            # SQLAlchemy ORM models (13 tables)
│   │   │   └── session.py           # SQLAlchemy engine & session factory
│   │   ├── importers/
│   │   │   ├── __init__.py
│   │   │   ├── jvlink_parser.py     # RA/SE/odds/AV/JC binary string parser
│   │   │   ├── race_importer.py     # UPSERT Race/RaceEntry/RaceResult
│   │   │   ├── odds_importer.py     # UPSERT OddsHistory
│   │   │   └── change_handler.py    # Handle scratch/jockey change events
│   │   ├── indices/
│   │   │   ├── __init__.py          # Barrel exports
│   │   │   ├── base.py              # Abstract IndexCalculator
│   │   │   ├── speed.py             # SpeedIndexCalculator (weight: 0.30)
│   │   │   ├── last3f.py            # Last3FIndexCalculator (weight: 0.12)
│   │   │   ├── course_aptitude.py   # CourseAptitudeCalculator (weight: 0.13)
│   │   │   ├── frame_bias.py        # FrameBiasCalculator (weight: 0.06)
│   │   │   ├── jockey.py            # JockeyIndexCalculator (weight: 0.08)
│   │   │   ├── pace.py              # PaceIndexCalculator (weight: 0.08)
│   │   │   ├── rotation.py          # RotationIndexCalculator (weight: 0.05)
│   │   │   └── composite.py         # CompositeIndexCalculator (weighted sum)
│   │   ├── utils/
│   │   │   ├── __init__.py
│   │   │   └── constants.py         # Global constants (weights, indices, limits)
│   │   └── betting/                 # (Empty, MS7+)
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_speed_index.py
│   │   ├── test_course_aptitude.py
│   │   ├── test_frame_bias.py
│   │   ├── test_jockey_index.py
│   │   ├── test_last3f_index.py
│   │   ├── test_pace_index.py
│   │   ├── test_rotation_index.py
│   │   └── conftest.py              # (If present) pytest fixtures
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   ├── versions/
│   │   │   └── 0001_initial_schema.py   # Create 13 core tables
│   │   └── alembic.ini
│   ├── scripts/                     # Helper scripts (e.g., CSV export)
│   ├── pyproject.toml               # uv package manifest (Python 3.12+)
│   ├── Dockerfile                   # Alpine-based Docker image
│   └── .pytest_cache/               # (Generated)
│
├── windows-agent/                   # Python 32-bit Windows Agent
│   ├── jvlink_agent.py              # Main agent (JVRead loops, HTTP POST)
│   ├── jv_status.py                 # Debug helper (check JV-Link status)
│   ├── jv_close.py                  # Debug helper (close JV-Link handles)
│   ├── fix_jvlink_303.py            # Repair script for rc=-303 errors
│   ├── debug_fields.py              # Debug helpers for field inspection
│   ├── debug_grade.py
│   ├── debug_ra.py
│   ├── debug_ra2.py
│   ├── debug_ra3.py
│   ├── requirements.txt              # Windows-only deps (pywin32, requests, etc.)
│   ├── start_agent.bat               # Batch file to launch agent
│   ├── SETUP_WINDOWS.md              # Setup instructions for Windows
│   └── data/                         # (Local Windows directories)
│       ├── cache/                    # JSONL cache of raw JVRead output
│       ├── pending/                  # Retry queue (failed POSTs)
│       └── completed/                # Processing completion log
│
├── frontend/                         # (Empty, MS6+) Next.js 14 PWA
│   └── (Placeholder)
│
├── data/
│   ├── raw/                         # External data sources (unused yet)
│   └── processed/                   # Analysis outputs (unused yet)
│
├── docs/
│   └── sources/                     # Documentation sources
│
├── scripts/                         # Top-level helper scripts
│   └── parallels_proxy.py            # (Placeholder for Parallels integration)
│
├── docker-compose.yml               # Multi-container orchestration
├── .env                             # Environment variables (NOT committed)
├── .env.example                     # Example config template
├── CLAUDE.md                        # Project instructions & rules
├── README.md                         # (If present)
└── .git/                            # Git repository
```

## Directory Purposes

**backend/src/:**
- Purpose: All Python backend code (FastAPI server, importers, agents)
- Structure: Functional modules (api, db, importers, indices, utils, betting)
- Config: Entry `backend/src/main.py` instantiates FastAPI app

**backend/src/api/:**
- Purpose: HTTP routers and request/response models
- Key files:
  - `agent_router.py` (165 lines): Command queue & status polling for Windows Agent
  - `import_router.py` (100+ lines): Receive RA/SE/odds from Windows Agent
  - `races.py` (100+ lines): Query race/entry/result data
- Pattern: Each router is a separate file; imported in main.py via `include_router()`

**backend/src/db/:**
- Purpose: Database layer (models, session management, migrations)
- Key files:
  - `models.py` (223+ lines): 13 SQLAlchemy ORM models in keiba schema
  - `session.py` (25 lines): Engine creation, SessionLocal factory, Base class
- Alembic: Migrations in `backend/alembic/versions/`

**backend/src/importers/:**
- Purpose: Parse raw JV-Link data and insert into database
- Key files:
  - `jvlink_parser.py` (500+ lines): Parse RA/SE/odds/AV/JC with SJIS handling
  - `race_importer.py` (200+ lines): UPSERT Race/RaceEntry/RaceResult
  - `odds_importer.py` (200+ lines): UPSERT OddsHistory
  - `change_handler.py` (150+ lines): Detect and handle race entry changes
- Pattern: One class per concept; reusable by API routes

**backend/src/indices/:**
- Purpose: Independent index calculation agents
- Key files:
  - `base.py` (27 lines): Abstract IndexCalculator base class
  - `speed.py` (200+ lines): Speed index (queries RaceResult for past races)
  - `last3f.py`, `course_aptitude.py`, `frame_bias.py`, `jockey.py`, `pace.py`, `rotation.py` (each 200-400 lines)
  - `composite.py` (250+ lines): Orchestrates all agents, weighted sum, persist to DB
- Pattern: Each agent is independent, testable, inherits IndexCalculator

**backend/src/utils/:**
- Purpose: Shared constants and utilities
- Key files:
  - `constants.py` (3K+ lines): INDEX_WEIGHTS, SPEED_INDEX_MEAN/STD, weight correction factors, track codes

**backend/tests/:**
- Purpose: Unit tests for index calculation logic
- Files:
  - `test_speed_index.py`, `test_course_aptitude.py`, `test_frame_bias.py`, etc.
  - One test file per agent (mirrors src/indices/)
- Pattern: Use pytest; fixtures likely in conftest.py

**windows-agent/:**
- Purpose: JV-Link SDK interface running on Windows (Parallels VM)
- Key files:
  - `jvlink_agent.py` (900+ lines): Main loop with JVRead, local cache, HTTP POST
  - Debug helpers: `jv_status.py`, `jv_close.py`, `fix_jvlink_303.py`
  - `data/`: Local directories for cache, pending queue, completion log

**backend/alembic/:**
- Purpose: Database schema migration management
- Key files:
  - `versions/0001_initial_schema.py` (300+ lines): Create horses, races, race_entries, race_results, calculated_indices, etc.
  - `alembic.ini`: Alembic config
- Pattern: Always migrate with `alembic upgrade head`; never hand-edit schema

## Key File Locations

**Entry Points:**
- `backend/src/main.py`: FastAPI app creation & router inclusion
- `windows-agent/jvlink_agent.py`: Windows Agent main loop
- `backend/alembic/env.py`: Alembic runtime environment

**Configuration:**
- `backend/src/config.py`: Pydantic Settings (reads .env)
- `docker-compose.yml`: Multi-container setup
- `backend/pyproject.toml`: Python dependencies (uv)

**Core Logic:**
- `backend/src/indices/`: All 9 index calculation agents
- `backend/src/importers/`: Data parsing and ingestion
- `backend/src/db/models.py`: 13-table schema definition

**Testing:**
- `backend/tests/`: Pytest test files (one per index agent)
- `backend/src/indices/base.py`: Base class (tested indirectly via agents)

## Naming Conventions

**Files:**
- Python modules: snake_case (e.g., `jvlink_parser.py`, `course_aptitude.py`)
- Test files: `test_<module>.py` (e.g., `test_speed_index.py`)
- Migration files: `<number>_<description>.py` (e.g., `0001_initial_schema.py`)

**Directories:**
- Package directories: snake_case (e.g., `indices`, `importers`, `api`)
- Generated directories: Dot-prefix (e.g., `.venv`, `.pytest_cache`, `.git`)

**Python Classes:**
- Calculator classes: `<Subject>Calculator` (e.g., `SpeedIndexCalculator`, `CompositeIndexCalculator`)
- Importer classes: `<Subject>Importer` (e.g., `RaceImporter`, `OddsImporter`)
- Router variables: `router` or `<name>_router` (e.g., `agent_router`, `import_router`)

**Database:**
- Tables: plural snake_case (e.g., `horses`, `races`, `race_entries`)
- Models: singular CamelCase (e.g., `Horse`, `Race`, `RaceEntry`)
- Schema: `keiba` (all tables in single schema)

**Variables:**
- Index weights: `INDEX_WEIGHTS` (dict keyed by string names)
- Constants: UPPER_SNAKE_CASE (e.g., `SPEED_INDEX_MEAN`, `BASE_WEIGHT`)
- Database IDs: `*_id` suffix (e.g., `race_id`, `horse_id`, `jravan_race_id`)

## Where to Add New Code

**New Index Agent:**
- Implementation: `backend/src/indices/<agent_name>.py`
  - Inherit from `IndexCalculator`
  - Implement `calculate()` and `calculate_batch()`
  - Add constants and algorithm docs
- Tests: `backend/tests/test_<agent_name>.py`
  - Use pytest; mock database queries
- Integration: Update `backend/src/indices/__init__.py` (barrel export)
- Weights: Add to `backend/src/utils/constants.py` (INDEX_WEIGHTS dict)
- Composite: Update `backend/src/indices/composite.py` (add agent instance + calculation)

**New API Endpoint:**
- Router file: `backend/src/api/<domain>_router.py`
- Models: Request/Response Pydantic models in same file
- Logic: Call importers/calculators from api module
- Registration: `app.include_router()` in `backend/src/main.py`

**New Database Table:**
- Model: Add class to `backend/src/db/models.py`
- Migration: `alembic revision --autogenerate -m "Add <table>"`
  - Edit `backend/alembic/versions/<id>_*.py` to verify
  - Run `alembic upgrade head`

**Utilities & Helpers:**
- Shared logic: `backend/src/utils/` (e.g., constants.py, formatters.py)
- Module-specific helpers: Keep in same file as user (e.g., parser helpers in jvlink_parser.py)

**Windows Agent Updates:**
- Main loop: Edit `windows-agent/jvlink_agent.py`
- Debug tools: New scripts in `windows-agent/debug_*.py` or `windows-agent/fix_*.py`
- Config: Update `windows-agent/requirements.txt` if dependencies added

## Special Directories

**backend/alembic/:**
- Purpose: Schema migration history
- Generated: No (hand-written migrations)
- Committed: Yes (always)
- Workflow: `alembic revision --autogenerate` generates skeleton; edit before `upgrade`

**backend/.venv/:**
- Purpose: Python virtual environment
- Generated: Yes (via `uv sync`)
- Committed: No (in .gitignore)

**windows-agent/data/:**
- Purpose: Local caching, retry queue, completion logging
- Generated: Yes (runtime)
- Committed: No (in .gitignore)

**logs/:**
- Purpose: Runtime log files (kiseki.log, jvlink_agent.log)
- Generated: Yes (runtime)
- Committed: No (in .gitignore)

**data/raw/ and data/processed/:**
- Purpose: External data sources and analysis outputs
- Generated: Yes (manual setup)
- Committed: No (in .gitignore)

---

*Structure analysis: 2026-03-22*
