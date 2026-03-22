# Testing Patterns

**Analysis Date:** 2026-03-22

## Test Framework

**Runner:**
- pytest (v8.0.0+) - see `pyproject.toml`
- Config: `pyproject.toml` under `[tool.pytest.ini_options]`

**Assertion Library:**
- pytest built-in assertions + `pytest.approx()` for float comparisons

**Run Commands:**
```bash
# Run all tests
pytest

# Run specific test file
pytest backend/tests/test_speed_index.py

# Run with verbose output
pytest -v

# Watch mode (requires pytest-watch)
pytest-watch

# Coverage report
pytest --cov=src --cov-report=html
```

**Async Testing:**
- Config: `asyncio_mode = "auto"` in pytest.ini
- Marks not needed for basic async functions
- Use `pytest.mark.asyncio` only if needed explicitly

## Test File Organization

**Location:**
- Co-located in `backend/tests/` directory (parallel to `backend/src/`)
- One test file per module: `test_speed_index.py` for `indices/speed.py`

**Naming:**
- Test files: `test_<module_name>.py`
- Test classes: `Test<FunctionName>` (capitalize function name)
- Test methods: `test_<scenario_description>` (lowercase with underscores)
- Test helpers: `_make_<object>()` prefix for mock factories

**Structure:**
```
backend/tests/
├── test_speed_index.py
│   ├── TestFinishTimeConversion
│   │   ├── test_typical_1600m
│   │   ├── test_none_returns_none
│   │   └── test_zero_returns_none
│   ├── TestWeightedAverage
│   └── TestSingleRaceSpeedScore
├── test_frame_bias.py
└── test_jockey_index.py
```

## Test Structure

**Suite Organization:**
```python
"""Module docstring explaining what is tested."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from src.indices.speed import SpeedIndexCalculator

# --- Helper functions ---

def _make_result(finish_time: float) -> MagicMock:
    """Create mock RaceResult."""
    r = MagicMock()
    r.finish_time = Decimal(str(finish_time))
    return r

# --- Test classes ---

class TestWeightedAverage:
    """Test weighted averaging logic."""

    def test_empty_returns_mean(self) -> None:
        """Empty list returns default mean value."""
        # Arrange
        calc = SpeedIndexCalculator(db=MagicMock())

        # Act
        result = calc._weighted_average([])

        # Assert
        assert result == SPEED_INDEX_MEAN

    def test_single_score(self) -> None:
        """Single score returns itself."""
        calc = SpeedIndexCalculator(db=MagicMock())
        assert calc._weighted_average([60.0]) == 60.0
```

**Setup/Teardown:**
- No explicit setup() or teardown() - use MagicMock for database
- Fixture pattern not used (tests are isolated via mocks)
- Per-test setup in arrange phase: `calc = SpeedIndexCalculator(db=MagicMock())`

## Mocking

**Framework:**
- `unittest.mock.MagicMock` from Python stdlib
- Sufficient for SQL queries and object attributes

**Patterns:**

**1. Database Mocks:**
```python
db = MagicMock()
mock_race = MagicMock()
mock_race.id = 1
mock_race.date = "20260322"
mock_race.course = "05"
mock_race.distance = 1600

# Configure query chain
db.query.return_value.filter.return_value.first.return_value = mock_race
db.query.return_value.filter.return_value.all.return_value = [entry1, entry2]

calc = SpeedIndexCalculator(db=db)
```

**2. Object Factory Helpers:**
```python
def _make_result(finish_time: float, abnormality: int = 0) -> MagicMock:
    """Create mock RaceResult with defaults."""
    r = MagicMock()
    r.finish_time = Decimal(str(finish_time))
    r.finish_position = 1
    r.abnormality_code = abnormality
    return r

def _make_race(course: str = "05", distance: int = 1600) -> MagicMock:
    """Create mock Race with defaults."""
    r = MagicMock()
    r.course = course
    r.distance = distance
    r.surface = "芝"
    return r
```

**3. Method Overriding in Tests:**
```python
calc = SpeedIndexCalculator(db=db)

# Override internal method for batch test
def mock_batch(horse_ids, before_date, exclude_race_id):
    # Return controlled test data
    return {101: [row1, row2], 102: [row3]}

calc._get_past_results_batch = mock_batch
result = calc.calculate_batch(race_id=1)
```

**4. Cache Injection:**
```python
calc = SpeedIndexCalculator(db=MagicMock())
# Inject cached values to avoid DB query
calc._std_time_cache[("05", 1600, "芝", "良")] = (93.0, 2.0)
```

**5. Side Effects for Multi-Call Sequences:**
```python
db.query.return_value.filter.return_value.first.side_effect = [
    target_race,    # First call returns race
    entry           # Second call returns entry
]
```

