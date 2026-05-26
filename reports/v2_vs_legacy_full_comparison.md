# Paradigm-v2 vs Legacy — full scenario comparison

- Scenarios: 14 (across 7 modes + 11 features)
- Runs per scenario: 3 warm
- Timestamp: 2026-05-26 11:37:53

## Overall summary

| Metric | Paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| p50 latency | 58.7ms | Nonems | None× faster |
| p95 latency | 62.9ms | Nonems | — |
| Mode dispatch | 14/14 | n/a (different schema) | — |
| Evidence coverage | 96.0% | 0.0% | — |
| Avg output chars | 2566 | 0 | — |

## Per-scenario detail

| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |
|---|---|---|---|---|---|---|---|---|---|
| cs_qa_di | F1 | cs_qa | cs_qa | ✓ | 59.2ms | -1ms | — | 2/2 | 0/3 |
| cs_qa_tx_iso | F1+F8 | cs_qa | cs_qa | ✓ | 61.2ms | -1ms | — | 2/2 | 0/3 |
| cs_qa_bean_lifecycle | F1 | cs_qa | cs_qa | ✓ | 59.2ms | -1ms | — | 2/2 | 0/2 |
| coach_refactor | F2+F10f | coaching | coaching | ✓ | 60.3ms | -1ms | — | 3/3 | 0/3 |
| coach_optional | F2 | coaching | coaching | ✓ | 60.3ms | -1ms | — | 2/2 | 0/2 |
| tool_transactional | F3/F1 | cs_qa | cs_qa | ✓ | 62.9ms | -1ms | — | 1/1 | 0/1 |
| tool_git_rebase | F3 | tool_only | tool_only | ✓ | 50.7ms | -1ms | — | 1/1 | 0/1 |
| retro_pr_flow | F4 | retro | retro | ✓ | 50.8ms | -1ms | — | 1/2 | 0/2 |
| retro_recurring | F4 | retro | retro | ✓ | 48.9ms | -1ms | — | 2/2 | 0/2 |
| drill_offer | F5+F6 (policy) | cs_qa | cs_qa | ✓ | 61.5ms | -1ms | — | 1/1 | 0/1 |
| self_assess | F7 (policy) | cs_qa | cs_qa | ✓ | 58.1ms | -1ms | — | 2/2 | 0/2 |
| f11_cross_crew | F11 | f11_anchor | f11_anchor | ✓ | 52.4ms | -1ms | — | 3/3 | 0/3 |
| f11_precise | F11 | f11_anchor | f11_anchor | ✓ | 53.1ms | -1ms | — | 2/2 | 0/2 |
| short_prompt | F9 | tool_only | tool_only | ✓ | 53.2ms | -1ms | — | 0/0 | 0/0 |