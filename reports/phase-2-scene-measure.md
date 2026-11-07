# Phase 2 — scene measurement (per-session aggregates)

Exact re-run command (from `~/biblioteca`):

```
cd ~/biblioteca && python3 src/measure_scenes.py --out reports/phase-2-scene-measure.md
# exclude: session-96bbdeea-72fa-436b-a12f-7b6333190b76
```

Sessions measured: 148 (this session excluded via DSH_SESSION_ID)
Total decompressed chars: 52853188

## 1. Histogram: user messages per session

| bucket | sessions | total chars | % of total chars |
|---|---:|---:|---:|
| 1 | 99 | 25460212 | 48.2% |
| 2-3 | 26 | 10776700 | 20.4% |
| 4-6 | 7 | 6782922 | 12.8% |
| 7-10 | 2 | 6140311 | 11.6% |
| 11-20 | 2 | 3685159 | 7.0% |
| >20 | 0 | 0 | 0.0% |
| (0 user messages) | 12 | 7884 | 0.0% |

## 2. Histogram: decompressed size per session

Bounds: <100K; [100K,500K); [500K,1M); [1M,3M); >=3M chars.

| bucket | sessions | total chars | % of total chars |
|---|---:|---:|---:|
| <100K | 81 | 4110141 | 7.8% |
| 100K-500K | 44 | 11076546 | 21.0% |
| 500K-1M | 9 | 5862986 | 11.1% |
| 1M-3M | 10 | 16595420 | 31.4% |
| >3M | 4 | 15208095 | 28.8% |

## 3. Top 10 sessions by decompressed size

| id | chars | user messages | tool calls | commits |
|---|---:|---:|---:|---:|
| session-6d7eea23-7c87-4e4b-83ae-5bd2d516a76c | 4420220 | 9 | 252 | 0 |
| session-54548ded-4ff3-419c-ab63-86a12f0fe6d5 | 4148890 | 2 | 102 | 0 |
| session-4e8e8083-a4d7-44cc-9750-4792eb95b26b | 3522574 | 1 | 93 | 0 |
| session-dbc5673f-b5cc-411c-8def-63297e66a00f | 3116411 | 4 | 197 | 1 |
| session-2122a024-555e-4691-8f70-ede868606e89 | 2867503 | 1 | 69 | 0 |
| session-907fd2cd-c6bc-4533-a35e-5d3f2151f7b6 | 2412831 | 16 | 173 | 0 |
| session-860f5253-e270-4918-ac09-08317542be50 | 1976270 | 6 | 122 | 0 |
| session-97995ddf-cf7f-4356-b598-d1fe5c53b154 | 1720091 | 10 | 205 | 0 |
| session-abf97961-f9b5-4805-aea2-9685fad4b35c | 1595228 | 1 | 103 | 0 |
| session-6e168499-343c-4441-888c-90b225142cc6 | 1338971 | 1 | 62 | 2 |

## 4. Median / p90 of user messages per session
- median: 1
- p90 (linear interpolation, inclusive): 3

