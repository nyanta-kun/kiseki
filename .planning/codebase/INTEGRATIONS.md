# External Integrations

**Analysis Date:** 2026-03-22

## APIs & External Services

**JRA-VAN Data Lab SDK:**
- Service: Japanese horse racing data provider (official JRA-VAN system)
- What it's used for: Fetch race information, entries, odds, results, pedigree, training data
- SDK/Client: `pywin32` COM interface to JV-Link (32bit Windows only)
- Auth: `JRAVAN_SID` environment variable (subscription ID)
- Data Types Consumed:
  - Accumulation data: `JVOpen()` for race, pedigree, training history
  - Real-time data: `JVRTOpen()` for odds (O1-O8), scratch notifications, jockey changes
  - Batch operations: `JVRead()` returns SJIS-encoded fixed-length binary strings (1 record per call)
- Integration Point: `backend/src/importers/jvlink_parser.py` parses fixed-format JV-Link records

**HTTP Backend Communication (Windows → Mac):**
- Protocol: HTTP POST to FastAPI backend
- URL: `BACKEND_URL` from `.env` (typically `http://host.internal:8000` in Parallels)
- Authentication: `X-API-Key` header with `CHANGE_NOTIFY_API_KEY` value
- Used by: `windows-agent/jvlink_agent.py` → MacOS FastAPI backend
- Endpoints:
  - `POST /api/import/races` - RA/SE records (race info, entries)
  - `POST /api/import/entries` - SE records (entries alternative)
  - `POST /api/import/odds` - O1-O8 records (odds)
  - `POST /api/import/weights` - WE records (horse weights)
  - `POST /api/changes/notify` - AV/JC records (scratches, jockey changes)

## Data Storage

**Databases:**

