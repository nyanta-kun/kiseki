# Coding Conventions

**Analysis Date:** 2026-03-22

## Naming Patterns

**Files:**
- Module files use lowercase with underscores: `race_importer.py`, `jvlink_parser.py`
- Test files follow pattern: `test_<module_name>.py` (e.g., `test_speed_index.py`)
- Package indicator files: `__init__.py`
- Configuration files: `config.py`, `constants.py`

**Functions:**
- Snake_case for all functions: `calculate_batch()`, `_get_past_results_for_horse()`
- Prefix private/internal functions with single underscore: `_make_result()`, `_compute_scores()`
- Prefix internal helpers in modules with underscore: `_finish_time_to_decimal()`, `_position_score()`
- Test helper functions follow pattern: `_make_<object>()`, `_build_<object>()`

**Variables:**
- Snake_case throughout: `horse_ids`, `race_id`, `std_time_cache`
- Module-level constants UPPERCASE_WITH_UNDERSCORES: `LOOKBACK_RACES`, `MIN_STD_SAMPLE`, `BASE_WEIGHT`
- Type aliases follow class naming: `dict[int, list[Any]]`
- Private cache attributes: `self._std_time_cache`

**Types:**
- PascalCase for class names: `IndexCalculator`, `SpeedIndexCalculator`, `FrameBiasCalculator`
- Class names describe agent roles: `RaceImporter`, `JockeyIndexCalculator`
- Use SQLAlchemy Mapped type hints: `Mapped[int]`, `Mapped[str | None]`
- Use `from __future__ import annotations` for forward references

**Imports Order:**
1. Built-in modules (`logging`, `typing`, `collections`, etc.)
2. Third-party libraries (`sqlalchemy`, `pydantic`, `fastapi`, etc.)
3. Relative imports from current package (`from ..db.models import...`)
4. Import `logger` at module top level: `logger = logging.getLogger(__name__)`

**Path Aliases:**
- Relative imports from package root: `from ..db.models import Race, RaceEntry`
- From same parent: `from .base import IndexCalculator`
- Never use hardcoded absolute paths

## Code Style

**Formatting:**
- Tool: Ruff (Python linter/formatter)
- Line length: 100 characters (enforced in `pyproject.toml`)
- Target Python version: 3.12+

**Linting:**
- Tool: Ruff with rules: E, F, I, N, W, UP, B, A, SIM
- E: PEP 8 errors
- F: PyFlakes (undefined names)
- I: isort (import ordering)
- N: pep8-naming
- W: PEP 8 warnings
- UP: pyupgrade (modern Python syntax)
- B: flake8-bugbear (common bugs)
- A: flake8-builtins (builtin shadowing)
- SIM: flake8-simplify (code simplifications)

**Type Hints:**
- Mandatory on all functions (especially public methods)
- Use `from __future__ import annotations` for cleaner syntax
- Return types on all functions: `def calculate(self, race_id: int) -> float:`
- Use `| None` for optional types instead of `Optional[T]`
- Type hints on class attributes via `Mapped[]`: `id: Mapped[int]`

## Error Handling

**Patterns:**
- Database queries guard against None: `if not race:` followed by early return
- Most Agent methods return default value (e.g., `SPEED_INDEX_MEAN`) on missing data
- Exceptions logged at error level with context: `logger.error(f"Import error rec_id={rec_id}: {e}")`
- No silent failures: catch-all except blocks log immediately
- Data validation happens in importer layer before DB insert

**Logging:**
```python
# Initialize at module top
logger = logging.getLogger(__name__)

# Use appropriate levels
logger.warning(f"Race not found: race_id={race_id}")  # unexpected but recoverable
logger.debug(f"Standard time samples insufficient: course={course}")  # development info
logger.error(f"Import error: {e}")  # operational failures
```

**Comments:**
- Module docstrings required: Explain purpose and algorithm for Agents
- Function docstrings mandatory for public methods: Use Google/NumPy style
- Inline comments for non-obvious logic (e.g., weight correction formula)
- Section markers: `# ------- ---- --- ` (72 dashes) separate logical blocks

