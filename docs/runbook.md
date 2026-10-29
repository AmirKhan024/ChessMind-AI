# Runbook

How to run, watch and fix ChessMind AI. Commands assume the repo root.

## Local

```sh
docker compose up -d --build --wait     # API on :8000, Grafana :3000, Prometheus :9090
```

Ports and sizes are environment variables: `API_PORT`, `DB_PORT`, `GRAFANA_PORT`,
`PROMETHEUS_PORT`, `ANALYSIS_WORKERS` (default 8), `REPORT_WORKERS` (default 2), `GAMES_CAP`
(default 300), `RATE_LIMIT` (default true; set false only for load tests).

## Production (AWS, one VM)

Infrastructure is in `infra/terraform/`: one EC2 instance running the same compose stack plus
Caddy for HTTPS (`infra/compose.prod.yml`), an Elastic IP, an IAM role that may read only
`/chessmind/*` parameters, and a monthly budget alarm. There is no SSH; use SSM Session
Manager.

First deploy (needs AWS credentials and the owner's approval of the cost):

```sh
aws ssm put-parameter --name /chessmind/groq_api_key --type SecureString --value "<key>"
cd infra/terraform
terraform init
terraform apply -var alert_email=you@example.com
terraform output url
```

The key is entered by hand into SSM, so it is never in the repo or in Terraform state. The
instance reads it at boot and writes it to `/opt/chessmind/.env` (mode 600).

Deploy a new version:

```sh
aws ssm start-session --target $(terraform output -raw instance_id)
cd /opt/chessmind && sudo git pull && \
  sudo docker compose -f docker-compose.yml -f infra/compose.prod.yml up -d --build --wait
```

Grafana (not public): run the `grafana_tunnel` command from `terraform output`, then open
http://localhost:3000 (user `admin`, password in `/opt/chessmind/.env`).

Tear down: `terraform destroy -var alert_email=...` (the SSM parameter stays; delete it with
`aws ssm delete-parameter --name /chessmind/groq_api_key`).

## Dashboards

Grafana → ChessMind AI. What each panel tells you:

| Panel | Healthy | If not |
|---|---|---|
| Queue depth | analysis drains within minutes | add `ANALYSIS_WORKERS` (one per vCPU) or lower `GAMES_CAP` |
| Positions analysed per second | about 30/s per 8 laptop workers | CPU throttled or workers down: `docker compose ps` |
| Position cache hit rate | 5-10% for new users | low is normal; it rises when many users share openings |
| Job time per 100 games | ~145 s on the dev laptop (8 workers) | see queue depth |
| Lichess 429 responses | 0 | the lichess worker pauses 60 s per 429 by itself; if constant, check for a second lichess worker |
| Jobs finished (failed) | 0 | `docker compose logs worker-lichess` (user not found shows here) |
| Groq calls by outcome | ok | `http_429`: free-tier limit, reports fall back to the template; `error`: network/timeout |
| Grounding pass rate | report ~0.5-0.8 per attempt, chat ~0.8+ | prompt or model changed: re-run `ml/eval_coach.py`, see STATUS |
| Reports by method | mostly llm | template = Groq down or grounding failed 3 times (still correct, just plainer) |
| HTTP p95 latency | < 200 ms | report building no longer runs in requests; look at the slow route |

## Logs

Every container logs one JSON object per line. Lines written while a job runs carry
`job_id`, so one user's job can be followed across the lichess, analysis and report workers:

```sh
docker compose logs --no-log-prefix worker-analysis worker-lichess | grep '"job_id": "<id>"'
```

## Common problems

**A job stays at "analysing" with no progress.** If a worker dies mid-game (container killed,
host reboot), RQ notices within a few minutes (`AbandonedJobError`), our failure callback
retries the game once, and a second failure skips the game (`jobs.skipped`); the job then
finishes. If Redis itself was lost, the job's queued games are gone: re-submit the username
(games already analysed are reused).

**Every report says "Written from a fixed template".** Groq is down, rate-limited, or the key is
wrong. Check "Groq calls by outcome" and `docker compose logs worker-report`. Users still get
correct reports.

**Users get HTTP 429 from us.** The per-IP limits are in `app/main.py` (`RATE_LIMITS`). Behind
Caddy, uvicorn must trust the proxy header (`FORWARDED_ALLOW_IPS` in `infra/compose.prod.yml`),
or every user shares Caddy's IP and one limit.

**Delete a user's data.** `DELETE /players/{username}` removes their games, moves, jobs and
reports (cached position evaluations are not personal and stay).

**Promote a new peer model.** Train into a new directory, then
`python -m ml.gate register NEW_DIR`. It refuses a model worse than the registered one on the
committed held-out sample; CI fails if `model.txt` changes without going through the gate.
