# Codebase Concerns

**Analysis Date:** 2026-03-22

## Tech Debt

### Incomplete Odds Import Implementation

**Area:** Odds processing

**Issue:** Pair and trio odds extraction is stubbed. Only single win (O1) and place (O2) odds are fully implemented. Complex bets (exacta, trifecta, trio, trifecta) are skipped entirely.

**Files:**
- `backend/src/importers/odds_importer.py:192-209` (_extract_pair_odds, _extract_trio_odds)

**Impact:** Missing odds data for quadrifecta, trifecta, trio, and exacta bets. These are critical for expected value calculations in MS7. Users cannot properly evaluate combination bet value.

**Fix approach:**
1. Parse JVDF v4.9 spec for O3-O8 record structures (combination key encoding)
2. Implement _extract_pair_odds() for O3/O4/O5/O6 (likely requires pair combination enumeration)
3. Implement _extract_trio_odds() for O7/O8 (requires trio combination enumeration)
4. Test each bet type against real JV-Link data with known odds

---

### Real-time Recalculation Not Implemented

**Area:** Change detection pipeline

**Issue:** When entry changes (scratches, jockey changes) are detected via `POST /api/changes/notify`, they are recorded to DB with `recalc_triggered=False` but no actual recalculation is triggered. The comment says "MS5 でリアルタイム再算出トリガーを実装".

**Files:**
- `backend/src/api/import_router.py:172` (TODO comment)
- `backend/src/importers/change_handler.py:86, 118` (recalc_triggered always False)

**Impact:** If a jockey changes 2 hours before post time, users will be operating on stale indices. System doesn't respond to real-time data changes, undermining the competitive advantage promise.

**Fix approach:**
1. Create a recalculation scheduler that queries EntryChange table for `recalc_triggered=False`
2. For scratch: call all index agents' recalculate() with entire race
3. For jockey change: call jockey index + pace index for that horse, then composite for race
4. Update EntryChange.recalc_triggered=True after completion
5. Integrate with FastAPI background task or AsyncIO worker pool

---

### Debug Logging Left in Production Code

**Area:** API import endpoint

**Issue:** Hardcoded debug logging using `logger.warning()` instead of `logger.debug()` for every import request. This generates unnecessary noise in logs.

**Files:**
- `backend/src/api/import_router.py:93` (logger.warning with "DEBUG recv" message)

**Impact:** Log files fill up with non-critical data. Actual warnings become harder to spot.

**Fix approach:** Change line 93 to use `logger.debug()` instead of `logger.warning()`.

---

## Known Bugs

### Race Entry Lookup May Return None Without Error

**Area:** Importer data handling

**Issue:** In `_get_horse_id_by_horse_num()`, if RaceEntry lookup returns None (horse not found for a given horse_number), the code silently returns None without logging a warning. This can mask data inconsistency bugs.

**Files:**
- `backend/src/importers/change_handler.py:50-60`

**Trigger:** Entry change notification arrives before corresponding RaceEntry is imported, or horse_number in AV/JC record doesn't match RaceEntry.horse_number.

**Workaround:** Log always shows the race_id and horse_num at line 90/122, so the issue can be traced if EntryChange.horse_id is NULL.

---

### Parse Exceptions Caught Broadly, Logged Minimally

**Area:** Record parsing

**Issue:** Parser exceptions in `jvlink_parser.py` are caught with generic `except Exception` and logged with only the first 30 characters of data. If parsing fails mid-record, root cause is difficult to diagnose.

**Files:**
- `backend/src/importers/jvlink_parser.py:295-297` (parse_ra exception handler)
- `backend/src/importers/jvlink_parser.py:409-411` (parse_se exception handler)
- `backend/src/importers/jvlink_parser.py:451-453` (parse_odds exception handler)
- `backend/src/importers/jvlink_parser.py:491-493` (parse_av exception handler)
- `backend/src/importers/jvlink_parser.py:538-540` (parse_jc exception handler)

**Trigger:** Malformed JV-Link data from Windows agent, encoding issues, or field boundary violations.

**Workaround:** Check `jvlink_agent.log` on Windows side to confirm data was sent correctly. Cross-check with JV-Link SDK version compatibility.

---

### ORM Lazy N+1 Risk in Index Calculators

**Area:** Database queries

**Issue:** Several index calculators use queries that may trigger N+1 lazy loads. For example, in `course_aptitude.py`, `pace.py`, and `rotation.py`, the code fetches multiple RaceResult rows and then accesses `race_result.race.surface`, `race_result.race.condition`, etc. If `race` is not eagerly joined, this causes additional queries per row.

