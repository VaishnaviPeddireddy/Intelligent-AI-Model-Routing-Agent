# Benchmark

## Workload

The included `load_test.py` sends **32 requests at concurrency 8**. The workload uses a 2:1 mix of `qwen` and `llama`, one short user message, and `max_tokens=20`. Workers are simulated with a 30 ms normal delay, capacity 2 requests per worker, queue limit 4, and a 150 ms request timeout.

Run it with:

```bash
python app.py
python load_test.py
```

The script prints the measured success count, error count, average latency, and p95 latency for the local machine. Results vary with CPU, Python version, and background load, so the command output is the source of truth rather than a hard-coded number.

## Interpretation

The main bottleneck is the simulated worker capacity and timeout, not GPU computation. At concurrency 8, requests are distributed by model and least-loaded healthy worker. Queue depth and active requests are visible through `/workers` and `/metrics`. Increasing request delay or marking a worker slow produces queue pressure and 429 responses when the bounded queue is exhausted.

Failure behavior can be reproduced by setting `worker-1` unhealthy, or by sending `x-simulate-error: true`. The latter fails the first selected attempt and performs one bounded retry on another eligible worker. These observations are measured locally; throughput and latency for production NVIDIA GPUs are estimates and require real model-serving benchmarks.
