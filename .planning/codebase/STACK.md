# Technology Stack

**Analysis Date:** 2026-03-22

## Languages

**Primary:**
- Python 3.12+ - Backend (FastAPI), Windows Agent, data importers, index calculations
- Python 3.x 32bit - Windows Agent requirement (JV-Link COM integration)

**Secondary:**
- TypeScript/Next.js - Frontend (planned, MS6)

## Runtime

**Backend Environment:**
- Python 3.12 (from `pyproject.toml`)
- FastAPI application server via Uvicorn

**Windows Agent Environment:**
- Python 3.x 32bit (mandatory for JV-Link COM compatibility)

**Package Manager:**
- uv (Python) - Primary dependency manager for backend
- pip (fallback for Windows Agent)
- pnpm (Node.js, for frontend when ready)

## Frameworks

**Core:**
- FastAPI 0.115.0+ - HTTP API server for race data, indices, betting endpoints
- SQLAlchemy 2.0+ - ORM for PostgreSQL with `keiba` schema management
- Alembic 1.14.0+ - Database migration framework

**Data Processing:**
- pandas 2.2.0+ - Data analysis and transformation
- numpy 2.0.0+ - Numerical computations for index calculations
- APScheduler 3.10.0+ - Scheduled tasks and background jobs

**Communication:**
- httpx 0.27.0+ - HTTP client for Windows Agent → FastAPI communication
- websockets 13.0+ - WebSocket support (for realtime features in MS5+)
- requests 2.31.0+ - HTTP requests from Windows Agent to backend

**Configuration:**
- pydantic 2.9.0+ - Data validation and settings management
- pydantic-settings 2.6.0+ - Environment variable loading
- python-dotenv 1.0.0+ - `.env` file parsing

## Testing

**Backend Testing:**
- pytest 8.0.0+ - Unit and integration test runner
- pytest-asyncio 0.24.0+ - Async test support for FastAPI async handlers

**Code Quality:**
- ruff 0.8.0+ - Python linter and formatter
- eslint (planned) - TypeScript linting
- prettier (planned) - Code formatting for TypeScript

## Build & Deployment

**Containerization:**
- Docker - Application containerization (`backend/Dockerfile`)
- Docker Compose - Multi-service orchestration (`docker-compose.yml`)

**Build Configuration:**
- `pyproject.toml` - Python project configuration and dependencies
- `Dockerfile` - Backend container build (Python 3.12-slim base)
- `docker-compose.yml` - Services: backend on port 8001 (internal 8000)

## Database

**Primary:**
- PostgreSQL (VPS hosted) - Persistent data storage, `keiba` schema
- psycopg2-binary 2.9.0+ - PostgreSQL adapter for Python

**Migration:**
- Alembic - Version control for database schema changes
- Migration file: `backend/alembic/versions/0001_initial_schema.py`

## Key Dependencies

**Critical:**
- fastapi - REST API framework with automatic OpenAPI documentation
- sqlalchemy - Database abstraction and ORM
- psycopg2-binary - PostgreSQL connection
- pywin32 306+ - Windows COM integration for JV-Link SDK (Windows Agent only)

**Data Processing:**
- pandas - DataFrame operations for race data transformation
- numpy - Vectorized numerical operations for index calculations

**Windows Integration:**
- pywin32 - COM interface to JV-Link SDK (32bit Python only)
- python-dotenv - Load credentials from `.env`

**Monitoring & Logging:**
- Standard Python logging module - Application logs to `logs/kiseki.log`

## Configuration Management

**Environment Variables:**
- Location: `.env` (git-ignored, see `.env.example`)
- Configuration class: `backend/src/config.py` → `Settings`
- Settings parsed by pydantic-settings from `.env`

**Key Configs Required:**
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` - PostgreSQL connection
- `DB_SCHEMA` - Always `keiba` (enforced in code)
- `JRAVAN_SID` - JRA-VAN SDK authentication key
- `BACKEND_URL` - Mac FastAPI URL for Windows Agent (typically `http://host.internal:8000` in Parallels)
- `CHANGE_NOTIFY_API_KEY` - Shared secret for Windows Agent → Backend API calls
- `BET_MAX_PER_DAY`, `BET_MAX_PER_RACE`, `BET_MAX_PER_TICKET` - Betting safety limits
- `BET_MIN_EXPECTED_VALUE` - Purchase threshold (1.2 = 20% edge required)
- `API_ENV`, `DEBUG`, `LOG_LEVEL` - Logging and debug configuration

**Build Targets:**
- Backend Docker image: `python:3.12-slim` base
- Uvicorn listening on `0.0.0.0:8000` (exposed to `127.0.0.1:8001` via docker-compose)
- Frontend (MS6): Next.js 14 with App Router, environment variables: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`

## Platform Requirements

**Development:**
- macOS (primary development, Parallels VM with Windows 11)
- Windows 11 (in Parallels VM) for JV-Link SDK integration
- Python 3.12+ on macOS for backend development
- Docker + Docker Compose for containerized backend

**Production:**
- VPS PostgreSQL instance (existing, keiba database)
- macOS or Linux Docker host for FastAPI backend
- Windows 11 with JV-Link SDK for data ingestion agent
- (Future) Web hosting for Next.js frontend (MS6+)

**Third-Party APIs:**
- JRA-VAN Data Lab SDK - Japanese horse racing data via COM interface (Windows 32bit Python only)
- No external SaaS integrations in MS1 (self-hosted PostgreSQL)

---

*Stack analysis: 2026-03-22*
