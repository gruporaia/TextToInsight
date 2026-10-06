"""
src/bird/data_loader.py

Data loader and parser module for the BIRD benchmark dataset.
Responsible for:
- Reading and validating question datasets (dev.json or mini_dev_sqlite.json).
- Filtering by database ID and difficulty level (simple, moderate, challenging).
- Deterministic uniform and stratified sampling with seed control.
- Formatting evaluation prompts with optional 'evidence' (hint) injection.
- Resolving and validating physical SQLite database paths.
"""

import json
import random
from pathlib import Path
# Type annotation for heterogeneous JSON values (dictionaries, lists, primitives)
from typing import Any

# Immutable tuple containing mandatory fields that every BIRD record must have.
REQUIRED_FIELDS = ("question_id", "db_id", "question", "SQL", "difficulty")

# Set containing the three canonical difficulty tiers of the BIRD benchmark.
VALID_DIFFICULTIES = {"simple", "moderate", "challenging"}

def load_bird_dev_examples(
    data_dir: str | Path,
    db_id: str | None = None,
    difficulty: str | None = None,
    dialect: str = "sqlite",
) -> list[dict[str, Any]]:
    """
    Loads and validates evaluation examples from the BIRD dataset.

    Searches for the dialect-specific question dataset file inside data_dir:
    - sqlite: 'dev.json' or 'mini_dev_sqlite.json'
    - mysql: 'mini_dev_mysql.json' or 'dev_mysql.json'
    - postgresql: 'mini_dev_postgresql.json' or 'dev_postgresql.json'

    Applies strict fail-fast validation to ensure no corrupted record is passed
    to downstream evaluation agents.

    Parameters:
        data_dir: Base directory containing BIRD dataset files (e.g. 'data/bird').
        db_id: Optional filter for a specific database ID (e.g. 'financial', 'superhero').
        difficulty: Optional filter by complexity level ('simple', 'moderate', 'challenging').
        dialect: SQL dialect ('sqlite', 'mysql', 'postgresql'). Default is 'sqlite'.

    Returns:
        List of dictionaries containing validated and filtered examples.

    Raises:
        ValueError: If dialect is unsupported, file is not a JSON list, or record lacks required fields.
        FileNotFoundError: If no compatible question dataset file is found.
        KeyError: If any mandatory field from REQUIRED_FIELDS is missing.
    """
    # 1. Guarantee data_dir is a Path instance, enabling .exists() and the '/' operator
    base_path = Path(data_dir)
    dialect_clean = dialect.lower().strip()

    # If user provided a direct file path, use it directly
    if base_path.is_file():
        dataset_path: Path | None = base_path
    else:
        # 2. Select dialect-specific candidate filenames
        if dialect_clean == "sqlite":
            candidate_names = ["dev.json", "mini_dev_sqlite.json", "mini_dev.json", "minidev.json"]
        elif dialect_clean == "mysql":
            candidate_names = ["mini_dev_mysql.json", "dev_mysql.json"]
        elif dialect_clean in ("postgresql", "postgres"):
            candidate_names = ["mini_dev_postgresql.json", "dev_postgresql.json"]
        else:
            raise ValueError(
                f"Unsupported SQL dialect '{dialect}'. Supported dialects are: 'sqlite', 'mysql', 'postgresql'."
            )

        candidate_files = [base_path / name for name in candidate_names]
        candidate_files.extend([base_path / "MINIDEV" / name for name in candidate_names])
        candidate_files.extend([base_path / "minidev" / name for name in candidate_names])

        # 3. Find inside any intermediate subfolder inside base_path (e.g. dev_20240627/dev.json)
        subfolder_candidates: list[Path] = []
        if base_path.is_dir():
            for sub in sorted(base_path.iterdir(), reverse=True):
                if sub.is_dir() and not sub.name.startswith("."):
                    for name in candidate_names:
                        candidate = sub / name
                        if candidate.is_file():
                            subfolder_candidates.append(candidate)

            # Fallback to deeper recursive search if not found in immediate subdirectories
            if not subfolder_candidates:
                for name in candidate_names:
                    matches = [
                        p for p in base_path.rglob(name)
                        if p.is_file() and not any(part.startswith(".") for part in p.parts)
                    ]
                    subfolder_candidates.extend(sorted(matches, reverse=True))

        all_candidates = candidate_files + subfolder_candidates
        dataset_path = next((p for p in all_candidates if p.exists() and p.is_file()), None)

        # 4. Ensure dev.json is visible in root as well as any intermediate subfolder
        if dataset_path and base_path.is_dir():
            import shutil
            # If found in a subfolder, make it visible in base_path root
            if dataset_path.parent != base_path:
                root_copy = base_path / dataset_path.name
                if not root_copy.exists():
                    try:
                        shutil.copy2(dataset_path, root_copy)
                    except Exception:
                        pass
            # If dev.json exists in root, ensure intermediate folders (e.g. dev_*) also have it visible
            root_dev = base_path / "dev.json"
            if root_dev.exists() and root_dev.is_file():
                for sub in base_path.iterdir():
                    if (
                        sub.is_dir()
                        and not sub.name.startswith(".")
                        and (sub.name.lower().startswith("dev") or sub.name.lower().startswith("minidev"))
                        and not sub.name.lower().startswith("dev_database")
                    ):
                        sub_dev = sub / "dev.json"
                        if not sub_dev.exists():
                            try:
                                shutil.copy2(root_dev, sub_dev)
                            except Exception:
                                pass

    # If no candidate file exists, fail fast with actionable guidance for the developer
    if not dataset_path:
        raise FileNotFoundError(
            f"BIRD dataset question file for dialect '{dialect}' not found in '{data_dir}'. "
            f"Checked root and all subdirectories for {[base_path / n for n in candidate_names]}. "
            f"Please run 'python scripts/setup_bird_data.py' first."
        )

    # 4. Open file in read mode with explicit UTF-8 encoding (avoids locale-dependent encoding errors)
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 5. Structural check: the BIRD dataset root must strictly be a list of examples
    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON list of examples in '{dataset_path}', but found {type(data).__name__}."
        )

    # Accumulator list for examples that pass all validation rules and active filters
    validated_examples: list[dict[str, Any]] = []

    # 6. Normalize filter inputs to lowercase and strip whitespace for case-insensitive matching
    target_difficulty = difficulty.lower().strip() if difficulty else None
    target_db_id = db_id.lower().strip() if db_id else None

    # 7. Iterate through each item tracking the index (idx) for precise error reporting
    for idx, item in enumerate(data):
        # Every item in the list must be a JSON object (represented as a Python dictionary)
        if not isinstance(item, dict):
            raise ValueError(f"Example at index {idx} in '{dataset_path}' is not a JSON object/dict.")

        # Fail-fast validation: verify presence of each mandatory field
        for field in REQUIRED_FIELDS:
            if field not in item:
                raise KeyError(
                    f"Missing mandatory field '{field}' in example index {idx} "
                    f"(question_id: {item.get('question_id', 'unknown')}) from '{dataset_path}'."
                )

        # Extract normalized attributes for filter comparison
        item_db_id = str(item["db_id"]).strip()
        item_difficulty = str(item["difficulty"]).lower().strip()

        # If a db_id filter is specified and does not match, skip to next item
        if target_db_id and item_db_id.lower() != target_db_id:
            continue

        # If a difficulty filter is specified and does not match, skip to next item
        if target_difficulty and item_difficulty != target_difficulty:
            continue

        # Item passed all checks and filters: append to results
        validated_examples.append(item)

    return validated_examples


