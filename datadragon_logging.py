"""Structured logging for DataDragon. Kept in its own module so the job-id context variable exists exactly once,
however often the application module is imported (tests do that)."""
import contextvars
import logging
import os

current_job_id = contextvars.ContextVar('current_job_id', default='-')


class JobIdFilter(logging.Filter):
    """Adds `record.job`: the id of the job running in this thread, or '-'."""

    def filter(self, record):
        record.job = current_job_id.get()
        return True


log = logging.getLogger('datadragon')
if not any(isinstance(f, JobIdFilter) for f in log.filters):
    log.addFilter(JobIdFilter())
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "job": "%(job)s", "message": "%(message)s"}'))
    log.addHandler(handler)
    log.setLevel(os.environ.get('DATADRAGON_LOG_LEVEL', 'INFO').upper())
    log.propagate = False
