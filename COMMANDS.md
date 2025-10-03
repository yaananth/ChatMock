# ChatMock Command Reference

## Quick Commands

### Server Management
```bash
servercodex           # Restart server (smart, handles running instances)
servercodexstart      # Start server
servercodexstop       # Stop server
servercodexrestart    # Restart server
servercodexstatus     # Show status + metrics
servercodexlogin      # Login to ChatGPT (refresh auth tokens) ⭐ NEW
servercodexupdate     # Update from git (handles conflicts)
```

### Logs (All Commands Show Combined Logs)
```bash
servercodexlogs       # Tail ALL logs (access + error) ⭐ UPDATED
sclogs                # Same as above (ALL logs)
scaccesslogs          # Only access log
scerrorlogs           # Only error log
```

### Short Aliases
```bash
sc                    # Restart server
scstatus              # Status
sclogs                # ALL logs ⭐
sclogin               # Login ⭐ NEW
scstop                # Stop
scupdate              # Update
```

---

## What Each Command Does

### `sclogs` - View ALL Logs ⭐
Shows **both** access and error logs simultaneously with file headers:
```
==> chatmock.access.log <==
127.0.0.1 - - [03/Oct/2025] "GET /health HTTP/1.1" 200

==> chatmock.error.log <==
[CHAT] Request: {"model":"gpt-5","stream":false}
[COMPATIBILITY] Converting 'message' type to 'input_text'
```

### `sclogin` - Login to ChatGPT ⭐
Refreshes authentication tokens:
1. Opens browser
2. Login to ChatGPT
3. Gets fresh tokens
4. Server uses them immediately

### `scstatus` - Server Status
Shows:
- Server status (running/stopped)
- PID and uptime
- Number of workers
- Test command

### `sc` / `servercodex` - Restart
Smart restart that:
- Stops if already running
- Cleans up orphaned processes
- Starts with production config

---

## Log Monitoring

### Request Logging
All requests are logged with tags:
- `[CHAT]` - Chat completions requests
- `[RESPONSES]` - Responses API requests
- `[COMPATIBILITY]` - Type conversions (e.g., message → input_text)
- `[UPSTREAM_ERROR]` - Upstream API errors

### Search Logs
```bash
cd ~/dev/yaananth/ChatMock

# Find RepoPrompt requests
grep "RepoPrompt" chatmock.error.log

# Find compatibility conversions
grep "\[COMPATIBILITY\]" chatmock.error.log

# Find upstream errors
grep "\[UPSTREAM_ERROR\]" chatmock.error.log

# Find requests to specific endpoint
grep "\[CHAT\]" chatmock.error.log
grep "\[RESPONSES\]" chatmock.error.log
```

---

## Common Workflows

### First Time Setup
```bash
source ~/.zshrc
sclogin              # Login to ChatGPT
sc                   # Start server
scstatus             # Verify running
```

### Daily Use
```bash
sc                   # Start/restart
sclogs               # Watch logs (all)
```

### Debugging Issues
```bash
sclogs               # Watch all logs
# In another terminal:
curl http://127.0.0.1:8000/health
```

### When Auth Expires
```bash
sclogin              # Refresh tokens
sc                   # Restart server
```

### Update from Git
```bash
scupdate             # Fetches & merges
# If conflicts:
# Use copilot to resolve, then:
git add .
git commit
```

---

## Server Features

### Enabled by Default
✅ Reasoning models exposed
✅ Responses API enabled
✅ 21 gevent workers (high concurrency)
✅ Health monitoring
✅ Request logging
✅ Compatibility conversions

### Disabled by Default
❌ Web search (enable per-request with `responses_tools`)
❌ Verbose logging (use `-v` flag)

### Environment Variables
```bash
CHATMOCK_VERBOSE=0                    # Verbose logging
CHATMOCK_EXPOSE_REASONING=1           # Reasoning models
CHATMOCK_ENABLE_WEB_SEARCH=0          # Web search (off by default)
CHATMOCK_ENABLE_RESPONSES_API=1       # Responses API
```

---

## Endpoints

```
GET  /health                    # Health check with metrics
GET  /v1/models                 # List models
POST /v1/chat/completions      POST /v1/chat/complePOST /v1/responses              # Responses API
GET  /v1/responses/{id}         # Get respoGET  /v1/responses

## Troubleshooting

### Server won't start
```bash
scstop && sc
scstop && sc
n't start
       ``bash
lsof -i :8000
scstop
sc
```

### 400 errors
```````````````````````      # Refresh auth tokens
```````````````````````````
```bash
scerrorlogs          # Error log only
scaccesslogs         # Access log only
sclogs               # ALL logs
```

---

## Files

- **Scripts**: `~/dev/yaananth/ChatMock/scripts/chatmock-*`
- **Logs**: `~/dev/yaananth/ChatMock/chatmock.*.log`
- **PID**: `~/dev/yaananth/ChatMock/chatmock.pid`
- **Cache**: `~/.chatmock/prompt_cache/`