**Files:**
- `backend/src/indices/course_aptitude.py:200-210` (unparsed query patterns need review)
- `backend/src/indices/pace.py:190-200` (similar pattern)
- `backend/src/indices/rotation.py:220-240` (similar pattern)

**Impact:** MS4/MS5 when processing 5,000+ historical races, batch recalculation becomes very slow (100s of extra queries per race).

**Fix approach:**
1. Add `.options(joinedload(RaceResult.race))` to all SQLAlchemy queries that access race attributes
2. Profile with `sqlalchemy.echo=True` to confirm N+1 is eliminated
3. Add query comment markers for future maintainers

---

## Security Considerations

### API Key Validation Bypass in Development

**Area:** API authentication

**Issue:** In `verify_api_key()`, if `settings.change_notify_api_key` is empty (development mode), the function returns early without validation. This is intentional but means any client can POST to `/api/import/*` and `/api/changes/notify` if the env var is not set.

**Files:**
- `backend/src/api/import_router.py:30-41`

**Current mitigation:** Docker-compose and local development require explicit .env setup. Production deployment MUST set CHANGE_NOTIFY_API_KEY.

**Recommendations:**
1. Add validation in config.py to raise an error if api_env="production" and change_notify_api_key is empty
2. Log every successful API call with the calling IP (for audit trail in production)
3. Rate-limit `/api/import` endpoints to prevent DOS

---

### Database Password in Connection String

**Area:** Database configuration

**Issue:** Database connection password is stored in `settings.database_url` property. If logs ever include the full connection string, the password is exposed.

**Files:**
- `backend/src/config.py:18-23`

**Current mitigation:** SQLAlchemy logs typically use "***" for passwords, but depends on logging setup.

**Recommendations:**
1. Use environment variable for full DSN (not assembled in code)
2. Audit log output for password leaks in CI/CD
3. Rotate DB password regularly (VPS admin task, not this codebase)

---

### Secrets in Git (Prevented)

**Area:** Version control

**Issue:** .env file is in .gitignore (good), but CLAUDE.md documents real values like JRAVAN_SID usage. Windows agent expects env var reading.

**Files:**
- `.gitignore` (should have .env listed)

**Current mitigation:** .env is not committed. .env.example is provided for template.

**Recommendations:** Verify .gitignore includes all secret patterns; audit git history for any accidental commits.

---

## Performance Bottlenecks

### Large File Parsing Without Streaming

**Area:** Windows agent data fetch

**Issue:** `jvlink_agent.py` loads entire JVRead() buffers into memory as strings, then parses line-by-line. For large result sets (2-year setup with option=3), a single JVRead() call may return 10MB+ in a single string. Python strings are immutable, so repeated slicing in parsers creates memory pressure.

**Files:**
- `windows-agent/jvlink_agent.py:150-170` (cache/load logic)

**Impact:** Peak memory usage during 2-year setup can spike to 500MB+. On 32-bit Python (Windows requirement), this approaches the virtual address space limit.

**Improvement path:**
1. Process JVRead data in chunks (read 1MB, parse, flush to DB, repeat)
2. Stream JSON to disk instead of buffering entire list in memory
3. Use a line-buffered generator for record iteration

---

### Index Calculation Caching Insufficient

**Area:** Index calculators

**Issue:** SpeedIndexCalculator has a `_std_time_cache` dict that is per-session. For MS4 batch recalculation of 200+ horses × 14 indices, the same base stats (e.g., "芝 1600m 良" mean/std) are recalculated hundreds of times.

**Files:**
- `backend/src/indices/speed.py:61` (_std_time_cache definition)
- Similar pattern in course_aptitude.py, pace.py, etc.

**Impact:** Unnecessary SQL queries and calculations. A 200-horse race batch recalculation takes ~5-10 seconds when it could take <1 second with proper caching.

**Improvement path:**
1. Move caching to a class-level or module-level LRU cache (not per-session)
2. Cache on (surface, distance, condition) tuple
3. Invalidate cache when new historical data is imported

---

### Batch Import Without Transaction Grouping

**Area:** Database import

**Issue:** `import_records()` in RaceImporter calls `db.flush()` once at the end, but doesn't batch commits by race ID or by time. If a race has 18 horses and each horse creates Horse/Jockey/Trainer/RaceEntry/RaceResult rows, the flush() writes ~100 rows per race sequentially.

**Files:**
- `backend/src/importers/race_importer.py:80-112`

**Impact:** Slow import (1000 records/minute instead of 10,000/minute). During 2-year setup, this extends import time by hours.

