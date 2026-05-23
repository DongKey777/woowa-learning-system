"""Phase 8 D13 falsification — intent dispatcher accuracy on 50-sample set.

If accuracy < 95%, the unified entry hypothesis (D13) is falsified and
either (a) the heuristic needs more rules, or (b) AI session override
must intervene more frequently.
"""
from __future__ import annotations

from core.intent import detect_mode

# (prompt, repo, pending_self_assess, pending_drill, expected_mode)
SAMPLES = [
    # CS Q&A — pure concept questions (default cs_qa, 18)
    ("DI가 뭐야", None, None, None, "cs_qa"),
    ("Bean lifecycle 설명해", None, None, None, "cs_qa"),
    ("transaction propagation 종류", None, None, None, "cs_qa"),
    ("CSRF 방어 어떻게", None, None, None, "cs_qa"),
    ("hash collision 처리법", None, None, None, "cs_qa"),
    ("B+tree vs B-tree 차이", None, None, None, "cs_qa"),
    ("HTTP/2 vs HTTP/3", None, None, None, "cs_qa"),
    ("IoC 컨테이너 원리", None, None, None, "cs_qa"),
    ("AOP proxy 메커니즘", None, None, None, "cs_qa"),
    ("JPA dirty checking", None, None, None, "cs_qa"),
    ("optimistic locking", None, None, None, "cs_qa"),
    ("L1 cache miss", None, None, None, "cs_qa"),
    ("Saga pattern", None, None, None, "cs_qa"),
    ("WebSocket vs SSE", None, None, None, "cs_qa"),
    ("CAP theorem", None, None, None, "cs_qa"),
    ("DNS resolution order", None, None, None, "cs_qa"),
    ("OS scheduler 종류", None, None, None, "cs_qa"),
    ("dependency injection benefits", None, None, None, "cs_qa"),

    # tool_only (8)
    ("gradle build 어떻게 해", None, None, None, "tool_only"),
    ("git rebase 충돌 났어", None, None, None, "tool_only"),
    ("intellij에서 단축키", None, None, None, "tool_only"),
    ("docker run 옵션", None, None, None, "tool_only"),
    ("brew install gh 안 됨", None, None, None, "tool_only"),
    ("docker-compose up 실패", None, None, None, "tool_only"),
    ("kubectl pod 상태 확인", None, None, None, "tool_only"),
    ("npm install 후 에러", None, None, None, "tool_only"),

    # coaching — repo + coaching keyword (10)
    ("내 코드 어떻게 리팩토링?", "roomescape-admin", None, None, "coaching"),
    ("내 작업 리뷰해줘", "roomescape", None, None, "coaching"),
    ("PR 어떻게 짜야 할까", "roomescape-admin", None, None, "coaching"),
    ("Controller 분리 어떻게 해야", "lotto", None, None, "coaching"),
    ("내 코드 고치는 방법", "javachess", None, None, "coaching"),
    ("코칭 부탁해", "missionrepo", None, None, "coaching"),
    ("내 작업 리뷰", "x", None, None, "coaching"),
    ("리팩토링 어떻게", "y", None, None, "coaching"),
    ("내 코드 어떻게 해야", "z", None, None, "coaching"),
    ("PR 가이드 어떻게 짜야", "w", None, None, "coaching"),

    # retro — keyword wins even with repo (8)
    ("내 PR 흐름 보여줘", "roomescape", None, None, "retro"),
    ("반복 멘토 지적", None, None, None, "retro"),
    ("PR 37 정밀", "roomescape", None, None, "retro"),
    ("내가 놓친 거 뭐야", None, None, None, "retro"),
    ("PR 타임라인", "x", None, None, "retro"),
    ("3사이클 동안 같은 지적", None, None, None, "retro"),
    ("내 흐름 보여줘", None, None, None, "retro"),
    ("정밀 비교", None, None, None, "retro"),

    # self_assess — pending trigger + score reply (3)
    ("8점", None, {"trigger_session_id": "s1"}, None, "self_assess"),
    ("잘 모르겠어", None, {"trigger_session_id": "s2"}, None, "self_assess"),
    ("7/10", None, {"trigger_session_id": "s3"}, None, "self_assess"),

    # drill — pending drill + answer (3)
    ("DI는 의존성 주입을 컨테이너가 처리하는 패턴이다.", None, None, {"id": "d1"}, "drill"),
    ("Bean은 컨테이너가 관리하는 객체이다.", None, None, {"id": "d2"}, "drill"),
    ("IoC는 제어의 역전을 의미한다.", None, None, {"id": "d3"}, "drill"),
]


def test_intent_dispatch_accuracy_50_sample() -> None:
    """D13 falsification gate — must hit ≥95% accuracy."""
    assert len(SAMPLES) == 50, f"expected 50 samples, got {len(SAMPLES)}"

    correct = 0
    failures: list[tuple[str, str, str]] = []
    for prompt, repo, pending_sa, pending_drill, expected in SAMPLES:
        decision = detect_mode(
            prompt,
            repo=repo,
            pending_self_assessment=pending_sa,
            pending_drill=pending_drill,
        )
        if decision.mode == expected:
            correct += 1
        else:
            failures.append((prompt, expected, decision.mode))

    accuracy = correct / len(SAMPLES)
    report = "\n".join(f"  '{p}' expected={e} got={g}" for p, e, g in failures)
    assert accuracy >= 0.95, f"accuracy {accuracy:.2%} below 95% gate\n{report}"
