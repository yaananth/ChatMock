from __future__ import annotations

from flask import Flask, jsonify

from .config import BASE_INSTRUCTIONS, GPT5_CODEX_INSTRUCTIONS
from .http import build_cors_headers
from .routes_openai import openai_bp
from .routes_ollama import ollama_bp
from .routes_responses import responses_bp
from .health import create_health_response, increment_request, increment_success, increment_error


def create_app(
    verbose: bool = False,
    reasoning_effort: str = "medium",
    reasoning_summary: str = "auto",
    reasoning_compat: str = "think-tags",
    debug_model: str | None = None,
    expose_reasoning_models: bool = False,
    default_web_search: bool = False,
    enable_responses_api: bool = False,
    responses_no_base_instructions: bool = False,
) -> Flask:
    app = Flask(__name__)

    app.config.update(
        VERBOSE=bool(verbose),
        REASONING_EFFORT=reasoning_effort,
        REASONING_SUMMARY=reasoning_summary,
        REASONING_COMPAT=reasoning_compat,
        DEBUG_MODEL=debug_model,
        BASE_INSTRUCTIONS=BASE_INSTRUCTIONS,
        GPT5_CODEX_INSTRUCTIONS=GPT5_CODEX_INSTRUCTIONS,
        EXPOSE_REASONING_MODELS=bool(expose_reasoning_models),
        DEFAULT_WEB_SEARCH=bool(default_web_search),
        ENABLE_RESPONSES_API=bool(enable_responses_api),
        RESPONSES_NO_BASE_INSTRUCTIONS=bool(responses_no_base_instructions),
    )

    # Middleware for request tracking
    @app.before_request
    def track_request():
        increment_request()

    @app.after_request
    def track_response(response):
        if response.status_code < 400:
            increment_success()
        else:
            increment_error()
        return response

    @app.get("/")
    @app.get("/health")
    def health():
        return create_health_response()

    @app.after_request
    def _cors(resp):
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    app.register_blueprint(openai_bp)
    app.register_blueprint(ollama_bp)
    if bool(app.config.get("ENABLE_RESPONSES_API")):
        app.register_blueprint(responses_bp)

    return app