**What to Mock:**
- Database Session (all tests)
- SQLAlchemy ORM queries (.query, .filter, .first, .all)
- External services (not applicable yet)
- Internal cache access (to avoid DB queries)

**What NOT to Mock:**
- Pure calculation functions (let them run)
- Utility functions (_finish_time_to_decimal, _last3f_to_decimal)
- Mock object attributes used in assertions (test their actual values)
- Don't mock the class under test - test actual public methods

## Fixtures and Factories

**Test Data:**
Test data is created per-test via factory functions named `_make_<object>()`:

```python
def _make_race_result(
    jockey_id: int | None = 1,
    finish_position: int = 1,
    last_3f: float | None = 34.0,
    abnormality_code: int = 0,
) -> MagicMock:
    """RaceResult mock with sensible defaults."""
    r = MagicMock()
    r.jockey_id = jockey_id
    r.finish_position = finish_position
    r.last_3f = Decimal(str(last_3f)) if last_3f is not None else None
    r.abnormality_code = abnormality_code
    return r
```

**Location:**
- Test-local factories at top of test file under `# --- Helpers ---` comment
- No shared fixtures.py (each test module is independent)
- Factories can accept keyword arguments for variations

**Pattern:**
- Default values match happy path behavior
- Parameters override defaults for specific scenarios
- Return type is always MagicMock
- Document expected attributes in docstring

## Coverage

**Requirements:**
- No explicit coverage threshold (not enforced in CI)
- Focus on critical paths: all index calculation logic
- Unit tests cover calculation formulas and edge cases

**View Coverage:**
```bash
pytest --cov=src --cov-report=html
# View report: htmlcov/index.html
```

## Test Types

**Unit Tests (Primary):**
- Test individual functions in isolation
- Location: `backend/tests/test_<module>.py`
- Scope: Single Agent method or utility function
- Dependencies: All mocked (no DB access)
- Examples: `test_speed_index.py` TestWeightedAverage class
- Time: < 1ms each

**Integration Tests:**
- Test calculation Agent with mocked DB data
- Location: Same file as unit tests
- Scope: full calculate_batch() pipeline
- Dependencies: DB Session mocked, data flows through real logic
- Examples: `test_speed_index.py` TestCalculateBatch class
- Pattern: Mock DB queries, inject test data, verify orchestration

**E2E Tests:**
- Not yet implemented (MS5 milestone)
- Will test live DB and API endpoints
- Location: TBD
- Scope: Full race processing pipeline

## Common Patterns

**Numeric Comparison (Floating Point):**
```python
# Use pytest.approx for float comparisons
assert result == pytest.approx(60.0, abs=0.1)
# abs tolerance: ±0.1
# or rel tolerance: rel=0.01 (1%)
```

**Testing Relative Ordering:**
```python
def test_fast_horse_higher_than_slow(self) -> None:
    """Faster horses get higher indices."""
    calc = _build_calc_with_past_data({101: [91.0], 102: [95.0]})
    result = calc.calculate_batch(race_id=1)
    assert result[101] > result[102]
```

**Testing Range Bounds:**
```python
def test_index_within_valid_range(self) -> None:
    """Result clipped to [0, 100]."""
    calc = self._calc()
    result = calc._single_race_speed_score(...)
    assert 0.0 <= result <= 100.0
```

**Testing Default/Fallback Behavior:**
```python
def test_no_data_returns_mean(self) -> None:
    """Missing past data returns default mean."""
    calc = self._build_calc_with_past_data([101], {})  # Empty past data
    result = calc.calculate_batch(race_id=1)
    assert result[101] == SPEED_INDEX_MEAN
```

**Testing Error Cases:**
```python
def test_race_not_found_returns_mean(self) -> None:
    """Non-existent race returns default."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    calc = SpeedIndexCalculator(db=db)
    result = calc.calculate(race_id=999, horse_id=1)
    assert result == SPEED_INDEX_MEAN
```

**Testing with Mutable Class State:**
```python
def test_cache_persistence(self) -> None:
    """Cache is reused within same Calculator instance."""
    calc = SpeedIndexCalculator(db=MagicMock())
    calc._std_time_cache[("05", 1600, "芝", "良")] = (93.0, 2.0)

    # Second call uses cached value (no additional DB query)
    score1 = calc._single_race_speed_score(...)
    score2 = calc._single_race_speed_score(...)

    assert score1 == score2
```

## Test Naming Convention

Test method names describe the scenario being tested:

```python
# Good names
def test_fast_horse_gets_above_mean(self) -> None:
def test_weight_correction_heavy(self) -> None:
def test_scratch_returns_none(self) -> None:
def test_no_past_data_returns_mean(self) -> None:
def test_relative_ordering_preserved(self) -> None:

# Pattern: test_<expected_behavior>_when_<condition>
# or: test_<input>_<action>_<output>
```

---

*Testing analysis: 2026-03-22*
