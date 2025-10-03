# ChatMock - Production-Ready & Highly Resilient

## Overview

This is the most resilient, production-ready version of ChatMock with:

✅ **Official Prompt Fetching** - Fetches prompts directly from OpenAI Codex GitHub  
✅ **Intelligent Caching** - 24-hour TTL with automatic updates  
✅ **High Performance** - Gunicorn with gevent async workers  
✅ **Resilient Architecture** - Retry logic, graceful degradation, error handling  
✅ **Simple CLI** - All complexity moved into Python scripts  
✅ **Health Monitoring** - Real-time metrics and status  

---

## Quick Start

```bash
# Activate new commands
source ~/.zshrc

# Start server (auto-restarts if running)
servercodex

# Check status
servercodexstatus

# View logs
servercodexlogs

# Stop server
servercodexstop

# Update from GitHub
servercodexupdate
```

---

## Architecture

### Prompt Management

**Source**: Official OpenAI Codex GitHub repository
- Base instructions: `https://github.com/openai/codex/blob/main/codex-rs/core/prompt.md`
- GPT-5 Codex: `https://github.com/openai/codex/blob/main/codex-rs/core/gpt_5_codex_prompt.md`

**Caching**:
- Location: `~/.chatmock/prompt_cache/`
- TTL: 24 hours
- Auto-refresh: Checks for updates when cache expires
- Fallback: Uses cached version if GitHub unavailable

**Resilience**:
1. Valid cached version (within TTL)
2. Fresh version from GitHub (with retry)
3. Expired cached version (fallback)
4. Local file fallback
5. Minimal default instructions

### Server Architecture

**WSGI Server**: Gunicorn with gevent workers
- Workers: CPU count × 2 + 1 (auto-calculated)
- Worker class: gevent (async/greenlet)
- Connections per worker: 1000
- Max requests: 10,000 (auto-restart workers)
- Timeout: 600 seconds (10 minutes)
- Graceful timeout: 120 seconds

**Resilience Features**:
- Worker auto-recycling (prevents memory leaks)
- Graceful shutdown with SIGTERM
- Orphaned process detection and cleanup
- Automatic restart on crashes
- Health check endpoint
- Request/error metrics

---

## Commands

### Core Commands

```bash
servercodex              # Restart server (smart restart)
servercodexstart         # Start server
servercodexstop          # Stop server
servercodexrestart       # Restart server
servercodexstatus        # Show status + metrics
servercodexlogs          # Tail logs
servercodexupdate        # Update from git
```

### Short Aliases

```bash
sc              # Same as servercodex
scstatus        # Same as servercodexstatus
sclogs          # Same as servercodexlogs
scstop          # Same as servercodexstop
scupdate        # Same as servercodexupdate
```

### Advanced Options

```bash
chatmock-start -v           # Start with verbose logging
chatmock-stop -f            # Force stop (SIGKILL)
chatmock-update --no-stash  # Update without stashing changes
```

---

## Health Monitoring

### Health Check Endpoint

```bash
curl http://localhost:8000/health
```

**Response includes**:
- Server status
- Uptime
- Request metrics (total, success, error, success rate)
- Prompt cache status
- Last request timestamp

### Metrics

The server tracks:
- Total requests
- Successful requests
- Failed requests
- Success rate
- Uptime
- Prompt cache status

---

## Prompt Cache Management

### Cache Location
```
~/.chatmock/prompt_cache/
├── base_instructions.md
├── gpt5_codex_instructions.md
└── metadata.json
```

### Cache Info

Check cache status via health endpoint or Python:

```python
from chatmock.prompts import get_cache_info
print(get_cache_info())
```

### Manual Cache Refresh

```python
from chatmock.prompts import invalidate_cache, get_base_instructions

# Invalidate specific cache
invalidate_cache('base_instructions')

# Invalidate all caches
invalidate_cache()

# Force refresh
get_base_instructions()  # Will fetch fresh from GitHub
```

---

## Resilience Features

### 1. Prompt Fetching
- **Retries**: 3 attempts with exponential backoff
- **Timeout**: 10 seconds per request
- **Fallback chain**: GitHub → Cache → Local file → Default
- **Thread-safe**: Multiple workers can safely access prompts

