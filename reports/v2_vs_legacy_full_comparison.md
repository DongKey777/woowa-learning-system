# Paradigm-v2 vs Legacy — full scenario comparison

- Scenarios: 14 (across 7 modes + 11 features)
- Runs per scenario: 3 warm
- Timestamp: 2026-05-24 18:10:03

## Overall summary

| Metric | Paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| p50 latency | 27.2ms | 120.0ms | 4.4× faster |
| p95 latency | 30.7ms | 423.7ms | — |
| Mode dispatch | 14/14 | n/a (different schema) | — |
| Evidence coverage | 96.0% | 68.0% | — |
| Avg output chars | 2388 | 48608 | — |

## Per-scenario detail

| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |
|---|---|---|---|---|---|---|---|---|---|
| cs_qa_di | F1 | cs_qa | cs_qa | ✓ | 27.8ms | 346.5ms | 12.5× | 2/2 | 2/2 |
| cs_qa_tx_iso | F1+F8 | cs_qa | cs_qa | ✓ | 30.7ms | 368.9ms | 12.0× | 2/2 | 2/2 |
| cs_qa_bean_lifecycle | F1 | cs_qa | cs_qa | ✓ | 28.6ms | 359.1ms | 12.6× | 2/2 | 2/2 |
| coach_refactor | F2+F10f | coaching | coaching | ✓ | 28.2ms | 13.3ms | 0.5× | 3/3 | 0/3 |
| coach_optional | F2 | coaching | coaching | ✓ | 30.5ms | 377.2ms | 12.4× | 2/2 | 1/2 |
| tool_transactional | F3/F1 | cs_qa | cs_qa | ✓ | 27.1ms | 399.1ms | 14.7× | 1/1 | 1/1 |
| tool_git_rebase | F3 | tool_only | tool_only | ✓ | 24.6ms | 13.7ms | 0.6× | 1/1 | 1/1 |
| retro_pr_flow | F4 | retro | retro | ✓ | 24.7ms | 12.5ms | 0.5× | 1/2 | 1/2 |
| retro_recurring | F4 | retro | retro | ✓ | 23.3ms | 13.0ms | 0.6× | 2/2 | 2/2 |
| drill_offer | F5+F6 (policy) | cs_qa | cs_qa | ✓ | 27.3ms | 13.4ms | 0.5× | 1/1 | 1/1 |
| self_assess | F7 (policy) | cs_qa | cs_qa | ✓ | 27.9ms | 423.7ms | 15.2× | 2/2 | 2/2 |
| f11_cross_crew | F11 | f11_anchor | f11_anchor | ✓ | 23.3ms | 118.5ms | 5.1× | 3/3 | 1/3 |
| f11_precise | F11 | f11_anchor | f11_anchor | ✓ | 22.7ms | 121.5ms | 5.4× | 2/2 | 1/2 |
| short_prompt | F9 | tool_only | tool_only | ✓ | 23.5ms | 13.1ms | 0.6× | 0/0 | 0/0 |