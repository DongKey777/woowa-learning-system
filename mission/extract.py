"""Java code → concept_id pattern extraction (F10 forward, D-D).

Tier 1 (regex annotations): Spring annotations — measured 95-98% precision
on DongKey777 archive sample. Uses ANNOTATION_TO_CONCEPT map (33 entries
covering ~80% of mission Java surface).

Tier 2 (regex method invocations): API call patterns like JdbcTemplate.query,
Stream.of, CompletableFuture.thenApply. Lighter than Treesitter AST; covers
common domain patterns at ~70% precision.

Output: MissionPattern dataclass list, persisted by mission/graph.py to
state/repos/<repo>/mission_patterns.json.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

# ── concept mapping tables ────────────────────────────────────────────────

ANNOTATION_TO_CONCEPT: dict[str, str] = {
    # Stereotypes
    "@Component": "spring/component-stereotypes",
    "@Service": "spring/component-stereotypes",
    "@Repository": "spring/component-stereotypes",
    "@Controller": "spring/mvc-controller-basics",
    "@RestController": "spring/mvc-controller-basics",
    "@Configuration": "spring/bean-di-basics",
    "@Bean": "spring/bean-di-basics",
    # DI
    "@Autowired": "spring/dependency-injection-basics",
    "@Inject": "spring/dependency-injection-basics",
    "@Qualifier": "spring/dependency-injection-basics",
    "@Value": "spring/configuration-properties",
    # MVC
    "@RequestMapping": "spring/mvc-controller-basics",
    "@GetMapping": "spring/mvc-controller-basics",
    "@PostMapping": "spring/mvc-controller-basics",
    "@PutMapping": "spring/mvc-controller-basics",
    "@DeleteMapping": "spring/mvc-controller-basics",
    "@PatchMapping": "spring/mvc-controller-basics",
    "@PathVariable": "spring/mvc-controller-basics",
    "@RequestBody": "spring/mvc-controller-basics",
    "@RequestParam": "spring/mvc-controller-basics",
    "@ResponseBody": "spring/mvc-controller-basics",
    # Transaction
    "@Transactional": "spring/transactional-propagation",
    # JPA / DB
    "@Entity": "database/jpa-basics",
    "@Table": "database/jpa-basics",
    "@Column": "database/jpa-basics",
    "@Id": "database/jpa-basics",
    "@GeneratedValue": "database/jpa-basics",
    "@OneToMany": "database/jpa-relationships",
    "@ManyToOne": "database/jpa-relationships",
    "@JoinColumn": "database/jpa-relationships",
    # Test
    "@Test": "testing/junit-basics",
    "@SpringBootTest": "testing/spring-boot-test",
    "@WebMvcTest": "testing/spring-boot-test",
    "@DataJpaTest": "testing/spring-boot-test",
    "@MockBean": "testing/spring-boot-test",
}

METHOD_TO_CONCEPT: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bJdbcTemplate\.\s*(query|queryForObject|update|batchUpdate)\b"), "database/jdbc-basics"),
    (re.compile(r"\bNamedParameterJdbcTemplate\.\s*\w+\b"), "database/jdbc-basics"),
    (re.compile(r"\bSimpleJdbcInsert\b"), "database/jdbc-basics"),
    (re.compile(r"\bResultSet\b"), "database/jdbc-basics"),
    (re.compile(r"\bRowMapper\b"), "database/jdbc-basics"),
    (re.compile(r"\bTransactionTemplate\.\s*execute\b"), "spring/transactional-propagation"),
    (re.compile(r"\bStream\.\s*(of|generate|iterate)\b"), "language/stream-api-basics"),
    (re.compile(r"\.\s*(stream|parallelStream)\s*\(\s*\)"), "language/stream-api-basics"),
    (re.compile(r"\bCollectors\.\s*\w+\b"), "language/stream-api-basics"),
    (re.compile(r"\bOptional\.\s*(of|ofNullable|empty)\b"), "language/optional-basics"),
    (re.compile(r"\bCompletableFuture\.\s*(supplyAsync|thenApply|thenCompose|allOf)\b"), "language/completablefuture-basics"),
    (re.compile(r"\bExecutorService\b"), "language/executor-service-basics"),
    (re.compile(r"\bsynchronized\b"), "language/synchronization-basics"),
    (re.compile(r"\bvolatile\b"), "language/synchronization-basics"),
    (re.compile(r"\bConcurrentHashMap\b"), "data-structure/concurrent-collections"),
    (re.compile(r"\bAtomicInteger\b|\bAtomicLong\b|\bAtomicReference\b"), "language/atomic-classes"),
    (re.compile(r"\bThrow new\s+\w*Exception\b", re.IGNORECASE), "language/exception-handling"),
    (re.compile(r"\btry\s*\(\s*\w"), "language/try-with-resources"),
]

EXCEPTION_TO_CONCEPT: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bDataIntegrityViolationException\b"), "database/constraint-violation"),
    (re.compile(r"\bOptimisticLockingFailureException\b"), "database/optimistic-locking"),
    (re.compile(r"\bEmptyResultDataAccessException\b"), "database/jdbc-basics"),
    (re.compile(r"\bNullPointerException\b"), "language/null-handling"),
    (re.compile(r"\bIllegalStateException\b"), "language/state-validation"),
]


@dataclass(frozen=True)
class MissionPattern:
    file: str
    line: int
    kind: str           # annotation | method | exception
    value: str          # matched text
    matched_concept_id: str
    confidence: float
    extractor: str      # regex_tier1 | regex_tier2


# ── extract API ───────────────────────────────────────────────────────────

ANNOTATION_PATTERN = re.compile(r"(@[A-Z]\w+)(?:\([^)]*\))?")
IMPORT_PATTERN = re.compile(r"^\s*import\s+([\w.]+)\s*;")

# When a file imports certain types, also detect bare-method invocations on
# their instances (e.g. `template.query(...)` after `import JdbcTemplate`).
IMPORT_TRIGGERED_METHODS: dict[str, list[tuple[re.Pattern, str]]] = {
    "JdbcTemplate": [
        (re.compile(r"\.\s*(query|queryForObject|queryForList|update|batchUpdate)\s*\("),
         "database/jdbc-basics"),
    ],
    "NamedParameterJdbcTemplate": [
        (re.compile(r"\.\s*(query|queryForObject|update|batchUpdate)\s*\("),
         "database/jdbc-basics"),
    ],
    "EntityManager": [
        (re.compile(r"\.\s*(persist|merge|remove|find|createQuery)\s*\("),
         "database/jpa-basics"),
    ],
    "Stream": [
        (re.compile(r"\.\s*(filter|map|collect|reduce|sorted)\s*\("),
         "language/stream-api-basics"),
    ],
}


def extract_from_text(file_path: str, text: str) -> list[MissionPattern]:
    """Extract patterns from a single Java source string."""
    patterns: list[MissionPattern] = []
    # First pass — detect imports to enable variable-call detection
    imported_types: set[str] = set()
    for line in text.splitlines():
        m = IMPORT_PATTERN.match(line)
        if m:
            short = m.group(1).rsplit(".", 1)[-1]
            imported_types.add(short)
    triggered_methods: list[tuple[re.Pattern, str]] = []
    for imp_type, methods in IMPORT_TRIGGERED_METHODS.items():
        if imp_type in imported_types:
            triggered_methods.extend(methods)

    for lineno, line in enumerate(text.splitlines(), start=1):
        # Tier 1 annotations
        for m in ANNOTATION_PATTERN.finditer(line):
            ann = m.group(1)
            concept = ANNOTATION_TO_CONCEPT.get(ann)
            if concept:
                patterns.append(MissionPattern(
                    file=file_path, line=lineno, kind="annotation",
                    value=ann, matched_concept_id=concept,
                    confidence=0.97, extractor="regex_tier1",
                ))
        # Tier 2 method invocations
        for pat, concept in METHOD_TO_CONCEPT:
            if pat.search(line):
                patterns.append(MissionPattern(
                    file=file_path, line=lineno, kind="method",
                    value=pat.pattern, matched_concept_id=concept,
                    confidence=0.75, extractor="regex_tier2",
                ))
        # Exception patterns
        for pat, concept in EXCEPTION_TO_CONCEPT:
            if pat.search(line):
                patterns.append(MissionPattern(
                    file=file_path, line=lineno, kind="exception",
                    value=pat.pattern, matched_concept_id=concept,
                    confidence=0.85, extractor="regex_tier2",
                ))
        # Import-triggered variable method calls
        for pat, concept in triggered_methods:
            if pat.search(line):
                patterns.append(MissionPattern(
                    file=file_path, line=lineno, kind="method",
                    value=pat.pattern, matched_concept_id=concept,
                    confidence=0.70, extractor="regex_tier2",
                ))
    return patterns


def extract_from_files(files: list[tuple[str, str]]) -> list[MissionPattern]:
    """Extract patterns from a list of (path, content) tuples."""
    out: list[MissionPattern] = []
    for path, content in files:
        if not path.endswith(".java"):
            continue
        out.extend(extract_from_text(path, content))
    return out


def extract_from_repo(
    repo_root: Path,
    src_glob: str = "src/**/*.java",
) -> list[MissionPattern]:
    """Walk a checked-out repo and extract patterns from all Java files."""
    files: list[tuple[str, str]] = []
    for p in repo_root.glob(src_glob):
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            files.append((str(p.relative_to(repo_root)), content))
    return extract_from_files(files)


def save_patterns(
    patterns: list[MissionPattern],
    repo: str,
    state_root: Path,
    last_built_commit: str | None = None,
) -> Path:
    """Persist patterns to state/repos/<repo>/mission_patterns.json."""
    out_path = state_root / "repos" / repo / "mission_patterns.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "repo": repo,
        "last_built_commit": last_built_commit,
        "built_at": str(date.today()),
        "pattern_count": len(patterns),
        "patterns": [asdict(p) for p in patterns],
    }
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