### 2. Server Management
- **Auto-restart**: `servercodex` automatically restarts if already running
- **Orphan cleanup**: Detects and cleans up orphaned processes
- **Graceful shutdown**: SIGTERM first, then SIGKILL if needed
- **PID tracking**: Prevents multiple instances

### 3. Worker Management
- **Auto-recycling**: Workers restart after 10,000 requests
- **Timeout handling**: Workers timeout after 10 minutes
- **Crash recovery**: Master process spawns new workers on crash
- **Graceful reload**: Zero-downtime worker restart

### 4. Error Handling
- **Request tracking**: All requests logged
- **Error metrics**: Track success/error rates
- **Health endpoint**: Monitor server health
- **Detailed logs**: Error and access logs

---

## Configuration

### Environment Variables

```bash
CHATMOCK_VERBOSE=1                    # Enable verbose logging
CHATMOCK_EXPOSE_REASONING=1           # Expose reasoning models
CHATMOCK_ENABLE_WEB_SEARCH=1          # Enable web search
CHATMOCK_ENABLE_RESPONSES_API=1       # Enable Responses API
CHATMOCK_RESPONSES_NO_BASE_INSTRUCTIONS=0  # Forward client instructions
```

### Files

- `gunicorn_config.py` - Gunicorn configuration
- `wsgi.py` - WSGI entry point
- `chatmock/prompts.py` - Prompt fetching and caching
- `chatmock/health.py` - Health monitoring
- `scripts/chatmock-manage.py` - Management CLI
- `scripts/chatmock-*` - Wrapper scripts

---

## Performance

### Capacity

On 8-core system:
- **Workers**: 17 (8 × 2 + 1)
- **Total connections**: 17,000 concurrent
- **Throughput**: 100+ req/sec for typical API calls

### Optimizations

- Async I/O with gevent
- Worker process pooling
- Connection keep-alive
- Request pipelining
- Shared memory for worker temp files
- Prompt caching (in-memory + disk)

---

## Troubleshooting

### Server won't start

```bash
# Check if port is in use
lsof -i :8000

# Clean up and restart
servercodexstop
servercodex
```

### Orphaned processes

```bash
# Find orphaned processes
pgrep -f "gunicorn.*wsgi:app"

# Clean up (done automatically by servercodexstop)
pkill -f "gunicorn.*wsgi:app"
```

### Prompt fetch fails

Prompts will fall back to cached versions. Check:

```bash
# View cache
ls -la ~/.chatmock/prompt_cache/

# Check logs
servercodexlogs
```

### Check health

```bash
curl http://localhost:8000/health | jq
```

---

## Upgrade Notes

### From Previous Version

1. Prompts now fetched from GitHub (cached locally)
2. All zsh complexity moved to Python scripts
3. New health monitoring system
4. Improved resilience and error handling

### New Features

- Automatic prompt updates from OpenAI Codex repo
- Health check endpoint with metrics
- Simplified zsh aliases
- Better orphaned process handling
- Graceful shutdown improvements

---

## Files Structure

```
ChatMock/
├── chatmock/
│   ├── prompts.py       # Prompt fetching & caching
│   ├── health.py        # Health monitoring
│   ├── app.py           # Flask app with middleware
│   ├── config.py        # Configuration
│   └── ...
├── scripts/
│   ├── chatmock-manage.py   # Main management script
│   ├── chatmock-start       # Start wrapper
│   ├── chatmock-stop        # Stop wrapper
│   ├── chatmock-restart     # Restart wrapper
│   ├── chatmock-status      # Status wrapper
│   ├── chatmock-logs        # Logs wrapper
│   └── chatmock-update      # Update wrapper
├── gunicorn_config.py   # Gunicorn config
├── wsgi.py              # WSGI entry
└── README.md            # This file
```

---

## Support

For issues or questions:
1. Check logs: `servercodexlogs`
2. Check status: `servercodexstatus`
3. Check health: `curl http://localhost:8000/health`
4. Review GitHub issues

---

## License

MIT License - See LICENSE file