def prepare_prompt_question(
    example: dict[str, Any],
    use_evidence: bool = True,
) -> str:
    """
    Constructs the final prompt question string sent to the agent / InsightEngine.

    In the BIRD benchmark, the 'evidence' field contains vital business rules and domain formulas.
    When enabled, hints are appended using the canonical format:
        f"{question}\\nHint: {evidence.strip()}"

    Parameters:
        example: Dictionary containing 'question' and optionally 'evidence'.
        use_evidence: Boolean toggle to include or exclude hints (essential for ablation studies).

    Returns:
        Formatted prompt question ready for evaluation.
    """
    # Extract question text with safe fallback and strip leading/trailing whitespace
    question = str(example.get("question", "")).strip()

    # Extract raw evidence value (can be None, empty string, or text)
    evidence = example.get("evidence")

    # Triple condition check:
    # 1. use_evidence toggle must be True
    # 2. evidence value must exist and not be falsy
    # 3. evidence must not be pure whitespace
    if use_evidence and evidence and str(evidence).strip():
        return f"{question}\nHint: {str(evidence).strip()}"

    # When evidence is absent or disabled, return the clean original question
    return question


def sample_examples(
    examples: list[dict[str, Any]],
    sample_size: int | None = None,
    seed: int | None = 42,
    stratify_by_difficulty: bool = False,
) -> list[dict[str, Any]]:
    """
    Selects a deterministic sample of questions for reproducible benchmark evaluation.

    Allows running small evaluation batches (e.g. N=10 or 20) to strictly conserve token budget.
    Ensures 100% cross-platform reproducibility by sorting examples by 'question_id'
    prior to invoking the pseudo-random generator.

    Parameters:
        examples: Source list of questions.
        sample_size: Target count of questions. If None or >= len(examples), returns all.
        seed: Random seed for deterministic reproducibility (Default: 42).
        stratify_by_difficulty: If True, preserves exact proportions of 'simple',
                               'moderate', and 'challenging' questions in the sample.

    Returns:
        Sampled list of question dictionaries.
    """
    # If sample_size is not specified or exceeds total count, return a shallow copy of all examples
    if sample_size is None or sample_size >= len(examples):
        return list(examples)

    # Sanity guard: requested sample size less than or equal to zero returns empty list
    if sample_size <= 0:
        return []

    # PREEMPTIVE DETERMINISTIC SORTING:
    # Guarantees identical input sequence to the pseudo-random generator regardless of OS,
    # filesystem differences, or dict hash ordering variations.
    sorted_examples = sorted(examples, key=lambda ex: ex.get("question_id", 0))

    # Instantiate an isolated Random generator using the specified seed.
    # An isolated instance prevents side-effects on global random state used by third-party packages.
    rng = random.Random(seed)

    # Case A: Standard uniform random sampling (canonical convention matching Spider)
    if not stratify_by_difficulty:
        return rng.sample(sorted_examples, k=sample_size)

    # Case B: Stratified sampling by difficulty tier (optimal for small budget-controlled runs)
    # Group examples into difficulty buckets
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ex in sorted_examples:
        diff = str(ex.get("difficulty", "unknown")).lower().strip()
        buckets.setdefault(diff, []).append(ex)

    total_count = len(sorted_examples)

    # Calculate proportional quota of questions to draw from each difficulty bucket
    allocated_counts: dict[str, int] = {}
    for diff, bucket in sorted(buckets.items()):
        # tier_ratio: proportion this tier represents in the overall dataset (e.g. 50% moderate = 0.50)
        tier_ratio = len(bucket) / total_count
        # Convert ratio to integer count, guaranteeing at least 1 question per tier when possible
        count = max(1, round(tier_ratio * sample_size))
        # Ensure quota does not exceed available examples in this bucket
        count = min(count, len(bucket))
        allocated_counts[diff] = count

    # Rounding adjustment: sum of rounded integers may slightly differ from sample_size (+1 or -1)
    current_sum = sum(allocated_counts.values())
    diff_keys = sorted(buckets.keys(), key=lambda k: len(buckets[k]), reverse=True)

    # If sum exceeds target sample_size, decrement from largest buckets
    while current_sum > sample_size:
        for k in diff_keys:
            if allocated_counts[k] > 1 and current_sum > sample_size:
                allocated_counts[k] -= 1
                current_sum -= 1

    # If sum falls short of target sample_size, increment in largest buckets
    while current_sum < sample_size:
        for k in diff_keys:
            if allocated_counts[k] < len(buckets[k]) and current_sum < sample_size:
                allocated_counts[k] += 1
                current_sum += 1

    # Deterministically sample from each bucket using the isolated generator
    sampled: list[dict[str, Any]] = []
    for diff, count in allocated_counts.items():
        tier_sample = rng.sample(buckets[diff], k=count)
        sampled.extend(tier_sample)

    # Re-sort final sample by question_id for consistent and predictable test progression
    return sorted(sampled, key=lambda ex: ex.get("question_id", 0))