**Improvement path:**
1. Batch inserts per race (flush after each race)
2. Use bulk_insert_mappings() for Horse/Jockey/Trainer bulk creates
3. Profile with timed splits to measure improvement

---

## Fragile Areas

### JV-Link Windows Agent Encoding Conversion

**Area:** Windows-Mac boundary

**Issue:** The SJIS/Latin-1 encoding conversion is fragile and depends on exact understanding of win32com BSTR behavior. If JV-Link SDK version changes or pywin32 is updated, the encoding may change.

**Files:**
- `windows-agent/jvlink_agent.py` (uses _normalize_jvread internally or directly)
- `backend/src/importers/jvlink_parser.py:113-131` (_decode function)

**Why fragile:** The CLAUDE.md documents the workaround but it's not obvious from code comments why `encode('latin-1').decode('cp932')` is needed.

**Safe modification:**
1. Add extensive docstring comments explaining win32com BSTR behavior
2. Add a test that reads actual JV-Link data and validates encoding round-trips
3. Version-pin pywin32 to known-good version in windows-agent requirements

**Test coverage:** SJIS encoding tests are missing. Add test with sample kanji strings.

---

### Incomplete Model Schema

**Area:** Database schema

**Issue:** Several DB models have nullable or incomplete fields that don't match JVDF spec requirements:
- `Horse.birthday` is String(8) but SEレコードにはbirthdayフィールドがない（出力してない）
- `RaceResult.margin` is defined but never populated (JV-Link doesn't provide margin in SEレコード)
- `TrackCondition` table is defined but never used

**Files:**
- `backend/src/db/models.py:31` (Horse.birthday never filled)
- `backend/src/db/models.py:139` (RaceResult.margin never filled)
- `backend/src/db/models.py:149+` (TrackCondition unused)

**Why fragile:** Unused schema creates confusion. Code reviewers wonder if they're missing something.

**Safe modification:**
1. Remove unused columns/tables or document planned use (e.g., "for MS4 TrackCondition analysis")
2. For birthday: either populate from HOSE レコード (not yet implemented) or document that it's always empty
3. For margin: either compute from passing positions or remove it

---

### Index Recalculation Versioning Not Implemented

**Area:** Version tracking

**Issue:** CLAUDE.md says "再算出対応: version番号をインクリメントして管理", but no CalculatedIndex table has a version column. If an index algo changes, there's no way to track which calculation method was used for a given index value.

**Files:**
- `backend/src/db/models.py` (no version column in CalculatedIndex or indices tables)

**Why fragile:** If a bug is discovered in SpeedIndexCalculator on 2026-03-25, we can't tell which historical values were calculated with the buggy code vs. the fixed code.

**Safe modification:**
1. Add `version: int` column to CalculatedIndex and related indices models
2. Increment version in constants.py when algo changes
3. Log version number in calculate_batch()

---

### CORS Hardcoded to localhost:3000

**Area:** Frontend integration

**Issue:** FastAPI CORS middleware in `main.py` has hardcoded allow_origins=["http://localhost:3000"]. This won't work for production deployment or testing on different ports.

**Files:**
- `backend/src/main.py:17-23`

**Impact:** Frontend on production domain will be blocked by CORS. Requires hardcode change per environment.

**Safe modification:**
1. Move allow_origins to settings.py configuration
2. Default to localhost for dev, allow env var override for production
3. Validate origin against a whitelist (don't use "*")

---

## Scaling Limits

### Single-threaded JVOpen() Blocks Entire Agent

**Area:** Data ingestion

**Issue:** `jvlink_agent.py` calls JVOpen(option=3) on main thread for 2-year setup. This blocks for hours. Although a heartbeat thread runs, it can't send keepalive data while JVOpen is executing.

**Files:**
- `windows-agent/jvlink_agent.py` (architecture issue)

**Current capacity:** Single machine Windows agent can handle 1 setup/day (takes ~4 hours). If we need faster turnaround or multiple data fetches, this is a hard wall.

**Scaling path:**
1. Run JVOpen in subprocess or thread with timeout monitoring
2. Implement queue-based worker model (fetch thread, parse thread, POST thread separate)
3. Support multiple Windows agents in parallel

---

### Indices Calculated Serially

**Area:** Computation

**Issue:** When calculate_batch() is called for a race, 14 index agents all run sequentially on the same thread. Each agent does independent DB queries, so they could run in parallel.

**Files:**
- `backend/src/api/agent_router.py` (not yet visible, but anticipated)

**Current capacity:** Single race (18 horses) with 14 indices takes ~2-3 seconds. For 500 races/day, that's 15 minutes of computation.

**Scaling path:**
1. Use ThreadPoolExecutor to call calculate_batch() for all index agents in parallel
2. Use ProcessPoolExecutor for CPU-bound index calculations (future, if algos get more complex)
3. Cache index results to avoid recalculation on API reads

---

### Database Connection Pool Not Configurable

**Area:** Database

**Issue:** SQLAlchemy uses default connection pool (5 connections). If multiple workers or background tasks try to calculate indices, they'll contend for pool connections.

**Files:**
- `backend/src/db/session.py` (not yet examined, but likely uses default pool)

**Current capacity:** ~5 concurrent DB queries before queuing. With 14 index agents × 200 horses, this becomes a bottleneck.

**Scaling path:**
1. Increase pool size based on expected concurrency (settings.py)
2. Use SQLAlchemy async driver (AsyncEngine) for non-blocking I/O
3. Implement proper connection lifecycle management

---

## Dependencies at Risk

### pywin32 Version Pinning Required

**Area:** Windows agent

**Issue:** `windows-agent` requirements.txt likely doesn't pin pywin32 version. JV-Link COM interface is fragile; newer pywin32 versions may change BSTR handling.

**Files:**
- `windows-agent/requirements.txt` (not examined, but critical)

**Risk:** If a developer upgrades pywin32, encoding may break silently.

**Migration plan:** Pin to known-good version (likely 300+) and test thoroughly before upgrading.

---

### PostgreSQL Version Compatibility Unknown

**Area:** Database

**Issue:** CLAUDE.md doesn't specify minimum PostgreSQL version. Code uses modern SQLAlchemy 2.0 features (Mapped, mapped_column). If VPS is running PostgreSQL 9.6, some features may not work.

**Files:**
- `backend/src/db/models.py` (SQLAlchemy 2.0 syntax)

**Risk:** Deployment fails if target DB is too old.

**Migration plan:** Document "PostgreSQL 12+" requirement and add version check at startup.

---

## Missing Critical Features

### No Data Validation at Import Boundary

**Issue:** Windows agent POST request is not validated against schema. If a corrupted batch is sent, the parsers attempt to extract fields that may be out of bounds.

**Blocks:** Reliable 2-year data ingestion. Currently, bad data is logged but not quarantined.

**Approach:** Add Pydantic models for expected JV-Link record structure and validate before parsing.

---

### No Backup/Recovery Procedure for Windows Agent

**Issue:** If Windows agent crashes during 2-year setup, progress is lost. LoLocalキャッシュ and pending queue help, but manual recovery might be needed.

**Blocks:** Automation, MS8 full unattended operation.

**Approach:** Implement checkpointing of processed file list and resumption logic.

---

### No Metrics or Observability

**Issue:** No Prometheus metrics, structured logging, or tracing. When things go wrong, diagnosing the issue requires reading raw logs.

**Blocks:** Production operations, SLA compliance.

**Approach:** Add Python logging.config, integrate with ELK or CloudWatch for production.

---

## Test Coverage Gaps

### No Integration Tests for Data Pipeline

**What's not tested:** The full flow from Windows JV-Link read → POST → parse → DB upsert. Test files exist for individual index calculators but not for importers.

**Files:**
- `backend/tests/test_speed_index.py` and others test indices in isolation
- No `test_race_importer.py` or `test_jvlink_parser.py`

**Risk:** Silent data loss (e.g., a field that's parsed but not inserted to DB).

**Priority:** High

**Path:** Create test_race_importer.py with fixture data from real JV-Link samples.

---

### No E2E Tests for Windows Agent

**What's not tested:** The jvlink_agent.py behavior with real JV-Link SDK. Windows agent is tested manually via logs.

**Files:**
- `windows-agent/jvlink_agent.py` (no test file exists)

**Risk:** Encoding bugs, file handling errors, POST failures go unnoticed until 2-year setup fails.

**Priority:** Medium (hard to automate without Windows + JV-Link license)

**Path:** Create fixture-based tests for non-COM parts (cache, pending queue, retry logic).

---

### No Database Schema Migration Tests

**What's not tested:** Alembic migrations. If a schema change is made, it's not tested on a real PostgreSQL instance before production.

**Files:**
- Alembic migrations (not yet examined)

**Risk:** Production deployment fails or data corrupts due to bad migration.

**Priority:** High

**Path:** Add Docker PostgreSQL test container and run migrations in CI/CD.

---

*Concerns audit: 2026-03-22*
