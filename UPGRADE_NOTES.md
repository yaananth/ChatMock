# Upgrade Notes - Production-Ready Resilient ChatMock

## Version: 2.0 - Production & Resilient

**Date**: October 2025

---

## Breaking Changes

### 1. Local Prompts Removed
- ❌ `prompt.md` - Removed
- ❌ `prompt_gpt5_codex.md` - Removed
- ✅ Now fetched from: `github.com/openai/codex/...`
- ✅ Cached in: `~/.chatmock/prompt_cache/`

**Migration**: None needed - prompts auto-fetch on first run

### 2. Command Interface Changed
- ❌ Old: Complex zsh functions in ~/.zshrc
- ✅ New: Python scripts in `scripts/` directory
- ✅ New: Simple zsh aliases

**Migration**: Run `source ~/.zshrc` to activate new aliases

### 3. Server Management
- ❌ Old: Manual process management
- ✅ New: Smart auto-restart
- ✅ New: Orphaned process cleanup
- ✅ New: Health monitoring

**Migration**: None needed - commands work the same

---

## New Features

### 1. Official Prompt Fetching
- Fetches from OpenAI Codex GitHub repository
- 24-hour caching with auto-refresh
- Resilient fallback chain
- Thread-safe operations

### 2. Health Monitoring
- New endpoint: `GET /health`
- Request/error metrics
- Uptime tracking
- Prompt cache status

### 3. Enhanced Resilience
- Retry logic (3 attempts with backoff)
- Graceful degradation
- Worker auto-recycling
- Crash recovery

### 4. Management Scripts
- Python-based server management
- Better error handling
- Improved logging
- Status reporting

---

## File Changes

### Removed
```
prompt.md                    ❌ (fetched from GitHub)
prompt_gpt5_codex.md         ❌ (fetched from GitHub)
requirements.txt.backup      ❌ (cleanup)
```

### Added
```
chatmock/prompts.py          ✅ (prompt fetching & caching)
chatmock/health.py           ✅ (health monitoring)
scripts/chatmock-manage.py   ✅ (server management)
scripts/chatmock-*           ✅ (command wrappers)
gunicorn_config.py           ✅ (production config)
wsgi.py                      ✅ (WSGI entry point)
README_RESILIENT.md          ✅ (documentation)
PERFORMANCE.md               ✅ (performance guide)
QUICKREF.txt                 ✅ (quick reference)
```

### Modified
```
chatmock/config.py           🔄 (uses new prompt manager)
chatmock/app.py              🔄 (health monitoring)
chatmock/upstream.py         🔄 (resilience features)
chatmock/utils.py            🔄 (error handling)
.gitignore                   🔄 (prompt cache, backups)
requirements.txt             🔄 (gunicorn, gevent)
```

---

## Command Changes

### Old Commands (still work)
```bash
servercodex          # Start/restart
servercodexstatus    # Status
servercodexlogs      # Logs
servercodexstop      # Stop
servercodexupdate    # Update
```

### New Aliases
```bash
sc                   # Short for servercodex
scstatus             # Short for servercodexstatus
sclogs               # Short for servercodexlogs
scstop               # Short for servercodexstop
scupdate             # Short for servercodexupdate
```

### Advanced Options
```bash
chatmock-start -v            # Verbose mode
chatmock-stop -f             # Force stop
chatmock-update --no-stash   # No stash
```

---

## Configuration Changes

### Environment Variables (NEW)
```bash
CHATMOCK_VERBOSE=1                    # Verbose logging
CHATMOCK_EXPOSE_REASONING=1           # Expose reasoning models
CHATMOCK_ENABLE_WEB_SEARCH=1          # Enable web search
CHATMOCK_ENABLE_RESPONSES_API=1       # Enable Responses API
```

### Cache Location (NEW)
```
~/.chatmock/prompt_cache/
├── base_instructions.md
├── gpt5_codex_instructions.md
└── metadata.json
```

---

## Performance Improvements

### Before
- Single Flask dev server
- Limited concurrency
- Manual restarts
- No health monitoring

### After
- Gunicorn with gevent workers
- 100+ concurrent requests
- Auto-restart & recovery
- Health monitoring
- Worker recycling

---

## Migration Steps

1. **Update code**:
   ```bash
   cd ~/dev/yaananth/ChatMock
   git pull  # or apply changes
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Activate new commands**:
   ```bash
   source ~/.zshrc
   ```

4. **Start server**:
   ```bash
   servercodex
   ```

5. **Verify**:
   ```bash
   servercodexstatus
   curl http://localhost:8000/health
   ```

---

## Rollback (if needed)

If you need to rollback:

1. **Restore old prompts**:
   ```bash
   git checkout main -- prompt.md prompt_gpt5_codex.md
   ```

2. **Restore old config**:
   ```bash
   cp ~/.zshrc.before-script-refactor ~/.zshrc
   source ~/.zshrc
   ```

3. **Use old server**:
   ```bash
   python chatmock.py serve --port 8000
   ```

---

## Testing Checklist

- [ ] Server starts successfully
- [ ] Health endpoint responds
- [ ] Chat completions work
- [ ] Responses API works (if enabled)
- [ ] Prompts cached in ~/.chatmock/
- [ ] Status command shows metrics
- [ ] Logs accessible
- [ ] Server stops gracefully
- [ ] Update command works

---

## Support

For issues:
1. Check logs: `servercodexlogs`
2. Check status: `servercodexstatus`
3. Check health: `curl http://localhost:8000/health | jq`
4. Review: `README_RESILIENT.md`

---

## Credits

- All 3 GitHub PRs integrated (#50, #52, #57)
- Production-ready architecture
- High-performance configuration
- Comprehensive resilience
