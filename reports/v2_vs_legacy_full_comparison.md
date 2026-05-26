# Paradigm-v2 vs Legacy — full scenario comparison

- Scenarios: 14 (across 7 modes + 11 features)
- Runs per scenario: 3 warm
- Timestamp: 2026-05-26 23:27:18

## Overall summary

| Metric | Paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| p50 latency | 118.7ms | 160.6ms | 1.4× faster |
| p95 latency | 143.1ms | 273.2ms | — |
| Mode dispatch | 14/14 | n/a (different schema) | — |
| Evidence coverage | 96.0% | 64.0% | — |
| Avg output chars | 4021 | 12762 | — |

## Per-scenario detail

| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |
|---|---|---|---|---|---|---|---|---|---|
| cs_qa_di | F1 | cs_qa | cs_qa | ✓ | 106.1ms | 182.8ms | 1.7× | 2/2 | 2/2 |
| cs_qa_tx_iso | F1+F8 | cs_qa | cs_qa | ✓ | 141.6ms | 273.2ms | 1.9× | 2/2 | 2/2 |
| cs_qa_bean_lifecycle | F1 | cs_qa | cs_qa | ✓ | 129.1ms | 209.8ms | 1.6× | 2/2 | 2/2 |
| coach_refactor | F2+F10f | coaching | coaching | ✓ | 123.4ms | 64.3ms | 0.5× | 3/3 | 0/3 |
| coach_optional | F2 | coaching | coaching | ✓ | 119.9ms | 201.6ms | 1.7× | 2/2 | 1/2 |
| tool_transactional | F3/F1 | cs_qa | cs_qa | ✓ | 134.0ms | 222.5ms | 1.7× | 1/1 | 1/1 |
| tool_git_rebase | F3 | tool_only | tool_only | ✓ | 100.3ms | 67.2ms | 0.7× | 1/1 | 1/1 |
| retro_pr_flow | F4 | retro | retro | ✓ | 90.2ms | 57.0ms | 0.6× | 1/2 | 1/2 |
| retro_recurring | F4 | retro | retro | ✓ | 83.8ms | 70.2ms | 0.8× | 2/2 | 2/2 |
| drill_offer | F5+F6 (policy) | cs_qa | cs_qa | ✓ | 126.0ms | 67.1ms | 0.5× | 1/1 | 1/1 |
| self_assess | F7 (policy) | cs_qa | cs_qa | ✓ | 117.4ms | 200.4ms | 1.7× | 2/2 | 2/2 |
| f11_cross_crew | F11 | f11_anchor | f11_anchor | ✓ | 90.0ms | 156.7ms | 1.7× | 3/3 | 0/3 |
| f11_precise | F11 | f11_anchor | f11_anchor | ✓ | 92.7ms | 164.6ms | 1.8× | 2/2 | 1/2 |
| short_prompt | F9 | tool_only | tool_only | ✓ | 143.1ms | 63.3ms | 0.4× | 0/0 | 0/0 |