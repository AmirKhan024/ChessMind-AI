"""RQ worker entrypoint: `python -m app.worker QUEUE [QUEUE ...]`.
Same as `rq worker`, plus JSON logs tagged with our job id."""

import sys

from rq import Worker

from app import logs
from app.tasks import redis

# A job whose worker died (container killed, host lost) is found once its registry entry
# expires (worker_ttl + 60 s for a job shorter than the 30 s monitoring interval, as most
# games are) and the next maintenance run hands it to our failure callback for a retry.
# RQ's defaults (420 s, 600 s) left a killed worker's game stuck for 7 minutes in the
# failure test; these give about 2-3 minutes.
WORKER_TTL = 60
MAINTENANCE_SECONDS = 60


class JobWorker(Worker):
    def perform_job(self, job, queue):
        token = logs.job_id.set(job.meta.get("job_id", job.id))
        try:
            return super().perform_job(job, queue)
        finally:
            logs.job_id.reset(token)


def main() -> None:
    logs.setup()
    worker = JobWorker(
        sys.argv[1:],
        connection=redis,
        worker_ttl=WORKER_TTL,
        maintenance_interval=MAINTENANCE_SECONDS,
    )
    worker.work(logging_level="INFO")


if __name__ == "__main__":
    main()