**Docstring Style:**
```python
def calculate(self, race_id: int, horse_id: int) -> float:
    """Single line summary.

    Longer description if needed.

    Args:
        race_id: DB horses.id
        horse_id: DB horses.id

    Returns:
        Index value (0-100).

    Raises:
        ValueError: If race_id is invalid.
    """
```

## Function Design

**Size:**
- Agent methods (calculate/calculate_batch): 10-30 lines, focus on orchestration
- Internal helper methods: 10-50 lines (breaks down complex logic)
- Utility functions: 5-20 lines
- Too many internal methods signals need for refactoring

**Parameters:**
- Max 4-5 parameters per function; use dataclass/dict if more needed
- Public methods take DB Session + IDs: `__init__(self, db: Session)`, `calculate(self, race_id: int, horse_id: int)`
- Helper functions take extracted objects, not IDs: `_compute_scores(self, rows: list[Any])`

**Return Values:**
- Explicit return types always
- `dict[int, float]` for batch results: `{horse_id: index_value}`
- `float | None` for optional computed values
- Empty dict `{}` (not None) when no entries to process
- Early return pattern: guard clauses at top of function

## Module Design

**Exports:**
- Public classes: `class SpeedIndexCalculator(IndexCalculator):`
- No `__all__` lists currently (implicit public API)
- Test modules export nothing (test discovery via naming)

**Agent Structure:**
All Agents follow `IndexCalculator` base pattern:
```python
class <AgentName>Calculator(IndexCalculator):
    def __init__(self, db: Session) -> None:
        super().__init__(db)
        self._cache = {}  # Internal state

    def calculate(self, race_id: int, horse_id: int) -> float:
        """Single horse calculation."""

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """All horses in race."""

    def _internal_helper(self) -> ...:
        """Private implementation details."""
```

**Constants Location:**
- Global constants in `src/utils/constants.py`: `SPEED_INDEX_MEAN = 50.0`
- Module-level constants in agent files: `LOOKBACK_RACES = 10`
- Magic numbers documented in docstrings

## Database Conventions

**Models:**
- All models inherit from `Base` (SQLAlchemy declarative)
- Use `Mapped[T]` type hints (SQLAlchemy 2.0 syntax)
- Schema always "keiba": `id: Mapped[int] = mapped_column(ForeignKey("keiba.races.id"))`
- Datetime fields use server defaults: `created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())`
- Primary keys: `id: Mapped[int] = mapped_column(primary_key=True)`
- Foreign key notation: `ForeignKey("keiba.table_name.column")`

**Queries:**
- Use ORM query syntax (not raw SQL): `self.db.query(Race).filter(Race.id == race_id).first()`
- Filter patterns: explicit conditions with `.filter(condition1, condition2)`
- N+1 prevention: batch load with joins: `.join(Race, RaceResult.race_id == Race.id)`
- Grouping results in memory (not DB) when logic is complex

## Configuration

**Settings:**
- Use Pydantic BaseSettings: `from pydantic_settings import BaseSettings`
- Environment variables via model attributes with defaults
- Load from `.env` file (never committed): `model_config = {"env_file": "../.env"}`
- Sensitive values (API keys, DB password) require `.env` entries
- Public configuration at module level: `settings = Settings()`

**Constants:**
- Domain constants in `src/utils/constants.py`
- JRA-VAN code mappings: `COURSE_NAMES`, `TRACK_CODE_MAP`, `GRADE_MAP`
- Index parameters: `SPEED_INDEX_MEAN`, `BASE_WEIGHT`
- Thresholds and weights in same file

## Version Control

**Commits:**
- Descriptive messages: "feat: Add speed index calculation" not "fix bug"
- Co-author tag required: `Co-Authored-By: Claude <noreply@anthropic.com>`
- `.env` files never committed (checked in `.gitignore`)

---

*Convention analysis: 2026-03-22*
