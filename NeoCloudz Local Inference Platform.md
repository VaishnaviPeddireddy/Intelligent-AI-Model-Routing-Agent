# NeoCloudz Local Inference Platform

Small runnable FastAPI implementation of the take-home exercise. Workers are logical components in one service. Routing selects the least-loaded healthy worker for the requested model. SQLite records request usage as the selected advanced feature.

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H 'content-type: application/json' -H 'x-api-key: customer-a' \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Explain Kubernetes simply"}],"max_tokens":20}'
```

Endpoints are `/health`, `/ready`, `/workers`, `/metrics`, `/v1/usage`, and `/v1/chat/completions`. Every request gets a correlation ID; JSON logs include request ID, model, worker, status, and latency.

## Reproducible failures

```bash
# Remove one qwen worker; worker-2 continues serving.
curl -X POST 'http://127.0.0.1:8000/v1/admin/workers/worker-1?healthy=false'

# Restore it and make it slow.
curl -X POST 'http://127.0.0.1:8000/v1/admin/workers/worker-1?healthy=true&slow=true'

# First attempt fails, then one bounded retry uses another worker.
curl -H 'x-simulate-error: true' -X POST http://127.0.0.1:8000/v1/chat/completions -H 'content-type: application/json' -d '{"model":"qwen","messages":[{"role":"user","content":"hello"}]}'

# Every attempt fails and returns 503.
curl -H 'x-simulate-error: always' -X POST http://127.0.0.1:8000/v1/chat/completions -H 'content-type: application/json' -d '{"model":"qwen","messages":[{"role":"user","content":"hello"}]}'
```

Workers have capacity 2 and a bounded queue of 4. Requests that cannot acquire a worker slot within the timeout receive HTTP 429. Unknown models or no healthy worker receive HTTP 503. The implementation exposes active requests, queue depth, utilization, worker health, success/failure/retry counts, latency, and generated tokens in `/metrics`.

## Tests and load test

```bash
pytest -q
python load_test.py
```

The load test sends 32 requests with an actual semaphore-limited concurrency of 8 and prints average and p95 latency.

## Docker and Kubernetes

```bash
docker build -t neocloudz:local .
docker run --rm -p 8000:8000 neocloudz:local
kubectl apply -f k8s.yaml
```

See `BENCHMARK.md` for reproducibility and measured-result guidance. See `WHAT_I_WOULD_DO_NEXT.md` for production evolution and deliberate limitations.
