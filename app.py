import asyncio, json, logging, os, sqlite3, time, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

DB_PATH = os.getenv("DB_PATH", "usage.db")
QUEUE_LIMIT, CAPACITY, TIMEOUT, MAX_RETRIES = 4, 2, 0.15, 1
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("neocloudz")

class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, str]] = Field(min_length=1)
    max_tokens: int = Field(default=32, ge=1, le=512)

class Worker:
    def __init__(self, worker_id: str, model: str):
        self.worker_id, self.model = worker_id, model
        self.healthy, self.slow = True, False
        self.active_requests = self.queue_depth = 0
        self.utilization = 0.0
        self.requests_completed = self.requests_failed = 0
        self.lock, self.slots = asyncio.Lock(), asyncio.Semaphore(CAPACITY)
    def view(self) -> dict[str, Any]:
        return {"worker_id": self.worker_id, "model": self.model, "healthy": self.healthy,
                "active_requests": self.active_requests, "queue_depth": self.queue_depth,
                "utilization": round(self.utilization, 2), "requests_completed": self.requests_completed,
                "requests_failed": self.requests_failed, "slow": self.slow}

workers = [Worker("worker-1", "qwen"), Worker("worker-2", "qwen"), Worker("worker-3", "llama")]
metrics = {"total": 0, "success": 0, "failed": 0, "retried": 0, "latency_sum": 0.0, "latency_count": 0, "generated_tokens": 0}
metrics_lock = asyncio.Lock()

def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS usage (request_id TEXT PRIMARY KEY, customer TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, latency_ms REAL, worker_id TEXT, status TEXT, created_at TEXT)")
        db.commit()
init_db()

def token_count(text: str) -> int: return max(1, len(text.split()))
def select_worker(model: str, excluded=None):
    excluded = excluded or set()
    choices = [w for w in workers if w.model == model and w.worker_id not in excluded and w.healthy and w.queue_depth < QUEUE_LIMIT]
    return min(choices, key=lambda w: (w.active_requests + w.queue_depth, w.utilization), default=None)
def set_worker(worker_id, healthy=None, slow=None):
    worker = next((w for w in workers if w.worker_id == worker_id), None)
    if not worker: raise HTTPException(404, "worker not found")
    if healthy is not None: worker.healthy = healthy
    if slow is not None: worker.slow = slow
    return worker

@asynccontextmanager
async def lifespan(_):
    init_db(); yield
app = FastAPI(title="NeoCloudz Local Inference Platform", lifespan=lifespan)

@app.get("/health")
def health(): return {"status": "ok"}
@app.get("/ready")
def ready(): return {"ready": any(w.healthy for w in workers)}
@app.get("/workers")
def get_workers(): return {"workers": [w.view() for w in workers]}
@app.post("/v1/admin/workers/{worker_id}")
def change_worker(worker_id: str, healthy: bool | None = None, slow: bool | None = None): return set_worker(worker_id, healthy, slow).view()

@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    async with metrics_lock: m = dict(metrics)
    avg = m["latency_sum"] / m["latency_count"] if m["latency_count"] else 0
    lines = [f"inference_requests_total {m['total']}", f"inference_requests_success_total {m['success']}", f"inference_requests_failed_total {m['failed']}", f"inference_requests_retried_total {m['retried']}", f"inference_generated_tokens_total {m['generated_tokens']}", f"inference_latency_ms_average {avg:.2f}"]
    for w in workers:
        labels = f'worker_id="{w.worker_id}",model="{w.model}"'
        lines += [f"worker_active_requests{{{labels}}} {w.active_requests}", f"worker_queue_depth{{{labels}}} {w.queue_depth}", f"worker_utilization{{{labels}}} {w.utilization:.2f}", f"worker_healthy{{{labels}}} {int(w.healthy)}"]
    return "\n".join(lines) + "\n"

