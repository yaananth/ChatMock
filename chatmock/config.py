from __future__ import annotations

import os

CLIENT_ID_DEFAULT = os.getenv("CHATGPT_LOCAL_CLIENT_ID") or "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_ISSUER_DEFAULT = os.getenv("CHATGPT_LOCAL_ISSUER") or "https://auth.openai.com"
OAUTH_TOKEN_URL = f"{OAUTH_ISSUER_DEFAULT}/oauth/token"

CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"

# Import prompts from new resilient prompt manager
try:
    from .prompts import get_cached_base_instructions, get_cached_gpt5_codex_instructions
    
    BASE_INSTRUCTIONS = get_cached_base_instructions()
    GPT5_CODEX_INSTRUCTIONS = get_cached_gpt5_codex_instructions()
except Exception as e:
    # Ultimate fallback - minimal instructions
    print(f"Warning: Failed to load prompts from GitHub cache: {e}")
    BASE_INSTRUCTIONS = "You are a helpful AI assistant. Follow the user's instructions carefully."
    GPT5_CODEX_INSTRUCTIONS = BASE_INSTRUCTIONS