**PostgreSQL (VPS hosted):**
- Provider: Self-hosted on VPS (existing infrastructure)
- Connection: Host/port/credentials from `.env` (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`)
- Connection URL: `postgresql://keiba_app:password@host:5432/keiba?options=-csearch_path=keiba`
- Client: SQLAlchemy 2.0 ORM with psycopg2-binary adapter
- Schema: `keiba` (hardcoded, enforced in `backend/src/db/session.py`)
- Tables (MS1 implemented):
  - `keiba.horses` - Horse master data
  - `keiba.jockeys` - Jockey master data
  - `keiba.trainers` - Trainer master data
  - `keiba.races` - Race information
  - `keiba.race_entries` - Starting lineup
  - `keiba.race_results` - Race results
  - `keiba.odds` - Betting odds (all bet types: O1-O8)
  - `keiba.pedigrees` - Horse bloodline data
  - `keiba.calculated_indices` - Computed speed/aptitude/bias indices
  - `keiba.changes` - Change log (scratches, jockey changes, weight changes)

**File Storage:**
- Local filesystem only (Windows Agent):
  - `windows-agent/data/cache/` - JVRead raw record caching (JSONL format)
  - `windows-agent/data/pending/` - Failed POST retry queue
  - `windows-agent/data/completed/` - Processing completion log
  - `logs/kiseki.log` - Application log file

## Caching

**Local Cache (Windows Agent):**
- Type: File-based (JSONL) per dataspec/date/option
- Purpose: Avoid re-fetching identical JV-Link data across agent restarts
- Location: `windows-agent/data/cache/*.jsonl`
- Key format: `{DATASPEC}_{FROM_TIME}_{OPTION}.jsonl`
- Load before JVOpen: If cache exists for that key, skip JVOpen call
- Save after JVRead: Immediately persist all records to cache after fetch

**Pending Queue (Windows Agent):**
- Type: File-based (JSONL) per failed POST batch
- Purpose: Retry failed API calls on next agent startup (network resilience)
- Location: `windows-agent/data/pending/*.jsonl`
- Lifecycle: Moved to `completed/` on successful POST, deleted on manual cleanup

## Authentication & Identity

**Auth Provider:**
- Custom API Key approach (not OAuth/JWT)
- Implementation: X-API-Key header validation in `backend/src/api/import_router.py`
  - `verify_api_key()` function checks `X-API-Key` against `settings.change_notify_api_key`
  - If `CHANGE_NOTIFY_API_KEY` is empty in `.env`, auth is bypassed (development mode)
- Protected Endpoints: `/api/import/*`, `/api/changes/notify`
- Token Format: Plain string shared secret (stored in `.env`)

**JRA-VAN Authentication:**
- Method: Subscription ID (`JRAVAN_SID`) passed to JV-Link COM interface
- Scope: Windows Agent → JRA-VAN Data Lab SDK
- Token Lifetime: Persistent session per JV-Link instance

## Monitoring & Observability

**Error Tracking:**
- Not integrated in MS1 (self-hosted logging only)
- Future: External error tracking (Sentry, etc.) planned for MS7+

**Logs:**
- Approach: Standard Python `logging` module
- Output: Console + file (`logs/kiseki.log`)
- Format: `%(asctime)s [%(levelname)s] %(message)s`
- Log Level: Configurable via `LOG_LEVEL` env var (default: INFO)
- Windows Agent Logs: `windows-agent/jvlink_agent.log`
- Backend Logs: Docker container logs + `logs/kiseki.log` (mounted volume)

**Health Checks:**
- Endpoint: `GET /health` (FastAPI)
- Response: `{"status": "ok", "env": settings.api_env}`
- Docker Compose: Healthcheck every 30s via HTTP GET to `/health`

## CI/CD & Deployment

**Hosting:**
- Local macOS development (FastAPI on `http://localhost:8000`)
- Docker Compose for containerized backend (port 8001 → 8000)
- Windows Agent: Direct Python execution on Windows 11 (Parallels VM)
- VPS: PostgreSQL only (existing, no app server hosting)
- Future (MS6+): Next.js frontend hosting (location TBD)

**CI Pipeline:**
- Not detected in MS1 (manual local testing)
- Future: GitHub Actions or similar (planned for MS7+)

**Deployment Method:**
- Docker Compose: `docker-compose up -d`
- Container: `python:3.12-slim` base with `uv sync` dependency resolution
- Command: `uvicorn src.main:app --host 0.0.0.0 --port 8000`
- Volume Mounts: `./backend/src` (code), `./logs` (log file)
- Restart Policy: `unless-stopped`

## Environment Configuration

**Required Environment Variables:**

**Database:**
- `DB_HOST` - PostgreSQL hostname/IP
- `DB_PORT` - PostgreSQL port (default 5432)
- `DB_NAME` - Database name (default "keiba")
- `DB_USER` - Database user (default "keiba_app")
- `DB_PASSWORD` - Database password (no default, REQUIRED)
- `DB_SCHEMA` - Schema name (always "keiba", enforced in code)

**JRA-VAN:**
- `JRAVAN_SID` - JRA-VAN subscription ID (no default, REQUIRED)

**FastAPI:**
- `API_HOST` - Listen address (default 0.0.0.0)
- `API_PORT` - Listen port (default 8000)
- `API_ENV` - Environment name: "development" or "production" (default "development")
- `DEBUG` - Debug mode flag (default True)
- `BACKEND_URL` - Windows Agent → FastAPI URL (default "http://host.internal:8000")
- `CHANGE_NOTIFY_API_KEY` - X-API-Key shared secret (default "", enables dev mode if empty)

**Betting Safety:**
- `BET_MAX_PER_DAY` - Daily spend limit JPY (default 30000)
- `BET_MAX_PER_RACE` - Per-race spend limit JPY (default 5000)
- `BET_MAX_PER_TICKET` - Per-ticket spend limit JPY (default 1000)
- `BET_MIN_EXPECTED_VALUE` - Purchase threshold multiplier (default 1.20 = 20% edge)
- `BET_MAX_CONSECUTIVE_LOSSES` - Loss limit before pause (default 10)

**Logging:**
- `LOG_LEVEL` - Python logging level (default INFO)
- `LOG_FILE` - Log file path (default "logs/kiseki.log")

**Secrets Location:**
- Primary: `.env` file (git-ignored)
- Template: `.env.example` (checked in with defaults/placeholders)
- Windows Agent: Reads `.env` from project root via `python-dotenv`

## Webhooks & Callbacks

**Incoming Webhooks:**
- Not implemented in MS1

**Outgoing Webhooks:**
- Not implemented in MS1 (future IPAT integration in MS7)

**Real-time Change Notifications (MS5+ planned):**
- Current: POST requests from Windows Agent with change data
- Future: WebSocket subscription model for race updates
- Change Types: `scratch` (出走取消), `jockey_change` (騎手変更), `weight_change` (斤量変更)
- Endpoint: `POST /api/changes/notify` with `ChangeNotifyRequest` body:
  ```json
  {
    "change_type": "scratch|jockey_change",
    "raw_data": "[JC/AV レコード文字列]",
    "detected_at": "[ISO8601 timestamp]"
  }
  ```

---

*Integration audit: 2026-03-22*