async def acquire(worker):
    async with worker.lock:
        if worker.queue_depth >= QUEUE_LIMIT: raise HTTPException(429, "worker queue is full")
        worker.queue_depth += 1
    try: await asyncio.wait_for(worker.slots.acquire(), TIMEOUT)
    except asyncio.TimeoutError:
        async with worker.lock: worker.queue_depth -= 1
        raise HTTPException(429, "worker queue wait timed out")
    async with worker.lock:
        worker.queue_depth -= 1; worker.active_requests += 1; worker.utilization = worker.active_requests / CAPACITY
async def release(worker, success):
    async with worker.lock:
        worker.active_requests = max(0, worker.active_requests - 1); worker.utilization = worker.active_requests / CAPACITY
        if success: worker.requests_completed += 1
        else: worker.requests_failed += 1
    worker.slots.release()
async def attempt(worker, request, payload, fail_first):
    await acquire(worker); success = False
    try:
        if fail_first or request.headers.get("x-simulate-error") == "always": raise RuntimeError("simulated worker error")
        await asyncio.wait_for(asyncio.sleep(0.25 if worker.slow or request.headers.get("x-simulate-slow") == "true" else 0.03), TIMEOUT)
        success = True
        return f"[{payload.model}] Simulated answer for: {payload.messages[-1].get('content', '')}"
    finally: await release(worker, success)

@app.post("/v1/chat/completions")
async def chat(payload: ChatRequest, request: Request, x_api_key: str | None = Header(default=None)):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4())); customer = x_api_key or "anonymous"; started = time.perf_counter()
    tried, worker, status, error, text = set(), None, "failed", "no healthy worker available", ""
    output_tokens, input_tokens = min(payload.max_tokens, 32), sum(token_count(str(m.get("content", ""))) for m in payload.messages)
    for attempt_no in range(MAX_RETRIES + 1):
        worker = select_worker(payload.model, tried)
        if not worker: break
        tried.add(worker.worker_id)
        try:
            text = await attempt(worker, request, payload, attempt_no == 0 and request.headers.get("x-simulate-error") == "true"); status = "success"
            break
        except HTTPException as exc:
            error = exc.detail; break
        except (RuntimeError, asyncio.TimeoutError) as exc:
            error = str(exc) or "inference timed out"
            if attempt_no < MAX_RETRIES:
                async with metrics_lock: metrics["retried"] += 1
    latency = (time.perf_counter() - started) * 1000; worker_id = worker.worker_id if worker else "none"
    async with metrics_lock:
        metrics["total"] += 1; metrics["latency_sum"] += latency; metrics["latency_count"] += 1; metrics["generated_tokens"] += output_tokens if status == "success" else 0; metrics["success" if status == "success" else "failed"] += 1
    log.info(json.dumps({"request_id": request_id, "model": payload.model, "worker_id": worker_id, "status": status, "latency_ms": round(latency, 2)}))
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT OR REPLACE INTO usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (request_id, customer, payload.model, input_tokens, output_tokens if status == "success" else 0, input_tokens + (output_tokens if status == "success" else 0), latency, worker_id, status, datetime.now(timezone.utc).isoformat())); db.commit()
    if status != "success": raise HTTPException(429 if "queue" in error else (504 if "timed" in error else 503), error, headers={"x-request-id": request_id})
    return {"id": request_id, "object": "chat.completion", "model": payload.model, "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}, "worker_id": worker_id}

@app.get("/v1/usage")
def usage(x_api_key: str | None = Header(default=None)):
    customer = x_api_key or "anonymous"
    with sqlite3.connect(DB_PATH) as db: rows = db.execute("SELECT request_id, customer, model, input_tokens, output_tokens, total_tokens, latency_ms, worker_id, status, created_at FROM usage WHERE customer=? ORDER BY created_at DESC", (customer,)).fetchall()
    cols = ["request_id", "customer", "model", "input_tokens", "output_tokens", "total_tokens", "latency_ms", "worker_id", "status", "created_at"]
    return {"customer": customer, "records": [dict(zip(cols, row)) for row in rows]}

if __name__ == "__main__":
    import uvicorn; uvicorn.run("app:app", host="0.0.0.0", port=8000)
