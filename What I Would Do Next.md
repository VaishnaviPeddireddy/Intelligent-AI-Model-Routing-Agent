# What I Would Do Next

This submission intentionally keeps workers, routing state, metrics, and the SQLite usage store in one process so that it is easy to run locally. Before production, I would separate the inference API from GPU worker deployments and use a durable queue or streaming scheduler between them.

The first priorities would be authentication with per-tenant quotas, idempotency keys, durable usage events, Prometheus histograms, distributed tracing, and alerting on error rate, queue depth, GPU memory, utilization, and p95 latency. Worker health should come from active probes and heartbeats rather than an in-memory flag. Retries would be limited to safe/idempotent failures and would include backoff and circuit breaking.

For approximately 1,000 GPUs, I would partition capacity by model and GPU class across multiple Kubernetes clusters, use a global or regional gateway, place a model-aware scheduler over cluster-local queues, and autoscale with queue depth, token rate, utilization, and latency metrics. Rollouts would use canaries with automatic rollback. SQLite and simulated token counts must be replaced by a replicated usage pipeline and model-tokenizer-based accounting.

Known limitations include simulated inference, single-process state, no TLS or real authentication, no durable queue, no cross-instance coordination, and no real GPU metrics. These are deliberate omissions for a local take-home exercise.
