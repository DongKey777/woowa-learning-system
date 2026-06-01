"""Router paraphrase-recall leakage probe.

Measures how brittle the keyword router is to natural rephrasing. For each
ask-path mode (A-N + the base modes it competes with) we hand-author natural
learner utterances that DELIBERATELY avoid the literal keyword substrings in
core.intent.*_KEYWORDS, then route them through the real entrypoint
(core.router.route, which includes the f11 fast-path and the cs_qa→tier_0
guard) and report:

  * per-mode recall  = fraction of held-out paraphrases that still reach the
                       intended mode (pure keyword generalization),
  * confusion        = where the misses actually landed,
  * control precision= base-mode utterances (cs_qa / coaching / retro) that
                       must NOT be grabbed by an A-N mode (false positives).

This is a measurement, not a gate — it prints numbers and exits 0. The same
held-out set doubles as the eval corpus for whichever routing upgrade
(semantic / AI-session-driven) we adopt next.

Run: WOOWA_SESSION_MODE=development python3 tests/benchmarks/router_paraphrase_recall.py
"""
from __future__ import annotations

import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.router import route  # noqa: E402

REPO = "spring-roomescape-waiting"  # repo-gated modes need a repo present.

# mode -> natural held-out paraphrases (NONE contain the mode's keyword
# substrings, so a hit means real generalization, a miss means leakage).
PARAPHRASES: dict[str, list[str]] = {
    "pr_diff_evolution": [
        "리뷰 받고 코드를 어떻게 고쳐나갔는지 단계별로 보고 싶어",
        "PR 올린 뒤로 멘토 피드백 따라 뭐가 달라졌어?",
        "내 코드가 리뷰 거치면서 어떻게 발전했나",
        "어느 파일이 리뷰를 제일 많이 받았어?",
        "냄새나는 코드 푸시하기 전에 미리 잡아줘",
        "리뷰 댓글이랑 그 다음 커밋을 연결해서 보여줄래",
        "지적 많이 받은 파일 위치 짚어줘",
        "리뷰 한 바퀴 돌 때마다 코드가 어떻게 바뀌었나",
        "고친 흔적을 리뷰 순서대로 정리해줘",
        "푸시 전에 문제될 만한 패턴 좀 봐줘",
    ],
    "cross_mission": [
        "예전 미션에서 했던 실수 또 하고 있는지 봐줘",
        "여러 미션 통틀어서 자꾸 막히는 부분 있어?",
        "전에 배운 개념을 이번 미션에서도 쓰고 있나",
        "미션 넘어가면서 실력이 어떻게 쌓였어?",
        "앞 미션에서 약했던 게 지금도 약한지",
        "미션들 가로질러서 되풀이되는 패턴 찾아줘",
        "전에 짚인 문제가 또 나오는지 추적해줘",
        "미션 바뀌어도 똑같이 헤매는 개념 뭐야",
        "여태 한 과제들에서 공통으로 부족한 거",
        "한 미션에서 배운 걸 다음 미션에 써먹었는지",
    ],
    "memory_review": [
        "오래돼서 가물가물한 개념 다시 짚어줘",
        "내가 한 번도 안 들여다본 개념 뭐 있어?",
        "코드엔 쓰는데 정작 모르고 넘어간 거 알려줘",
        "슬슬 잊을 때 된 개념 리마인드 해줘",
        "예전에 봤는데 지금 떠오르지 않는 거",
        "놓치고 지나간 빈틈 개념 찾아줘",
        "복습 타이밍 된 거 추려줘",
        "안 보고 지나친 영역 정리해줘",
        "한동안 안 본 개념들 다시 꺼내줘",
        "머릿속에서 흐려진 거 다시 확인하고 싶어",
    ],
    "pr_review": [
        "멘토가 달아준 의견들 한눈에 보여줘",
        "내가 아직 답 못 한 리뷰 있어?",
        "지난 PR에서 받은 피드백 추려줘",
        "리뷰어가 짚어준 거 모아서 정리해줄래",
        "스레드 중에 아직 안 풀린 거 뭐야",
        "내 PR에 달린 코멘트 쭉 보고 싶어",
        "멘토 지적사항 목록으로 만들어줘",
        "리뷰 의견 종합해서 요약해줘",
        "아직 대응 안 한 리뷰 골라줘",
        "이번 PR 전반적인 평가 어땠어",
    ],
    "reviewer_profile": [
        "이 멘토 보통 어디를 많이 짚어?",
        "내 리뷰어 어떤 식으로 피드백 주는 사람이야?",
        "멘토가 주로 보는 관점이 뭐야",
        "리뷰어 취향 같은 거 있어?",
        "이 사람 리뷰할 때 버릇 있나",
        "멘토가 자주 강조하는 게 뭐야",
        "리뷰어가 뭘 중요하게 여기는지 알려줘",
        "내 멘토 피드백 경향 분석해줘",
        "이 리뷰어는 깐깐한 편이야?",
        "멘토별로 보는 포인트가 다른가",
    ],
    "learning_path": [
        "이제 뭐 공부하면 좋을까?",
        "다음 단계로 뭘 보면 돼?",
        "이거 이해하려면 먼저 뭘 알아야 해?",
        "어떤 순서로 배우는 게 효율적이야",
        "기초부터 차근차근 뭐부터 잡지",
        "이 개념 다음에 자연스럽게 이어지는 거",
        "지금 수준에서 다음에 도전할 만한 거",
        "뭘 알아야 이걸 제대로 이해해?",
        "학습 로드맵 짜줘",
        "이다음에 익히면 좋은 주제 추천해줘",
    ],
    "pr_meta": [
        "내 PR 한 번에 너무 많이 바꾼 거 아냐?",
        "설명을 충분히 적었는지 봐줘",
        "커밋을 너무 크게 묶었나",
        "PR 사이즈 적당한지 점검해줘",
        "변경 규모가 리뷰하기 버거운 수준이야?",
        "PR 본문이 부실한지 봐줄래",
        "커밋 쪼개는 단위가 적절한가",
        "이번 PR 너무 방대하지 않아?",
        "한 PR에 너무 많은 걸 담았는지",
        "PR 설명이랑 제목 괜찮아?",
    ],
    "thread_recon": [
        "리뷰 댓글 오간 거 처음부터 끝까지 보여줘",
        "그 스레드에서 무슨 얘기 주고받았어?",
        "멘토랑 나눈 대화 통째로 복원해줘",
        "댓글 타래 흐름 따라가게 정리해줘",
        "리뷰에서 오간 논의 쭉 보고 싶어",
        "그 코멘트 밑에 달린 답변들 다 보여줘",
        "리뷰 주고받은 맥락 살려서 보여줄래",
        "대화 이어진 거 순서대로 펼쳐줘",
        "그 리뷰 논쟁 어떻게 흘러갔어",
        "스레드 전체 맥락 재구성해줘",
    ],
    "temporal": [
        "첫 리뷰 받기까지 며칠 걸렸어?",
        "PR 머지되는 데 얼마나 시간 들었어",
        "리뷰가 한참 안 와서 멈췄던 적 있어?",
        "리뷰 사이 간격이 얼마나 떴어",
        "PR이 오래 방치된 구간 있어?",
        "리뷰 받는 텀이 보통 어느 정도야",
        "응답 기다린 시간 분석해줘",
        "리뷰 사이클이 늘어진 데 짚어줘",
        "PR 올리고 첫 반응까지 시간 봐줘",
        "어디서 제일 오래 지체됐어",
    ],
    "meta_analytics": [
        "내가 반복해서 헷갈려하는 주제 뭐야?",
        "어떤 걸 자꾸 다시 물어보는 경향 있어?",
        "내 공부 습관 좀 분석해줘",
        "내가 제일 많이 검색한 개념이 뭐야",
        "학습하면서 자주 돌아오는 질문 패턴",
        "내가 어디에 시간 많이 쓰는지 보여줘",
        "질문 경향 통계 내줘",
        "나 어떤 분야 위주로 묻는 편이야?",
        "내 학습 데이터에서 보이는 특징",
        "공부 흐름에서 반복되는 행동",
    ],
    "cohort": [
        "동기들이랑 비교하면 내 PR 어느 정도야?",
        "다른 사람들 평균이랑 내 거 비교해줘",
        "같이 하는 사람들 사이에서 내가 어디쯤이야",
        "내 PR이 또래보다 큰 편이야 작은 편이야?",
        "다들 받는 리뷰 수랑 내 거 비교",
        "내 위치가 상위권이야 하위권이야",
        "또래 분포에서 내가 어디 박혀 있어",
        "남들 대비 내 리뷰 라운드 많은 편이야?",
        "동료들 기준으로 내 PR 어떤지",
        "비슷한 사람들이랑 견줘서 내 수준",
    ],
    "predict": [
        "이거 올리면 멘토가 어디 걸고넘어질까?",
        "푸시하기 전에 리뷰 미리 받아본 셈 쳐줘",
        "내 PR 어떤 지적 들어올지 예상해줘",
        "올리면 무슨 코멘트 달릴 것 같아?",
        "리뷰 나오기 전에 미리 시뮬해줘",
        "제출 전에 약점 미리 짚어줘",
        "멘토가 뭐라고 할지 가늠해줄래",
        "이대로 올리면 리뷰가 어떨 것 같아",
        "예상되는 피드백 미리 알려줘",
        "올리기 전에 무슨 말 들을지 예측",
    ],
    "f11_anchor": [
        "다른 크루는 이 문제 어떻게 풀었어?",
        "같은 미션 다른 사람 코드 보여줘",
        "동료들은 이거 어떤 식으로 짰어?",
        "남들 구현 방식 궁금해",
        "다른 사람 PR 참고하고 싶어",
        "비슷한 미션 한 다른 크루 코드 보자",
        "내 방식 말고 다른 풀이도 보여줘",
        "옆 사람은 어떻게 했나",
        "크루원들 코드에서 배울 점 찾아줘",
        "다른 팀 풀이 좀 보여줘",
    ],
}