def get_database_path(data_dir: str | Path, db_id: str) -> Path:
    """
    Locates and verifies the absolute path to a BIRD database's physical .sqlite file.

    Parameters:
        data_dir: Root directory of the BIRD dataset (e.g. 'data/bird').
        db_id: Database identifier (e.g. 'financial', 'california_schools').

    Returns:
        Path object pointing to the verified existing .sqlite file.

    Raises:
        FileNotFoundError: If the database file does not exist on disk.
    """
    base_dir = Path(data_dir)
    clean_db_id = str(db_id).strip()

    # Standardized lookup paths where the SQLite database file may reside
    candidate_paths = [
        base_dir / "dev_databases" / clean_db_id / f"{clean_db_id}.sqlite",
        base_dir / "dev_databases" / clean_db_id / f"{clean_db_id}.db",
        base_dir / "databases" / clean_db_id / f"{clean_db_id}.sqlite",
        base_dir / clean_db_id / f"{clean_db_id}.sqlite",
    ]

    # Return the first candidate that exists and is a file
    for path in candidate_paths:
        if path.exists() and path.is_file():
            return path.resolve()

    # Check intermediate subfolders (e.g. dev_20240627/dev_databases/<clean_db_id>/...)
    if base_dir.is_dir():
        for sub in sorted(base_dir.iterdir(), reverse=True):
            if sub.is_dir() and not sub.name.startswith("."):
                sub_candidates = [
                    sub / "dev_databases" / clean_db_id / f"{clean_db_id}.sqlite",
                    sub / "dev_databases" / clean_db_id / f"{clean_db_id}.db",
                    sub / "databases" / clean_db_id / f"{clean_db_id}.sqlite",
                    sub / clean_db_id / f"{clean_db_id}.sqlite",
                    sub / clean_db_id / f"{clean_db_id}.db",
                ]
                for p in sub_candidates:
                    if p.exists() and p.is_file():
                        return p.resolve()

        # Fallback recursive search across any folder in base_dir
        for ext in (".sqlite", ".db"):
            matches = [
                p for p in base_dir.rglob(f"{clean_db_id}{ext}")
                if p.is_file() and not any(part.startswith(".") for part in p.parts)
            ]
            if matches:
                return sorted(matches, reverse=True)[0].resolve()

    # If missing, raise explicit FileNotFoundError with checked locations
    raise FileNotFoundError(
        f"Database file not found for db_id '{db_id}'. "
        f"Checked location: '{candidate_paths[0]}' and subdirectories in '{base_dir}'. "
        f"Please ensure databases are extracted under '{base_dir / 'dev_databases'}'."
    )


