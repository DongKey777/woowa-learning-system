# Paradigm-v2 vs Legacy — full scenario comparison

- Scenarios: 14 (across 7 modes + 11 features)
- Runs per scenario: 5 warm
- Timestamp: 2026-05-28 10:17:14

## Overall summary

| Metric | Paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| p50 latency | 32.0ms | 41.0ms | 1.3× faster |
| p95 latency | 40.0ms | 50.0ms | 1.2× faster |
| Mode dispatch | 14/14 | n/a (different schema) | — |
| Evidence coverage | 96.0% | 64.0% | — |
| Avg output chars | 4117 | 7332 | — |

## Per-scenario detail

| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |
|---|---|---|---|---|---|---|---|---|---|
| cs_qa_di | F1 | cs_qa | cs_qa | ✓ | 39.3ms | 41.0ms | 1.0× | 2/2 | 2/2 |
| cs_qa_tx_iso | F1+F8 | cs_qa | cs_qa | ✓ | 31.7ms | 48.4ms | 1.5× | 2/2 | 2/2 |
| cs_qa_bean_lifecycle | F1 | cs_qa | cs_qa | ✓ | 33.7ms | 52.0ms | 1.5× | 2/2 | 2/2 |
| coach_refactor | F2+F10f | coaching | coaching | ✓ | 35.8ms | 37.4ms | 1.0× | 3/3 | 0/3 |
| coach_optional | F2 | coaching | coaching | ✓ | 30.6ms | 44.9ms | 1.5× | 2/2 | 1/2 |
| tool_transactional | F3/F1 | cs_qa | cs_qa | ✓ | 35.9ms | 42.4ms | 1.2× | 1/1 | 1/1 |
| tool_git_rebase | F3 | tool_only | tool_only | ✓ | 31.0ms | 35.5ms | 1.1× | 1/1 | 1/1 |
| retro_pr_flow | F4 | retro | retro | ✓ | 30.3ms | 33.9ms | 1.1× | 1/2 | 1/2 |
| retro_recurring | F4 | retro | retro | ✓ | 32.0ms | 30.6ms | 1.0× | 2/2 | 2/2 |
| drill_offer | F5+F6 (policy) | cs_qa | cs_qa | ✓ | 31.5ms | 32.7ms | 1.0× | 1/1 | 1/1 |
| self_assess | F7 (policy) | cs_qa | cs_qa | ✓ | 33.0ms | 44.8ms | 1.4× | 2/2 | 2/2 |
| f11_cross_crew | F11 | f11_anchor | f11_anchor | ✓ | 33.3ms | 45.4ms | 1.4× | 3/3 | 0/3 |
| f11_precise | F11 | f11_anchor | f11_anchor | ✓ | 32.4ms | 43.6ms | 1.3× | 2/2 | 1/2 |
| short_prompt | F9 | tool_only | tool_only | ✓ | 30.1ms | 34.1ms | 1.1× | 0/0 | 0/0 |