# Base-mode controls — must NOT be grabbed by an A-N mode.
CONTROLS: dict[str, list[str]] = {
    "cs_qa": [
        "Bean DI가 뭐야?",
        "트랜잭션 격리 수준 설명해줘",
        "DI랑 IoC 차이가 뭔데?",
        "JPA 영속성 컨텍스트가 뭐야?",
        "스프링 빈 생명주기 알려줘",
    ],
    "coaching": [
        "이 컨트롤러 어떻게 리팩토링하지?",
        "내 서비스 코드 개선점 알려줘",
    ],
    "retro": [
        "내 PR 흐름 보여줘 리뷰",  # test_intent_prompt.py:50 guard
        "내 PR 활동 요약해줘",
    ],
}

A_N_MODES = list(PARAPHRASES.keys())


def _run() -> None:
    print("=" * 72)
    print("ROUTER PARAPHRASE-RECALL LEAKAGE PROBE")
    print(f"repo={REPO!r}  (route() — incl. f11 fast-path + cs_qa guard)")
    print("=" * 72)

    total_hit = 0
    total = 0
    per_mode: list[tuple[str, int, int]] = []
    misses: list[tuple[str, str, str]] = []  # (mode, prompt, predicted)

    for mode, prompts in PARAPHRASES.items():
        hit = 0
        for p in prompts:
            predicted = route(p, repo=REPO).mode
            if predicted == mode:
                hit += 1
            else:
                misses.append((mode, p, predicted))
        per_mode.append((mode, hit, len(prompts)))
        total_hit += hit
        total += len(prompts)

    print("\nPER-MODE RECALL (held-out paraphrases):")
    for mode, hit, n in sorted(per_mode, key=lambda x: x[1] / x[2]):
        bar = "█" * round(20 * hit / n)
        print(f"  {mode:<20} {hit:>2}/{n:<2}  {hit/n:5.0%}  {bar}")
    print(f"\n  OVERALL A-N RECALL: {total_hit}/{total} = {total_hit/total:.1%}")

    print("\nMISSES (where leakage landed):")
    by_target = Counter(pred for _, _, pred in misses)
    for mode, p, pred in misses:
        print(f"  [{mode} → {pred}]  {p}")
    print("\n  miss sink distribution:",
          dict(by_target.most_common()))

    print("\nCONTROL PRECISION (base-mode utterances must stay out of A-N):")
    leaks = 0
    for expected, prompts in CONTROLS.items():
        for p in prompts:
            predicted = route(p, repo=REPO).mode
            leaked = predicted in A_N_MODES
            flag = "  <-- LEAK" if leaked else ""
            if leaked:
                leaks += 1
            print(f"  [{expected} → {predicted}]{flag}  {p}")
    print(f"\n  control leaks into A-N modes: {leaks}")
    print("=" * 72)


if __name__ == "__main__":
    _run()
