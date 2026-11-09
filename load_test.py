import asyncio, time, httpx
URL = "http://127.0.0.1:8000/v1/chat/completions"
CONCURRENCY, REQUESTS = 8, 32
async def one(client, sem, i):
    async with sem:
        body = {"model": "qwen" if i % 3 else "llama", "messages": [{"role": "user", "content": "Explain Kubernetes simply."}], "max_tokens": 20}
        started = time.perf_counter(); response = await client.post(URL, json=body)
        return response.status_code, (time.perf_counter() - started) * 1000
async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=5) as client:
        results = await asyncio.gather(*(one(client, sem, i) for i in range(REQUESTS)))
    latencies = sorted(x[1] for x in results)
    print({"requests": REQUESTS, "concurrency": CONCURRENCY, "success": sum(x[0] == 200 for x in results), "errors": sum(x[0] != 200 for x in results), "avg_ms": round(sum(latencies) / len(latencies), 2), "p95_ms": round(latencies[int(len(latencies) * .95) - 1], 2)})
if __name__ == "__main__": asyncio.run(main())
