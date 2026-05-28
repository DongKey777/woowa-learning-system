# Paradigm-v2 vs Legacy — full scenario comparison

- Scenarios: 14 (across 7 modes + 11 features)
- Runs per scenario: 5 warm
- Timestamp: 2026-05-28 10:34:44

## Overall summary

| Metric | Paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| p50 latency | 32.2ms | 43.3ms | 1.3× faster |
| p95 latency | 37.5ms | 48.8ms | 1.3× faster |
| Mode dispatch | 14/14 | n/a (different schema) | — |
| Evidence coverage | 96.0% | 64.0% | — |
| Avg output chars | 4117 | 7332 | — |

## Per-scenario detail

| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |
|---|---|---|---|---|---|---|---|---|---|
| cs_qa_di | F1 | cs_qa | cs_qa | ✓ | 30.4ms | 41.0ms | 1.3× | 2/2 | 2/2 |
| cs_qa_tx_iso | F1+F8 | cs_qa | cs_qa | ✓ | 33.8ms | 48.2ms | 1.4× | 2/2 | 2/2 |
| cs_qa_bean_lifecycle | F1 | cs_qa | cs_qa | ✓ | 34.6ms | 45.9ms | 1.3× | 2/2 | 2/2 |
| coach_refactor | F2+F10f | coaching | coaching | ✓ | 32.9ms | 33.7ms | 1.0× | 3/3 | 0/3 |
| coach_optional | F2 | coaching | coaching | ✓ | 34.7ms | 44.7ms | 1.3× | 2/2 | 1/2 |
| tool_transactional | F3/F1 | cs_qa | cs_qa | ✓ | 32.0ms | 45.1ms | 1.4× | 1/1 | 1/1 |
| tool_git_rebase | F3 | tool_only | tool_only | ✓ | 31.1ms | 34.7ms | 1.1× | 1/1 | 1/1 |
| retro_pr_flow | F4 | retro | retro | ✓ | 31.0ms | 32.9ms | 1.1× | 1/2 | 1/2 |
| retro_recurring | F4 | retro | retro | ✓ | 31.4ms | 33.7ms | 1.1× | 2/2 | 2/2 |
| drill_offer | F5+F6 (policy) | cs_qa | cs_qa | ✓ | 33.4ms | 33.5ms | 1.0× | 1/1 | 1/1 |
| self_assess | F7 (policy) | cs_qa | cs_qa | ✓ | 31.7ms | 43.8ms | 1.4× | 2/2 | 2/2 |
| f11_cross_crew | F11 | f11_anchor | f11_anchor | ✓ | 31.1ms | 45.1ms | 1.5× | 3/3 | 0/3 |
| f11_precise | F11 | f11_anchor | f11_anchor | ✓ | 32.5ms | 44.8ms | 1.4× | 2/2 | 1/2 |
| short_prompt | F9 | tool_only | tool_only | ✓ | 30.0ms | 35.7ms | 1.2× | 0/0 | 0/0 |