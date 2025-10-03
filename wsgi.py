"""
WSGI entry point for production deployment with gunicorn.
Usage: gunicorn -c gunicorn_config.py wsgi:app
"""
from chatmock.app import create_app
import os

# Get configuration from environment or use defaults
verbose = os.getenv("CHATMOCK_VERBOSE", "").lower() in ("1", "true", "yes")
reasoning_effort = os.getenv("CHATMOCK_REASONING_EFFORT", "medium")
reasoning_summary = os.getenv("CHATMOCK_REASONING_SUMMARY", "auto")
reasoning_compat = os.getenv("CHATMOCK_REASONING_COMPAT", "think-tags")
debug_model = os.getenv("CHATMOCK_DEBUG_MODEL")
expose_reasoning_models = os.getenv("CHATMOCK_EXPOSE_REASONING", "").lower() in ("1", "true", "yes")
default_web_search = os.getenv("CHATMOCK_ENABLE_WEB_SEARCH", "true").lower() in ("1", "true", "yes")
enable_responses_api = os.getenv("CHATMOCK_ENABLE_RESPONSES_API", "").lower() in ("1", "true", "yes")
responses_no_base_instructions = os.getenv("CHATMOCK_RESPONSES_NO_BASE_INSTRUCTIONS", "").lower() in ("1", "true", "yes")

# Create the Flask application
app = create_app(
    verbose=verbose,
    reasoning_effort=reasoning_effort,
    reasoning_summary=reasoning_summary,
    reasoning_compat=reasoning_compat,
    debug_model=debug_model,
    expose_reasoning_models=expose_reasoning_models,
    default_web_search=default_web_search,
    enable_responses_api=enable_responses_api,
    responses_no_base_instructions=responses_no_base_instructions,
)

if __name__ == "__main__":
    # For development only - use gunicorn for production
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
