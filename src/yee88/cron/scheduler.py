from datetime import datetime
from typing import Callable, Awaitable, Set, Dict
from anyio.abc import TaskGroup
import anyio
from anyio import Lock, move_on_after
from .manager import CronManager
from .models import CronJob
from ..logging import get_logger

logger = get_logger()


class CronScheduler:
    def __init__(
        self,
        manager: CronManager,
        callback: Callable[[CronJob], Awaitable[None]],
        task_group: TaskGroup,
    ):
        self.manager = manager
        self.callback = callback
        self.task_group = task_group
        self.running = False
        self._running_jobs: Set[str] = set()
        self._job_locks: Dict[str, Lock] = {}

    def _calculate_next_check(self) -> float:
        now = datetime.now(self.manager.timezone)
        min_sleep = 1.0
        max_sleep = 60.0

        earliest_next_run = None

        for job in self.manager.jobs:
            if not job.enabled:
                continue

            try:
                if job.one_time:
                    exec_time = datetime.fromisoformat(job.schedule)
                    if exec_time.tzinfo is None:
                        exec_time = exec_time.replace(tzinfo=self.manager.timezone)
                    if exec_time > now:
                        if earliest_next_run is None or exec_time < earliest_next_run:
                            earliest_next_run = exec_time
                else:
                    if job.next_run:
                        next_run = datetime.fromisoformat(job.next_run)
                        if next_run.tzinfo is None:
                            next_run = next_run.replace(tzinfo=self.manager.timezone)
                    else:
                        from croniter import croniter

                        itr = croniter(job.schedule, now)
                        next_run = itr.get_next(datetime)
                        if next_run.tzinfo is None:
                            next_run = next_run.replace(tzinfo=self.manager.timezone)

                    if next_run > now:
                        if earliest_next_run is None or next_run < earliest_next_run:
                            earliest_next_run = next_run
            except Exception:
                continue

        if earliest_next_run is None:
            return max_sleep

        seconds_until = (earliest_next_run - now).total_seconds()
        return max(min_sleep, min(seconds_until, max_sleep))

    async def start(self):
        self.running = True
        self.manager.load()
        logger.info("cron.scheduler.started", job_count=len(self.manager.jobs))

        # Debug: log all jobs and their next_run times
        for job in self.manager.jobs:
            logger.info(
                "cron.scheduler.job_status",
                job_id=job.id,
                enabled=job.enabled,
                schedule=job.schedule,
                last_run=job.last_run,
                next_run=job.next_run,
            )

        cycle = 0
        while self.running:
            cycle += 1
            sleep_seconds = self._calculate_next_check()

            if sleep_seconds > 5:
                logger.info(
                    "cron.scheduler.check_cycle",
                    cycle=cycle,
                    job_count=len(self.manager.jobs),
                    next_check_in=sleep_seconds,
                )

            due_jobs = self.manager.get_due_jobs()

            if due_jobs:
                logger.info(
                    "cron.scheduler.due_jobs_found",
                    cycle=cycle,
                    count=len(due_jobs),
                    job_ids=[j.id for j in due_jobs],
                )
            elif sleep_seconds <= 5:
                logger.debug("cron.scheduler.no_due_jobs", cycle=cycle)

            for job in due_jobs:
                logger.info(
                    "cron.scheduler.dispatching_job",
                    job_id=job.id,
                    message=job.message[:50],
                )
                self.task_group.start_soon(self._run_job_safe, job)

            logger.info("cron.scheduler.sleeping", cycle=cycle, seconds=sleep_seconds)
            await anyio.sleep(sleep_seconds)

    def _acquire_job_lock(self, job_id: str) -> bool:
        if job_id in self._running_jobs:
            return False

        if job_id not in self._job_locks:
            self._job_locks[job_id] = Lock()

        job_lock = self._job_locks[job_id]

        if job_lock.locked():
            return False

        self._running_jobs.add(job_id)
        return True

    def _release_job_lock(self, job_id: str) -> None:
        self._running_jobs.discard(job_id)

    async def _run_job_safe(self, job: CronJob) -> None:
        if not self._acquire_job_lock(job.id):
            logger.warning("cron.job.already_running", job_id=job.id)
            return

        try:
            logger.info("cron.job.executing", job_id=job.id)
            with move_on_after(180):
                await self.callback(job)
                logger.info("cron.job.completed", job_id=job.id)
                return

            logger.warning("cron.job.timeout", job_id=job.id, timeout_s=180)
        except Exception as exc:
            logger.error("cron.job.failed", job_id=job.id, error=str(exc))
        finally:
            self._release_job_lock(job.id)

    def stop(self):
        self.running = False
        logger.info("cron.scheduler.stopped")
