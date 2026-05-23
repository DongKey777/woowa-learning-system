# CLAUDE.md — Woowa Learning System (Clean-Slate RAG)

이 repo는 legacy `woowa-learning-hub`에서 7 cycle 동안 누적한 데이터/지식/lessons를 토대로 처음부터 재설계한 RAG 시스템이다. Legacy는 parallel 유지되며, 새 시스템이 **모든 면에서 압도** 시 legacy 삭제.

## Development principles

매 commit + plan revision에 다음 4 원칙 자체점검 (commit message에 포함):

### 1. Hypothesis-Driven Autonomy

Don't assume. Don't hide confusion. Don't ask the user when you can verify yourself.

Before implementing:
- State your assumptions explicitly.
- 모호 case 발생 시: **직접 가설 수립 → 측정/테스트 → 완벽 파악 → 직접 판단**. Ask the user 안 함.
- If multiple interpretations exist, list them all, then test each. Don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.

Ask the user only for genuine policy decisions:
- Scope changes (e.g. 새 subsystem 추가?)
- Priority/trade-off (latency vs quality)
- Time/budget (RunPod $5 spend OK?)
- Multi-system migration policy

For anything verifiable (function behavior, ROI between two approaches, file content, latency measurement) — verify yourself, don't ask.

### 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## Commit message template

```
<Phase N>: <subject>

Hypothesis: <한 줄>
Hypotheses tested + decisions autonomously made: <목록>
Verify: <verify command + result>
Decision rationale: <근거 1줄>

Self-check:
- [x] Hypothesis-Driven Autonomy: <ambiguity 발생 시 어떻게 가설+테스트로 해결했는지>
- [x] Simplicity First: <LOC vs target>
- [x] Surgical Changes: <scope>
- [x] Goal-Driven Execution: <verifiable hypothesis>
```

---

## Architecture summary

자세한 design decisions + falsification은 plan: `/Users/idonghun/.claude/plans/misty-giggling-valley.md`

- **Corpus**: 1 concept = 1 JSON entity (`corpus/concepts/<category>/<id>.json`). Schema-enforced.
- **Index**: 3211 concepts × dense (BGE-M3 1024-d) only. Sparse + lexical sidecar 폐기.
- **Router**: TOOL_TOKENS fast-path만. Else always-on retrieval + confidence-derived response style.
- **Eval**: AI session semantic judge (learner history 9992 events primary, fixture secondary).
- **Personalization**: Retrieval 후 post-process module (mechanical separation, cache-safe).
- **AI session**: corpus curator + retrieval reasoner + eval judge (3 roles).
- **Target LOC**: ≤1100 runtime (legacy 5000+ 대비 -78%).

---

## Out of scope (정직한 거절 + 근거)

- 외부 LLM API — `feedback_no_paid_llm_api.md`
- 모델 swap — 5회 reject 이력
- ColBERT — M4 RAM 한계
- Always-warm daemon — 사용자 거절
- Hand fixture expansion — scale 안 됨 입증
