# Paradigm-v2 vs Legacy — full scenario comparison

- Scenarios: 14 (across 7 modes + 11 features)
- Runs per scenario: 5 warm
- Timestamp: 2026-05-27 18:59:52

## Overall summary

| Metric | Paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| p50 latency | 64.5ms | 88.0ms | 1.4× faster |
| p95 latency | 87.0ms | 116.6ms | 1.3× faster |
| Mode dispatch | 14/14 | n/a (different schema) | — |
| Evidence coverage | 96.0% | 64.0% | — |
| Avg output chars | 4120 | 7332 | — |

## Per-scenario detail

| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |
|---|---|---|---|---|---|---|---|---|---|
| cs_qa_di | F1 | cs_qa | cs_qa | ✓ | 48.2ms | 78.6ms | 1.6× | 2/2 | 2/2 |
| cs_qa_tx_iso | F1+F8 | cs_qa | cs_qa | ✓ | 67.5ms | 93.8ms | 1.4× | 2/2 | 2/2 |
| cs_qa_bean_lifecycle | F1 | cs_qa | cs_qa | ✓ | 65.9ms | 101.5ms | 1.5× | 2/2 | 2/2 |
| coach_refactor | F2+F10f | coaching | coaching | ✓ | 70.5ms | 100.6ms | 1.4× | 3/3 | 0/3 |
| coach_optional | F2 | coaching | coaching | ✓ | 77.4ms | 103.3ms | 1.3× | 2/2 | 1/2 |
| tool_transactional | F3/F1 | cs_qa | cs_qa | ✓ | 80.6ms | 93.1ms | 1.2× | 1/1 | 1/1 |
| tool_git_rebase | F3 | tool_only | tool_only | ✓ | 80.2ms | 90.1ms | 1.1× | 1/1 | 1/1 |
| retro_pr_flow | F4 | retro | retro | ✓ | 54.5ms | 84.2ms | 1.5× | 1/2 | 1/2 |
| retro_recurring | F4 | retro | retro | ✓ | 63.7ms | 87.7ms | 1.4× | 2/2 | 2/2 |
| drill_offer | F5+F6 (policy) | cs_qa | cs_qa | ✓ | 63.7ms | 77.7ms | 1.2× | 1/1 | 1/1 |
| self_assess | F7 (policy) | cs_qa | cs_qa | ✓ | 58.1ms | 88.0ms | 1.5× | 2/2 | 2/2 |
| f11_cross_crew | F11 | f11_anchor | f11_anchor | ✓ | 58.7ms | 93.4ms | 1.6× | 3/3 | 0/3 |
| f11_precise | F11 | f11_anchor | f11_anchor | ✓ | 63.4ms | 87.3ms | 1.4× | 2/2 | 1/2 |
| short_prompt | F9 | tool_only | tool_only | ✓ | 62.4ms | 72.4ms | 1.2× | 0/0 | 0/0 |