"""
Gunicorn configuration for high-performance, resilient ChatMock server.
Optimized for concurrent request handling and reliability.
"""
import multiprocessing
import os

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes - resilient configuration
workers = multiprocessing.cpu_count() * 2 + 1  # Recommended formula
worker_class = "gevent"  # Async worker for high concurrency
worker_connections = 1000  # Max concurrent connections per worker
max_requests = 10000  # Restart workers after N requests (prevents memory leaks)
max_requests_jitter = 1000  # Add randomness to max_requests
timeout = 600  # Worker timeout (10 minutes for long-running requests)
graceful_timeout = 120  # Time for graceful shutdown
keepalive = 5  # Keepalive timeout

# Resilience settings
worker_tmp_dir = None  # Use default temp dir (works on macOS)
preload_app = False  # Don't preload to allow worker-level recovery

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Logging
accesslog = os.path.join(os.path.dirname(__file__), "chatmock.access.log")
errorlog = os.path.join(os.path.dirname(__file__), "chatmock.error.log")
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "chatmock"

# Server hooks for resilience
def on_starting(server):
    """Called just before the master process is initialized."""
    print(f"🚀 Starting ChatMock with {workers} workers (gevent async)")
    print(f"   Resilience: max_requests={max_requests}, graceful_timeout={graceful_timeout}s")

def on_reload(server):
    """Called to recycle workers during a reload via SIGHUP."""
    print("♻️  Reloading ChatMock workers...")

def worker_int(worker):
    """Called when a worker receives a SIGINT or SIGQUIT signal."""
    print(f"⚠️  Worker {worker.pid} received interrupt signal")

def worker_abort(worker):
    """Called when a worker times out."""
    print(f"⚠️  Worker {worker.pid} timed out - will be restarted")

def pre_fork(server, worker):
    """Called just before a worker is forked."""
    pass

def post_fork(server, worker):
    """Called after a worker has been forked."""
    print(f"✓ Worker {worker.pid} started")

def post_worker_init(worker):
    """Called after a worker has initialized the application."""
    # Warm up worker
    try:
        from chatmock.prompts import get_cached_base_instructions
        # Pre-load prompts in worker
        get_cached_base_instructions()
    except Exception as e:
        print(f"⚠️  Worker {worker.pid} failed to pre-load prompts: {e}")

def worker_exit(server, worker):
    """Called when a worker is exited."""
    print(f"✓ Worker {worker.pid} exited")

def on_exit(server):
    """Called just before the master process exits."""
    print("👋 ChatMock server shutting down")

# Error handling
def when_ready(server):
    """Called just after the server is started."""
    print(f"✓ ChatMock ready on {bind}")
    print(f"✓ Workers: {workers} | Connections per worker: {worker_connections}")
    print(f"✓ Total capacity: {workers * worker_connections} concurrent connections")
