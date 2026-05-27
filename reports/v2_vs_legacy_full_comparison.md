# Paradigm-v2 vs Legacy — full scenario comparison

- Scenarios: 14 (across 7 modes + 11 features)
- Runs per scenario: 5 warm
- Timestamp: 2026-05-27 19:37:59

## Overall summary

| Metric | Paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| p50 latency | 48.8ms | 70.7ms | 1.4× faster |
| p95 latency | 71.5ms | 100.4ms | 1.4× faster |
| Mode dispatch | 14/14 | n/a (different schema) | — |
| Evidence coverage | 96.0% | 64.0% | — |
| Avg output chars | 4120 | 7332 | — |

## Per-scenario detail

| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |
|---|---|---|---|---|---|---|---|---|---|
| cs_qa_di | F1 | cs_qa | cs_qa | ✓ | 50.7ms | 64.8ms | 1.3× | 2/2 | 2/2 |
| cs_qa_tx_iso | F1+F8 | cs_qa | cs_qa | ✓ | 50.0ms | 77.3ms | 1.5× | 2/2 | 2/2 |
| cs_qa_bean_lifecycle | F1 | cs_qa | cs_qa | ✓ | 48.1ms | 71.7ms | 1.5× | 2/2 | 2/2 |
| coach_refactor | F2+F10f | coaching | coaching | ✓ | 49.6ms | 66.3ms | 1.3× | 3/3 | 0/3 |
| coach_optional | F2 | coaching | coaching | ✓ | 41.4ms | 99.3ms | 2.4× | 2/2 | 1/2 |
| tool_transactional | F3/F1 | cs_qa | cs_qa | ✓ | 48.9ms | 89.2ms | 1.8× | 1/1 | 1/1 |
| tool_git_rebase | F3 | tool_only | tool_only | ✓ | 51.6ms | 60.9ms | 1.2× | 1/1 | 1/1 |
| retro_pr_flow | F4 | retro | retro | ✓ | 56.5ms | 68.9ms | 1.2× | 1/2 | 1/2 |
| retro_recurring | F4 | retro | retro | ✓ | 51.2ms | 62.3ms | 1.2× | 2/2 | 2/2 |
| drill_offer | F5+F6 (policy) | cs_qa | cs_qa | ✓ | 45.9ms | 65.1ms | 1.4× | 1/1 | 1/1 |
| self_assess | F7 (policy) | cs_qa | cs_qa | ✓ | 46.6ms | 83.0ms | 1.8× | 2/2 | 2/2 |
| f11_cross_crew | F11 | f11_anchor | f11_anchor | ✓ | 54.2ms | 76.7ms | 1.4× | 3/3 | 0/3 |
| f11_precise | F11 | f11_anchor | f11_anchor | ✓ | 43.4ms | 85.6ms | 2.0× | 2/2 | 1/2 |
| short_prompt | F9 | tool_only | tool_only | ✓ | 46.0ms | 63.6ms | 1.4× | 0/0 | 0/0 |