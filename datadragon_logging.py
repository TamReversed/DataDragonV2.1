"""Structured logging for DataDragon. Kept in its own module so the job-id context variable exists exactly once,
however often the application module is imported (tests do that)."""
import contextvars
import json
import logging
import os

current_job_id = contextvars.ContextVar('current_job_id', default='-')


class JobIdFilter(logging.Filter):
    """Adds `record.job`: the id of the job running in this thread, or '-'."""

    def filter(self, record):
        record.job = current_job_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line. json.dumps escapes quotes and line breaks, so a message cannot forge another line."""

    def format(self, record):
        entry = {'time': self.formatTime(record), 'level': record.levelname, 'job': getattr(record, 'job', '-'),
                 'message': record.getMessage()}
        if record.exc_info:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


log = logging.getLogger('datadragon')
if not any(isinstance(f, JobIdFilter) for f in log.filters):
    log.addFilter(JobIdFilter())
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.setLevel(os.environ.get('DATADRAGON_LOG_LEVEL', 'INFO').upper())
    log.propagate = False
