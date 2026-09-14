# GLM-4.7-flash Extraction Runs — Documentation

Date: 2026-09-14. Two extraction runs of `sources/thesis_en.txt`
(23,017 chars → 36 chunks) against `Book1.xlsx`, via 9router → GLM-4.7-flash
(reasoning model). Both runs killed before completion — run 1 by session
teardown (undocumented at the time), run 2 deliberately at 18 chunks.

## Run 1 — overnight (killed by agent_close at chunk 18)

- Log: `/tmp/thesis_audit_glm_run1.log`
- Started 03:53, killed after 26,173 s (7.27 h) — chunk 18 reached
- 91 statistics validated at kill. **No extracted_values.json survived**:
  extraction only wrote output at completion. This data loss motivated the
  incremental-save fix (commit a81a61b).
- Chunk yield pattern: 0,3,7,3,0,0,0,10,0,0,0,5,17,16,11,5,0,14,7 → 91
  (chunks 0–18). Zero-yield chunks = Methods/aims text, correctly empty.

## Run 2 — controlled stop at 18 chunks

- Log: `/tmp/thesis_audit_glm_run2.log`
- Started 11:14, stopped 13:20 (~2 h 6 min), chunks 0–17 complete
- **74 validated statistics saved** to `extracted_values_glm_18chunks.json`
  (74 entries, all status=validated; incremental save per chunk)
- Kind distribution: count 26, mean 15, std 15, p 8, min 2, max 2, r 1,
  other 5
- Yield pattern (0–17): 0,3,7,3,0,0,0,10,0,0,1,17,8,7,4,0,0,23 → 76
  cumulative reported, 74 after final dedupe

## Run 1 vs run 2 consistency (chunks 0–17)

| chunk | run 1 | run 2 | note |
|---|---|---|---|
| 0–10 | 0,3,7,3,0,0,0,10,0,0,0 | 0,3,7,3,0,0,0,10,0,0,**1** | identical except chunk 10 |
| 11 | 5 | 1 | divergence |
| 12 | 17 | 17 | identical |
| 13 | 16 | 8 | divergence |
| 14 | 11 | 7 | divergence |
| 15 | 5 | 4 | near |
| 16 | 0 | 0 | identical |
| 17 | 14 | 23 | divergence |

Extraction is *mostly* deterministic but not fully: identical yields on
simple chunks, divergent on dense table chunks (LLM non-determinism despite
temperature 0). Run 2 total for 0–17 (74) < run 1 (88 over same chunks) —
run 1 got luckier on dense chunks, or extracted more before discard.
Dedupe and status accounting differ between runs (run 1 counts not
preserved per-entry), so this comparison is at yield level only.

## Speed documentation — local gemma-4-e4b vs GLM-4.7-flash

**GLM-4.7-flash (9router, reasoning model), extraction:**
- Quiet chunks (0–3 proposals): 2–6 min each
- Dense chunks (7–23 proposals): 20–35 min each (chunk 12: ~18 min stall
  then 8 proposals; chunk 18: ~35 min, 7 proposals)
- Overall run 2: 18 chunks in ~106 min ≈ **5.9 min/chunk**
- Attributed to reasoning-token overhead: the model thinks before emitting
  JSON; thinking scales with table complexity

**Local gemma-4-e4b (LM Studio, localhost:1234), chat latency:**
- Single-query responses: seconds (fast enough that chat feels instant;
  no formal benchmark captured — chat was subjectively responsive)
- Not benchmarked for extraction batch; on a Mac, a 36-chunk extraction
  locally would be substantially slower than cloud throughput for dense
  chunks and was never attempted
- Role split intended: local gemma = chat (latency fine, free, offline),
  cloud model = extraction batch (throughput matters)

**Candidates shortlisted to replace GLM for extraction:**
nemotron-3-super (hosted, ~460–500 t/s, native JSON mode — likely fastest),
cloud gemma 4 31b (dense, direct, strong contract compliance),
ling-3.0-flash-free (MoE 5.1B active, free-tier limits unknown),
mimo-v2.5-free (reasoning_content overhead risk).
Decision pending; bake-off plan: 3 chunks (1, 12, 17) per candidate,
compare proposals/validated/discards against GLM's known per-chunk results.

## Next steps

1. Accuracy analysis of the 74 saved statistics against thesis tables
   (18-chunk coverage: everything through Table 11 + partial Discussion)
2. Model bake-off → pick extraction model
3. Interactive test 3 (user-interaction path) on the chosen model
