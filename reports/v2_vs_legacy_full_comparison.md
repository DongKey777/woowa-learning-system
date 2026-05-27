# Paradigm-v2 vs Legacy — full scenario comparison

- Scenarios: 14 (across 7 modes + 11 features)
- Runs per scenario: 5 warm
- Timestamp: 2026-05-27 19:25:14

## Overall summary

| Metric | Paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| p50 latency | 54.7ms | 77.7ms | 1.4× faster |
| p95 latency | 70.9ms | 104.6ms | 1.5× faster |
| Mode dispatch | 14/14 | n/a (different schema) | — |
| Evidence coverage | 96.0% | 64.0% | — |
| Avg output chars | 4098 | 7332 | — |

## Per-scenario detail

| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |
|---|---|---|---|---|---|---|---|---|---|
| cs_qa_di | F1 | cs_qa | cs_qa | ✓ | 54.8ms | 72.9ms | 1.3× | 2/2 | 2/2 |
| cs_qa_tx_iso | F1+F8 | cs_qa | cs_qa | ✓ | 51.8ms | 88.2ms | 1.7× | 2/2 | 2/2 |
| cs_qa_bean_lifecycle | F1 | cs_qa | cs_qa | ✓ | 59.6ms | 86.6ms | 1.5× | 2/2 | 2/2 |
| coach_refactor | F2+F10f | coaching | coaching | ✓ | 41.1ms | 71.0ms | 1.7× | 3/3 | 0/3 |
| coach_optional | F2 | coaching | coaching | ✓ | 57.5ms | 86.5ms | 1.5× | 2/2 | 1/2 |
| tool_transactional | F3/F1 | cs_qa | cs_qa | ✓ | 54.7ms | 95.3ms | 1.7× | 1/1 | 1/1 |
| tool_git_rebase | F3 | tool_only | tool_only | ✓ | 46.1ms | 69.4ms | 1.5× | 1/1 | 1/1 |
| retro_pr_flow | F4 | retro | retro | ✓ | 55.5ms | 63.7ms | 1.1× | 1/2 | 1/2 |
| retro_recurring | F4 | retro | retro | ✓ | 48.2ms | 67.7ms | 1.4× | 2/2 | 2/2 |
| drill_offer | F5+F6 (policy) | cs_qa | cs_qa | ✓ | 57.0ms | 68.0ms | 1.2× | 1/1 | 1/1 |
| self_assess | F7 (policy) | cs_qa | cs_qa | ✓ | 58.3ms | 81.2ms | 1.4× | 2/2 | 2/2 |
| f11_cross_crew | F11 | f11_anchor | f11_anchor | ✓ | 59.5ms | 87.5ms | 1.5× | 3/3 | 0/3 |
| f11_precise | F11 | f11_anchor | f11_anchor | ✓ | 57.7ms | 81.5ms | 1.4× | 2/2 | 1/2 |
| short_prompt | F9 | tool_only | tool_only | ✓ | 53.2ms | 71.9ms | 1.4× | 0/0 | 0/0 |