def get_unique_db_ids(examples: list[dict[str, Any]]) -> list[str]:
    """
    Returns an alphabetically sorted list of unique database identifiers (db_id)
    present across the provided examples.
    """
    return sorted({str(ex["db_id"]).strip() for ex in examples if "db_id" in ex})


def get_difficulty_distribution(examples: list[dict[str, Any]]) -> dict[str, int]:
    """
    Calculates the frequency distribution of questions grouped by difficulty tier
    (e.g. {'simple': 148, 'moderate': 250, 'challenging': 102}).
    """
    counts: dict[str, int] = {}
    for ex in examples:
        diff = str(ex.get("difficulty", "unknown")).lower().strip()
        counts[diff] = counts.get(diff, 0) + 1
    return counts


def get_database_uri(
    data_dir: str | Path,
    db_id: str,
    dialect: str = "sqlite",
) -> str:
    """
    Constructs a SQLAlchemy-compatible database URI (connection string) for the specified database.

    Prepares TextToInsight for seamless multi-dialect execution (PR #15).
    Supports:
    - sqlite: returns 'sqlite:///{absolute_path_to_sqlite_file}'
    - mysql: returns 'mysql+pymysql://root:password@localhost:3306/{db_id}'
             (overridable via BIRD_MYSQL_URI environment variable template)
    - postgresql: returns 'postgresql+psycopg2://postgres:password@localhost:5432/{db_id}'
                  (overridable via BIRD_POSTGRES_URI environment variable template)

    Parameters:
        data_dir: Base directory for the BIRD dataset.
        db_id: Database name identifier (e.g. 'financial', 'california_schools').
        dialect: Target SQL dialect ('sqlite', 'mysql', 'postgresql'). Default is 'sqlite'.

    Returns:
        SQLAlchemy connection URI string.
    """
    import os

    clean_dialect = dialect.lower().strip()
    clean_db = str(db_id).strip()

    if clean_dialect == "sqlite":
        db_path = get_database_path(data_dir, clean_db)
        return f"sqlite:///{db_path.resolve()}"

    if clean_dialect == "mysql":
        base_template = os.getenv("BIRD_MYSQL_URI", "mysql+pymysql://root:password@localhost:3306/{db_id}")
        return base_template.format(db_id=clean_db)

    if clean_dialect in ("postgresql", "postgres"):
        base_template = os.getenv("BIRD_POSTGRES_URI", "postgresql+psycopg2://postgres:password@localhost:5432/{db_id}")
        return base_template.format(db_id=clean_db)

    raise ValueError(
        f"Unsupported SQL dialect '{dialect}' for URI generation. "
        f"Supported dialects: 'sqlite', 'mysql', 'postgresql'."
    )

