# Local LLM Q4 Experiment

Status: archived. The supported AI direction is now OpenAI controlled
refinement. Local LLM/llama.cpp runtime support should be removed during Phase 7;
this document is kept only as historical experiment evidence until that cleanup
is complete.

Branch: `experiment/local-gemma-q4-llamacpp`

## Model

- Q8 baseline file: `models/gemma-4-E2B-it-Q8_0.gguf`
- Q4 test file: `models/gemma-4-e2b-Q4_K_M.gguf`
- Q4 source: `dahus/gemma-4-e2b-it-Q4_K_M-GGUF`

## Query Benchmark

Query:

```text
Fpj Elegante Titanium
```

Q8 baseline measured in the bot container:

```text
elapsed_seconds=42.363
result_count=9
llama.cpp idle memory=2.969GiB
```

Q4 measured in the bot container:

```text
elapsed_seconds=39.818
elapsed_seconds=39.876
result_count=9
llama.cpp idle memory=1.731GiB
```

## Output Assessment

Q4 returned the same 9 result snippets as the Q8 baseline for this query.

## Notes

- Q4 reduced idle llama.cpp container memory by about 1.24GiB in this local run.
- End-to-end query latency improved slightly, about 2.5 seconds versus the Q8 baseline.
- `make llm-smoke` was slower on Q4 because Gemma thinking mode generated a longer response; the task-specific search flow remained slightly faster.
