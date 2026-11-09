| | Claude Haiku 5.5 (default) | Claude Sonnet 5.5 (default) | Microtensor route table |
|---|---|---|---|
| Compactions answered | 36 | 36 | 36 |
| Passed the arena grader | 3 | 16 | 11 |
| Pass rate | 8.3% | 44.4% | 30.6% |
| Kept all 9 headings | 21 | 36 | 36 |
| Within the arena budget | 16 | 31 | 36 |
| Recall of key facts | 66.7% | 78.4% | 48.1% |
| Hallucination | 5.2% | 6.5% | 1.7% |
| Continuation | 0.46 | 0.52 | 0.49 |
| Score without the budget rule | 0.35 | 0.61 | 0.48 |
| Cost per compaction | $0.0849 | $0.3057 | $0.0050 |
| Median time | 76.5 s | 49.5 s | 39.7 s |
| p95 time | 86.6 s | 69.8 s | 46.1 s |
| Errors | 0 | 0 | 0 |

| Titles | Claude Haiku 5.5 (default) | Claude Sonnet 5.5 (default) | Microtensor route table |
|---|---|---|---|
| Answered | 36 | 36 | 36 |
| Valid JSON title | 36 | 36 | 36 |
| 3 to 7 words | 35 | 36 | 29 |
| Median time | 0.6 s | 1.1 s | 44.3 s |
| Cost per title | $0.0000 | $0.0006 | $0.0000 |

Normal turns through the route table: 3 of 3 answered, 3 served by the model they asked for, so the table left them alone.

### Charts

![Cost per compaction, Haiku vs Sonnet vs Microtensor](results/charts/cost_per_compaction.png)

Numbers: [cost_per_compaction.csv](results/charts/cost_per_compaction.csv)

![Quality against cost, one dot per model](results/charts/quality_vs_cost.png)

Numbers: [quality_vs_cost.csv](results/charts/quality_vs_cost.csv)

![Time per compaction, median and p95 per model](results/charts/time_per_compaction.png)

Numbers: [time_per_compaction.csv](results/charts/time_per_compaction.csv)

![Requests by type and the route that served them](results/charts/requests_by_type_and_route.png)

Numbers: [requests_by_type_and_route.csv](results/charts/requests_by_type_and_route.csv)

![Quality by measure per model](results/charts/quality_by_measure.png)

Numbers: [quality_by_measure.csv](results/charts/quality_by_measure.csv)

![Each session's score across the three models](results/charts/session_scores.png)

Numbers: [session_scores.csv](results/charts/session_scores.csv)
