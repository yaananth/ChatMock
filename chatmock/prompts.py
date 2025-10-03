"""
Prompt manager that fetches instructions from the upstream OpenAI Codex repository.

This module keeps a small on-disk cache so we do not fetch on every request, and it
validates downloaded prompts against an allowlist of hashes that are known to work
with the ChatGPT Responses API. If the latest upstream prompt is ahead of what the
API accepts, we seamlessly fall back to the most recent allowed commit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

LOGGER = logging.getLogger(__name__)

CODEX_BASE_PRIMARY = "https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/prompt.md"
CODEX_GPT5_PRIMARY = "https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/gpt_5_codex_prompt.md"

# Known-good commits whose prompts are accepted by the upstream Responses API.
CODEX_BASE_FALLBACK_COMMITS: Dict[str, str] = {
    "30c68535a3251254dd5a45cbbc18fe0312b8cc7cd78f6158a6aad87e9fb61033": "https://raw.githubusercontent.com/openai/codex/81b148bda271615b37f7e04b3135e9d552df8111/codex-rs/core/prompt.md",
    "47b092a0a6453260204c5d35a7ef3706b13028c2373d7665f257f54a7deb4e9a": "https://raw.githubusercontent.com/RayBytes/ChatMock/70025724f8fd5e72e337a5506ae1a865cf8ed88b/prompt.md",
}
CODEX_GPT5_FALLBACK_COMMITS: Dict[str, str] = {
    "32306fa2af2afd5dc3ad570700f6b457af73e576a97935b99d97b5a21f5d458b": CODEX_GPT5_PRIMARY,
    "f3ec1a90966ef8360ea79b3f8c925328b12b33a8a67c5651d2b400dd84d0e464": "https://raw.githubusercontent.com/RayBytes/ChatMock/70025724f8fd5e72e337a5506ae1a865cf8ed88b/prompt_gpt5_codex.md",
}

# Cache configuration
CACHE_DIR = Path.home() / ".chatmock" / "prompt_cache"
CACHE_TTL_HOURS = 24
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 2

_lock = threading.RLock()
_prompts_cache: Dict[str, Dict[str, object]] = {}

@dataclass
class PromptSource:
    prompt_type: str
    primary_url: str
    fallbacks: Dict[str, str]
    dynamic_hashes: set[str] = field(default_factory=set)

    def allowed_hashes(self) -> set[str]:
        return set(self.fallbacks.keys()) | set(self.dynamic_hashes)

PROMPT_SOURCES: Dict[str, PromptSource] = {
    "base_instructions": PromptSource("base_instructions", CODEX_BASE_PRIMARY, CODEX_BASE_FALLBACK_COMMITS),
    "gpt5_codex_instructions": PromptSource("gpt5_codex_instructions", CODEX_GPT5_PRIMARY, CODEX_GPT5_FALLBACK_COMMITS),
}

DYNAMIC_PROMPT_CONTENT: Dict[str, Dict[str, str]] = {}
BANNED_DYNAMIC_HASHES: Dict[str, set[str]] = {}



class PromptCache:
    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.cache_dir / "metadata.json"
        self._metadata = self._load_metadata()

    def _load_metadata(self) -> Dict[str, object]:
        try:
            if self.metadata_file.exists():
                with self.metadata_file.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception:
            pass
        return {}

    def _save_metadata(self) -> None:
        try:
            with self.metadata_file.open("w", encoding="utf-8") as fh:
                json.dump(self._metadata, fh, indent=2)
        except Exception:
            pass

    def cache_path(self, prompt_type: str) -> Path:
        return self.cache_dir / f"{prompt_type}.md"

    def is_valid(self, prompt_type: str) -> bool:
        meta = self._metadata.get(prompt_type, {})
        cached_at = meta.get("cached_at")
        if not cached_at:
            return False
        try:
            cached_time = datetime.fromisoformat(str(cached_at))
        except Exception:
            return False
        return datetime.now() - cached_time < timedelta(hours=CACHE_TTL_HOURS)

    def read(self, prompt_type: str) -> Optional[str]:
        path = self.cache_path(prompt_type)
        if not path.exists() or not self.is_valid(prompt_type):
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    def read_any(self, prompt_type: str) -> Optional[str]:
        path = self.cache_path(prompt_type)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    def write(self, prompt_type: str, content: str, digest: str) -> None:
        path = self.cache_path(prompt_type)
        try:
            path.write_text(content, encoding="utf-8")
            self._metadata[prompt_type] = {
                "cached_at": datetime.now().isoformat(),
                "content_hash": digest,
                "size": len(content),
            }
            self._save_metadata()
        except Exception:
            pass


def _rehydrate_dynamic_hashes_from_metadata() -> None:
    try:
        cache = PromptCache()
        dynamic_meta = cache._metadata.get("dynamic_hashes", {})
        banned_meta = cache._metadata.get("dynamic_hashes_banned", {})
    except Exception:
        return
    if isinstance(banned_meta, dict):
        for prompt_type, entries in banned_meta.items():
            if not isinstance(entries, dict):
                continue
            banned_set = BANNED_DYNAMIC_HASHES.setdefault(prompt_type, set())
            for digest in entries.keys():
                if isinstance(digest, str):
                    banned_set.add(digest)
    if not isinstance(dynamic_meta, dict):
        return
    for prompt_type, entries in dynamic_meta.items():
        source = PROMPT_SOURCES.get(prompt_type)
        if not source or not isinstance(entries, dict):
            continue
        for digest in entries.keys():
            if isinstance(digest, str) and digest not in BANNED_DYNAMIC_HASHES.get(prompt_type, set()):
                source.dynamic_hashes.add(digest)


_rehydrate_dynamic_hashes_from_metadata()


def _fetch_url(url: str) -> Optional[str]:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "ChatMock/1.0", "Accept": "text/plain"},
            )
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
            if resp.status_code == 404:
                return None
        except requests.Timeout:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue
    return None


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()



def _select_dynamic_prompt(prompt_type: str) -> Optional[Tuple[str, str]]:
    entries = DYNAMIC_PROMPT_CONTENT.get(prompt_type)
    if not entries:
        return None
    digest, content = list(entries.items())[-1]
    return content, digest


def _record_dynamic_hash(prompt_type: str, digest: str, source: str) -> None:
    try:
        cache = PromptCache()
        dynamic_meta = cache._metadata.setdefault("dynamic_hashes", {})
        prompt_meta = dynamic_meta.setdefault(prompt_type, {})
        prompt_meta[digest] = {
            "source": source,
            "discovered_at": datetime.now().isoformat(),
        }
        cache._save_metadata()
    except Exception:
        pass


def _register_dynamic_prompt(prompt_type: str, content: str, source: str) -> None:
    source_obj = PROMPT_SOURCES.get(prompt_type)
    if not source_obj:
        return
    digest = _hash(content)
    if digest in source_obj.allowed_hashes():
        return
    if digest in BANNED_DYNAMIC_HASHES.get(prompt_type, set()):
        LOGGER.info(
            "Skipping banned dynamic prompt hash %s for %s", digest, prompt_type
        )
        return
    source_obj.dynamic_hashes.add(digest)
    bucket = DYNAMIC_PROMPT_CONTENT.setdefault(prompt_type, {})
    bucket[digest] = content
    _record_dynamic_hash(prompt_type, digest, source)
    LOGGER.info(
        "Registered local Codex prompt for %s hash=%s source=%s",
        prompt_type,
        digest,
        source,
    )


def _extract_prompt_from_binary(binary_path: Path, marker: str) -> Optional[str]:
    try:
        data = binary_path.read_bytes()
    except Exception:
        return None
    marker_bytes = marker.encode("utf-8")
    idx = data.find(marker_bytes)
    if idx == -1:
        return None
    end = data.find(b"\x00", idx)
    if end == -1:
        end = len(data)
    try:
        prompt = data[idx:end].decode("utf-8", errors="ignore")
    except Exception:
        return None
    return prompt.strip()


def _discover_local_codex_prompts() -> None:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return
    module_root = Path(codex_bin).resolve().parent.parent
    vendor_dir = module_root / "vendor"
    if not vendor_dir.exists():
        return
    binary_path: Optional[Path] = None
    for arch_dir in vendor_dir.iterdir():
        codex_dir = arch_dir / "codex"
        if not codex_dir.is_dir():
            continue
        for candidate_name in ("codex", "codex.exe"):
            candidate = codex_dir / candidate_name
            if candidate.is_file():
                binary_path = candidate
                break
        if binary_path:
            break
    if not binary_path:
        return
    try:
        source_label = f"{module_root}::{binary_path.relative_to(module_root)}"
    except Exception:
        source_label = str(binary_path)
    base_prompt = _extract_prompt_from_binary(binary_path, "You are a coding agent running in the Codex CLI")
    if base_prompt:
        _register_dynamic_prompt("base_instructions", base_prompt, source_label)
    gpt5_prompt = _extract_prompt_from_binary(binary_path, "You are Codex, based on GPT-5")
    if gpt5_prompt:
        _register_dynamic_prompt("gpt5_codex_instructions", gpt5_prompt, source_label)


def mark_prompt_invalid(prompt_type: str, instructions: str, reason: str) -> None:
    if not instructions:
        return
    source = PROMPT_SOURCES.get(prompt_type)
    if not source:
        return
    digest = _hash(instructions)
    if digest in source.fallbacks:
        return
    removed = False
    if digest in source.dynamic_hashes:
        source.dynamic_hashes.discard(digest)
        removed = True
    bucket = DYNAMIC_PROMPT_CONTENT.get(prompt_type)
    if bucket:
        bucket.pop(digest, None)
    BANNED_DYNAMIC_HASHES.setdefault(prompt_type, set()).add(digest)
    try:
        cache = PromptCache()
        dynamic_meta = cache._metadata.get("dynamic_hashes", {})
        if isinstance(dynamic_meta, dict):
            prompt_meta = dynamic_meta.get(prompt_type)
            if isinstance(prompt_meta, dict) and digest in prompt_meta:
                prompt_meta.pop(digest, None)
                cache._metadata["dynamic_hashes"] = dynamic_meta
                cache._save_metadata()
    except Exception:
        pass
    try:
        cache = PromptCache()
        banned_meta = cache._metadata.setdefault("dynamic_hashes_banned", {})
        if isinstance(banned_meta, dict):
            prompt_meta = banned_meta.setdefault(prompt_type, {})
            if isinstance(prompt_meta, dict):
                prompt_meta[digest] = {
                    "reason": reason,
                    "timestamp": datetime.now().isoformat(),
                }
                cache._metadata["dynamic_hashes_banned"] = banned_meta
                cache._save_metadata()
    except Exception:
        pass
    if removed:
        try:
            cache = PromptCache()
            cache_path = cache.cache_path(prompt_type)
            if cache_path.exists():
                content = cache_path.read_text(encoding="utf-8")
                if _hash(content) == digest:
                    cache_path.unlink(missing_ok=True)
        except Exception:
            pass
    LOGGER.warning(
        "Banned dynamic prompt hash %s for %s due to %s",
        digest,
        prompt_type,
        reason,
    )


_discover_local_codex_prompts()


def _should_accept(prompt_type: str, digest: str) -> bool:
    env_override = os.getenv("CHATMOCK_PROMPT_ACCEPT_ANY", "0").strip().lower()
    if env_override in {"1", "true", "yes", "on"}:
        return True
    source = PROMPT_SOURCES[prompt_type]
    return digest in source.allowed_hashes()


def _load_from_remote(source: PromptSource) -> Optional[tuple[str, str]]:
    content = _fetch_url(source.primary_url)
    if not content:
        return None
    digest = _hash(content)
    if _should_accept(source.prompt_type, digest):
        return content, digest
    LOGGER.warning(
        "Remote prompt %s has unexpected digest %s; attempting fallbacks.",
        source.prompt_type,
        digest,
    )
    return None


def _load_from_fallbacks(source: PromptSource) -> Optional[tuple[str, str]]:
    for digest, url in source.fallbacks.items():
        content = _fetch_url(url)
        if not content:
            continue
        if _hash(content) != digest:
            continue
        return content, digest
    return None


def _get_prompt(prompt_type: str, fallback_content: Optional[str] = None) -> str:
    source = PROMPT_SOURCES[prompt_type]
    with _lock:
        cached = _prompts_cache.get(prompt_type)
        if cached and cached.get("valid_until", 0) > time.time():
            return cached["content"]

        cache = PromptCache()
        disk = cache.read(prompt_type)
        if disk:
            digest = _hash(disk)
            if _should_accept(prompt_type, digest):
                _prompts_cache[prompt_type] = {
                    "content": disk,
                    "valid_until": time.time() + CACHE_TTL_HOURS * 3600,
                }
                return disk
            LOGGER.warning(
                "Cached prompt %s has digest %s which is not allowed; ignoring.",
                prompt_type,
                digest,
            )

        dynamic_candidate = _select_dynamic_prompt(prompt_type)
        if dynamic_candidate:
            dyn_content, dyn_digest = dynamic_candidate
            if _should_accept(prompt_type, dyn_digest):
                _prompts_cache[prompt_type] = {
                    "content": dyn_content,
                    "valid_until": time.time() + CACHE_TTL_HOURS * 3600,
                }
                cache.write(prompt_type, dyn_content, dyn_digest)
                return dyn_content

        result = _load_from_remote(source)
        if not result:
            result = _load_from_fallbacks(source)
        if result:
            content, digest = result
            _prompts_cache[prompt_type] = {
                "content": content,
                "valid_until": time.time() + CACHE_TTL_HOURS * 3600,
            }
            cache.write(prompt_type, content, digest)
            return content

        stale = cache.read_any(prompt_type)
        if stale and stale.strip():
            LOGGER.warning("Using stale cached prompt for %s; remote fetch failed.", prompt_type)
            return stale

        if fallback_content:
            LOGGER.warning("Falling back to provided inline prompt for %s.", prompt_type)
            return fallback_content

        LOGGER.error("Unable to load prompt for %s; returning empty string.", prompt_type)
        return ""


def get_cached_base_instructions() -> str:
    local_fallback = None
    try:
        local_path = Path(__file__).parent.parent / "prompt.md"
        if local_path.exists():
            local_fallback = local_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return _get_prompt("base_instructions", local_fallback)


def get_cached_gpt5_codex_instructions() -> str:
    local_fallback = None
    try:
        local_path = Path(__file__).parent.parent / "prompt_gpt5_codex.md"
        if local_path.exists():
            local_fallback = local_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return _get_prompt("gpt5_codex_instructions", local_fallback)


def invalidate_cache(prompt_type: Optional[str] = None) -> None:
    with _lock:
        if prompt_type:
            _prompts_cache.pop(prompt_type, None)
        else:
            _prompts_cache.clear()


def get_cache_info() -> dict:
    cache = PromptCache()
    return {
        "cache_dir": str(cache.cache_dir),
        "metadata": cache._metadata,
        "ttl_hours": CACHE_TTL_HOURS,
        "in_memory_cache": list(_prompts_cache.keys()),
    }
