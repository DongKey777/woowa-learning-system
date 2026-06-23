"""Tests for mission.predict — family K (mode predict).

Read-only synthesis of three prebuilt artifacts (reviewer_profile.json,
cohort.json, pr_diff_evolution.json) into a pre-push prediction. No SQLite, no
git — fixtures are hand-written JSON under a tmp_path state_root.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission.predict import analyze_repo, save_predict  # noqa: E402

LEARNER = "DongKey777"


def _write(state_root: Path, repo: str, name: str, payload: dict) -> None:
    out = state_root / "repos" / repo / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _reviewer_profile() -> dict:
    return {
        "status": "ok",
        "reviewer_login": "hyeonic",
        "nickname": "hyeonic",
        "recurring_topics": [
            {"topic": "로직을", "n": 10},
            {"topic": "도메인 책임", "n": 9},
            {"topic": "예외 처리", "n": 7},
        ],
        "hotspot_files": [
            {"file": "ReservationService.java", "n": 10},
            {"file": "Reservation.java", "n": 9},
            {"file": "GlobalExceptionHandler.java", "n": 6},
        ],
        "review_states": {"APPROVED": 8, "CHANGES_REQUESTED": 15, "COMMENTED": 6},
    }


def _cohort() -> dict:
    return {
        "status": "ok",
        "cohort": {
            "n": 374,
            "median_total_changes": 3388,
            "median_changed_files": 63,
            "median_commits": 53,
            "median_reviews_received": 5,
            "median_review_rounds": 5,
        },
        "learner_prs": [
            {"number": 390, "total_changes": 4176, "review_rounds": 3},
            {"number": 388, "total_changes": 2100, "review_rounds": 2},
        ],
        "comparison": [
            {"metric": "total_changes", "learner_value": 4176,
             "cohort_median": 3388, "percentile": 75},
            {"metric": "changed_files", "learner_value": 58,
             "cohort_median": 63, "percentile": 36},
            {"metric": "review_rounds", "learner_value": 3,
             "cohort_median": 5, "percentile": 35},
        ],
    }


def _pr_diff_evolution() -> dict:
    return {
        "status": "ok",
        "clone_available": True,
        "prs": [
            {
                "number": 388,
                "merged": True,
                "hotspots": [{"path": "src/main/java/x/Old.java", "comments": 99}],
                "smells": [],
            },
            {
                "number": 390,
                "merged": True,
                "hotspots": [
                    {"path": "src/main/java/roomescape/reservation/ReservationService.java",
                     "comments": 6},
                    {"path": "src/test/java/roomescape/reservation/JdbcReservationRepositoryTest.java",
                     "comments": 5},
                ],
                "smells": [
                    {"path": "src/main/java/roomescape/admin/AdminReservationTimeController.java",
                     "concept_id": "spring/mvc-controller-basics",
                     "value": "@RestController", "line": 17, "kind": "annotation"},
                    {"path": "src/main/java/roomescape/reservation/Reservation.java",
                     "concept_id": "jpa/entity-basics",
                     "value": "public Long id", "line": 12, "kind": "field"},
                ],
            },
        ],
    }


def test_all_inputs_present_status_ok(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "reviewer_profile", _reviewer_profile())
    _write(tmp_path, "demo", "cohort", _cohort())
    _write(tmp_path, "demo", "pr_diff_evolution", _pr_diff_evolution())
    rep = analyze_repo("demo", LEARNER, tmp_path)

    assert rep["repo"] == "demo"
    assert rep["status"] == "ok"
    assert rep["learner_login"] == LEARNER
    assert rep["inputs"] == {"reviewer_profile": True, "cohort": True,
                             "pr_diff_evolution": True}


def test_likely_review_topics_from_reviewer_profile(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "reviewer_profile", _reviewer_profile())
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["likely_review_topics"] == [
        {"topic": "로직을", "mentions": 10},
        {"topic": "도메인 책임", "mentions": 9},
        {"topic": "예외 처리", "mentions": 7},
    ]
    assert rep["counts"]["likely_topics"] == 3


def test_likely_review_topics_capped_at_8(tmp_path: Path) -> None:
    profile = _reviewer_profile()
    profile["recurring_topics"] = [
        {"topic": f"주제{i}", "n": 20 - i} for i in range(12)]
    _write(tmp_path, "demo", "reviewer_profile", profile)
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert len(rep["likely_review_topics"]) == 8
    assert rep["likely_review_topics"][0] == {"topic": "주제0", "mentions": 20}


def test_hotspot_basename_merge_combined_sources(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "reviewer_profile", _reviewer_profile())
    _write(tmp_path, "demo", "pr_diff_evolution", _pr_diff_evolution())
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_file = {h["file"]: h for h in rep["likely_hotspot_files"]}

    # ReservationService.java is in BOTH C (n=10) and H most-recent (comments=6):
    # merged by basename, review_comments = max(10, 6) = 10, both sources.
    rs = by_file["ReservationService.java"]
    assert rs["review_comments"] == 10
    assert sorted(rs["sources"]) == ["pr_diff_evolution", "reviewer_profile"]

    # Reservation.java only in C (n=9).
    assert by_file["Reservation.java"]["review_comments"] == 9
    assert by_file["Reservation.java"]["sources"] == ["reviewer_profile"]

    # Test file only in H most-recent PR (comments=5).
    tf = by_file["JdbcReservationRepositoryTest.java"]
    assert tf["review_comments"] == 5
    assert tf["sources"] == ["pr_diff_evolution"]

    # PR 388's Old.java must NOT appear — only the most-recent PR (390) is used.
    assert "Old.java" not in by_file

    # Sorted by review_comments desc.
    counts = [h["review_comments"] for h in rep["likely_hotspot_files"]]
    assert counts == sorted(counts, reverse=True)


def test_smell_warnings_from_most_recent_pr(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "pr_diff_evolution", _pr_diff_evolution())
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["smell_warnings"] == [
        {"concept_id": "spring/mvc-controller-basics", "value": "@RestController",
         "file": "src/main/java/roomescape/admin/AdminReservationTimeController.java",
         "line": 17},
        {"concept_id": "jpa/entity-basics", "value": "public Long id",
         "file": "src/main/java/roomescape/reservation/Reservation.java",
         "line": 12},
    ]
    assert rep["counts"]["smells"] == 2


def test_review_load_projection_pulls_real_numbers(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "reviewer_profile", _reviewer_profile())
    _write(tmp_path, "demo", "cohort", _cohort())
    rep = analyze_repo("demo", LEARNER, tmp_path)
    proj = rep["review_load_projection"]
    # percentile from comparison row metric == total_changes.
    assert proj["pr_size_percentile"] == 75
    # learner_prs[0] is most recent (number 390) → review_rounds 3.
    assert proj["learner_review_rounds"] == 3
    assert proj["cohort_median_review_rounds"] == 5
    assert proj["mentor_changes_requested"] == 15
    assert proj["mentor_approved"] == 8
    # Sentence is built only from these real numbers.
    s = proj["projection"]
    assert "75" in s and "3" in s and "5" in s and "15" in s and "8" in s
    assert s != "예측에 쓸 과거 데이터가 아직 부족해."


def test_partial_inputs_only_reviewer_profile(tmp_path: Path) -> None:
    """Only C present → flags correct, cohort/H sections empty, projection
    uses only mentor numbers."""
    _write(tmp_path, "demo", "reviewer_profile", _reviewer_profile())
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["status"] == "ok"
    assert rep["inputs"] == {"reviewer_profile": True, "cohort": False,
                             "pr_diff_evolution": False}
    # topics + hotspots from C; smells empty (no H).
    assert rep["likely_review_topics"]
    assert rep["likely_hotspot_files"]
    assert rep["smell_warnings"] == []
    proj = rep["review_load_projection"]
    assert proj["pr_size_percentile"] is None
    assert proj["learner_review_rounds"] is None
    assert proj["cohort_median_review_rounds"] is None
    assert proj["mentor_changes_requested"] == 15
    assert proj["mentor_approved"] == 8
    # projection uses only the available mentor numbers.
    assert "15" in proj["projection"] and "8" in proj["projection"]
    assert proj["projection"] != "예측에 쓸 과거 데이터가 아직 부족해."


def test_partial_inputs_only_cohort(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "cohort", _cohort())
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["inputs"] == {"reviewer_profile": False, "cohort": True,
                             "pr_diff_evolution": False}
    assert rep["likely_review_topics"] == []
    assert rep["likely_hotspot_files"] == []
    assert rep["smell_warnings"] == []
    proj = rep["review_load_projection"]
    assert proj["pr_size_percentile"] == 75
    assert proj["learner_review_rounds"] == 3
    assert proj["cohort_median_review_rounds"] == 5
    assert proj["mentor_changes_requested"] is None
    assert proj["mentor_approved"] is None


def test_non_ok_input_treated_unavailable(tmp_path: Path) -> None:
    """A file present but status != 'ok' counts as unavailable."""
    bad = _cohort()
    bad["status"] = "missing_archive"
    _write(tmp_path, "demo", "cohort", bad)
    _write(tmp_path, "demo", "reviewer_profile", _reviewer_profile())
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["inputs"]["cohort"] is False
    assert rep["review_load_projection"]["pr_size_percentile"] is None


def test_zero_inputs_missing_status_and_fallback(tmp_path: Path) -> None:
    rep = analyze_repo("nonexistent", LEARNER, tmp_path)
    assert rep["status"] == "missing_inputs"
    assert rep["inputs"] == {"reviewer_profile": False, "cohort": False,
                             "pr_diff_evolution": False}
    assert rep["likely_review_topics"] == []
    assert rep["likely_hotspot_files"] == []
    assert rep["smell_warnings"] == []
    assert rep["counts"] == {"likely_topics": 0, "likely_hotspots": 0, "smells": 0}
    assert rep["review_load_projection"]["projection"] == \
        "예측에 쓸 과거 데이터가 아직 부족해."


def test_corrupt_json_does_not_crash(tmp_path: Path) -> None:
    out = tmp_path / "repos" / "demo" / "reviewer_profile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{ not valid json", encoding="utf-8")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["inputs"]["reviewer_profile"] is False
    assert rep["status"] == "missing_inputs"


def test_save_round_trip_to_right_path(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "reviewer_profile", _reviewer_profile())
    _write(tmp_path, "demo", "cohort", _cohort())
    _write(tmp_path, "demo", "pr_diff_evolution", _pr_diff_evolution())
    rep = analyze_repo("demo", LEARNER, tmp_path)
    out = save_predict(rep, tmp_path)
    assert out == tmp_path / "repos" / "demo" / "predict.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    documented = ("repo", "status", "learner_login", "inputs",
                  "likely_review_topics", "likely_hotspot_files",
                  "smell_warnings", "review_load_projection", "counts",
                  "generated_at")
    assert set(data.keys()) == set(documented)
    assert len(documented) == 10
    assert data["repo"] == "demo"
    assert data["status"] == "ok"
    assert data["learner_login"] == LEARNER


def test_now_timestamp_is_deterministic(tmp_path: Path) -> None:
    rep = analyze_repo("demo", LEARNER, tmp_path, now=0.0)
    assert rep["generated_at"].startswith("1970-01-01T00:00:00")


def test_projection_sentence_small_pr_not_inverted() -> None:
    # percentile = 나보다 작거나 같은 동료 비율 (coach.py 정의): pct=4 = 작은 PR.
    # 기존 '상위 4 percentile'은 작은 PR을 '큰/상위'로 뒤집어 학습자에게 노출했다.
    from mission.predict import _projection_sentence
    s = _projection_sentence(4, None, None, None, None)
    assert "상위" not in s
    assert "동기 4%보다 큰" in s
