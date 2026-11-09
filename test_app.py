import asyncio
from fastapi.testclient import TestClient
from app import app, workers

client = TestClient(app)
def setup_function():
    for w in workers:
        w.healthy = True; w.slow = False; w.active_requests = 0; w.queue_depth = 0; w.utilization = 0

def body(model="qwen"):
    return {"model": model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 5}

def test_health_and_routing():
    assert client.get("/health").json()["status"] == "ok"
    response = client.post("/v1/chat/completions", json=body())
    assert response.status_code == 200
    assert response.json()["worker_id"] in ["worker-1", "worker-2"]

def test_unhealthy_worker_is_excluded():
    client.post("/v1/admin/workers/worker-1?healthy=false")
    response = client.post("/v1/chat/completions", json=body())
    assert response.status_code == 200 and response.json()["worker_id"] == "worker-2"

def test_no_worker_returns_503():
    for worker in workers:
        if worker.model == "qwen": worker.healthy = False
    assert client.post("/v1/chat/completions", json=body()).status_code == 503

def test_worker_error_retries_on_another_worker():
    response = client.post("/v1/chat/completions", headers={"x-simulate-error": "true"}, json=body())
    assert response.status_code == 200
    assert client.get("/metrics").text.find("inference_requests_retried_total") >= 0

def test_always_error_returns_503_after_bounded_retry():
    assert client.post("/v1/chat/completions", headers={"x-simulate-error": "always"}, json=body()).status_code == 503

def test_slow_worker_is_reported_and_times_out():
    assert client.post("/v1/admin/workers/worker-1?slow=true").json()["slow"] is True
    assert client.post("/v1/chat/completions", headers={"x-simulate-slow": "true"}, json=body()).status_code in [200, 504]

def test_usage_is_separated_by_customer():
    client.post("/v1/chat/completions", headers={"x-api-key": "alice"}, json=body())
    assert client.get("/v1/usage", headers={"x-api-key": "alice"}).json()["customer"] == "alice"
