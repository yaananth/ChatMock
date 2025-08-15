from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import time
import urllib.parse
import webbrowser
from typing import Any, Dict, Generator, List

import requests
from flask import Flask, Response, jsonify, make_response, request

from oauth import OAuthHTTPServer, OAuthHandler, REQUIRED_PORT, URL_BASE
from models import AuthBundle, PkceCodes, TokenData
from utils import (
    convert_chat_messages_to_responses_input,
    convert_tools_chat_to_responses,
    eprint,
    get_effective_chatgpt_auth,
    get_home_dir,
    load_chatgpt_tokens,
    parse_jwt_claims,
    read_auth_file,
    sse_translate_chat,
    sse_translate_text,
)

CLIENT_ID_DEFAULT = os.getenv("CHATGPT_LOCAL_CLIENT_ID") or "app_EMoamEEZ73f0CkXaXp7hrann"

CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"

def read_base_instructions() -> str:
    try:
        with open(os.path.join(os.path.dirname(__file__), "prompt.md"), "r", encoding="utf-8") as f:
            content = f.read()
            if isinstance(content, str) and content.strip():
                return content
    except FileNotFoundError:
        raise Exception("Failed to read prompt.md, make sure it exists in the same directory you are running this script from!")

BASE_INSTRUCTIONS = read_base_instructions()

def create_app(
    verbose: bool = False,
    reasoning_effort: str = "medium",
    reasoning_summary: str = "auto",
    reasoning_compat: str = "think-tags",
    debug_model: str | None = None,
) -> Flask:
    app = Flask(__name__)

    def vlog(*args: Any) -> None:
        if verbose:
            print(*args, file=sys.stderr)

    def build_cors_headers() -> dict:
        origin = request.headers.get("Origin", "*")
        req_headers = request.headers.get("Access-Control-Request-Headers")
        allow_headers = req_headers if req_headers else "Authorization, Content-Type, Accept"
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": allow_headers,
            "Access-Control-Max-Age": "86400",
        }

    @app.get("/")
    @app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok"})

    def _build_reasoning_param(overrides: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
        effort = (reasoning_effort or "").strip().lower()
        summary = (reasoning_summary or "").strip().lower()

        valid_efforts = {"low", "medium", "high", "none"}
        valid_summaries = {"auto", "concise", "detailed", "none"}

        if isinstance(overrides, dict):
            o_eff = str(overrides.get("effort", "")).strip().lower()
            o_sum = str(overrides.get("summary", "")).strip().lower()
            if o_eff in valid_efforts and o_eff:
                effort = o_eff
            if o_sum in valid_summaries and o_sum:
                summary = o_sum
        if effort not in valid_efforts:
            effort = "medium"
        if summary not in valid_summaries:
            summary = "auto"

        reasoning: Dict[str, Any] = {"effort": effort}
        if summary != "none":
            reasoning["summary"] = summary
        return reasoning

    @app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
    def chat_completions() -> Response:
        if request.method == "OPTIONS":
            resp = make_response("", 204)
            for k, v in build_cors_headers().items():
                resp.headers[k] = v
            return resp

        try:
            if verbose:
                body_preview = (request.get_data(cache=True, as_text=True) or "")[:2000]
                vlog("IN POST /v1/chat/completions\n" + body_preview)
        except Exception:
            pass

        access_token, account_id = get_effective_chatgpt_auth()
        if not access_token or not account_id:
            return jsonify({
                "error": {
                    "message": "Missing ChatGPT credentials. Run 'python3 chatmock.py login' first.",
                }
            }), 401

        raw = request.get_data(cache=True, as_text=True) or ""
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            try:
                payload = json.loads(raw.replace("\r", "").replace("\n", ""))
            except Exception:
                return jsonify({"error": {"message": "Invalid JSON body"}}), 400

        model = _normalize_model_name(payload.get("model"))
        messages = payload.get("messages")
        if messages is None and isinstance(payload.get("prompt"), str):
            messages = [{"role": "user", "content": payload.get("prompt") or ""}]
        if messages is None and isinstance(payload.get("input"), str):
            messages = [{"role": "user", "content": payload.get("input") or ""}]
        if messages is None:
            messages = []
        if not isinstance(messages, list):
            return jsonify({"error": {"message": "Request must include messages: []"}}), 400
        is_stream = bool(payload.get("stream"))

        tools_responses = convert_tools_chat_to_responses(payload.get("tools"))
        tool_choice = payload.get("tool_choice", "auto")
        parallel_tool_calls = bool(payload.get("parallel_tool_calls", False))

        input_items = convert_chat_messages_to_responses_input(messages)
        if not input_items and isinstance(payload.get("prompt"), str) and payload.get("prompt").strip():
            input_items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": payload.get("prompt")}]}]

        instructions = BASE_INSTRUCTIONS

        reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else None

        upstream, error_resp = _start_upstream_request(
            model,
            input_items,
            instructions=instructions,
            tools=tools_responses,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_param=_build_reasoning_param(reasoning_overrides),
        )
        if error_resp is not None:
            return error_resp

        created = int(time.time())
        if upstream.status_code >= 400:
            try:
                raw = upstream.content
                err_body = json.loads(raw.decode("utf-8", errors="ignore")) if raw else {"raw": upstream.text}
            except Exception:
                err_body = {"raw": upstream.text}
            if verbose:
                vlog("Upstream error status=", upstream.status_code, " body:", json.dumps(err_body)[:2000])
            return (
                jsonify({"error": {"message": (err_body.get("error", {}) or {}).get("message", "Upstream error")}}),
                upstream.status_code,
            )

        if is_stream:
            resp = Response(
                sse_translate_chat(
                    upstream,
                    model,
                    created,
                    verbose=verbose,
                    vlog=vlog,
                    reasoning_compat=reasoning_compat,
                ),
                status=upstream.status_code,
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
            for k, v in build_cors_headers().items():
                resp.headers.setdefault(k, v)
            return resp

        full_text = ""
        reasoning_summary_text = ""
        reasoning_full_text = ""
        response_id = "chatcmpl"
        tool_calls: List[Dict[str, Any]] = []
        error_message: str | None = None
        try:
            for raw in upstream.iter_lines(decode_unicode=False):
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else raw
                if not line.startswith("data: "):
                    continue
                data = line[len("data: "):].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    evt = json.loads(data)
                except Exception:
                    continue
                kind = evt.get("type")
                if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                    response_id = evt["response"].get("id") or response_id
                if kind == "response.output_text.delta":
                    full_text += evt.get("delta") or ""
                elif kind == "response.reasoning_summary_text.delta":
                    reasoning_summary_text += evt.get("delta") or ""
                elif kind == "response.reasoning_text.delta":
                    reasoning_full_text += evt.get("delta") or ""
                elif kind == "response.output_item.done":
                    item = evt.get("item") or {}
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        call_id = item.get("call_id") or item.get("id") or ""
                        name = item.get("name") or ""
                        args = item.get("arguments") or ""
                        if isinstance(call_id, str) and isinstance(name, str) and isinstance(args, str):
                            tool_calls.append(
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {"name": name, "arguments": args},
                                }
                            )
                elif kind == "response.failed":
                    error_message = evt.get("response", {}).get("error", {}).get("message", "response.failed")
                elif kind == "response.completed":
                    break
        finally:
            upstream.close()

        if error_message:
            resp = make_response(jsonify({"error": {"message": error_message}}), 502)
            for k, v in build_cors_headers().items():
                resp.headers.setdefault(k, v)
            return resp

        message: Dict[str, Any] = {"role": "assistant", "content": full_text if full_text else None}
        if tool_calls:
            message["tool_calls"] = tool_calls

        try:
            compat = (reasoning_compat or "think-tags").strip().lower()
        except Exception:
            compat = "think-tags"

        if compat == "o3":
            rtxt_parts: List[str] = []
            if isinstance(reasoning_summary_text, str) and reasoning_summary_text.strip():
                rtxt_parts.append(reasoning_summary_text)
            if isinstance(reasoning_full_text, str) and reasoning_full_text.strip():
                rtxt_parts.append(reasoning_full_text)
            rtxt = "\n\n".join([p for p in rtxt_parts if p])
            if rtxt:
                message["reasoning"] = {"content": [{"type": "text", "text": rtxt}]}
        elif compat == "think-tags":
            rtxt_parts: List[str] = []
            if isinstance(reasoning_summary_text, str) and reasoning_summary_text.strip():
                rtxt_parts.append(reasoning_summary_text)
            if isinstance(reasoning_full_text, str) and reasoning_full_text.strip():
                rtxt_parts.append(reasoning_full_text)
            rtxt = "\n\n".join([p for p in rtxt_parts if p])
            if rtxt:
                think_block = f"<think>{rtxt}</think>"
                content_text = message.get("content") or ""
                if isinstance(content_text, str):
                    message["content"] = think_block + (content_text or "")
        elif compat in ("legacy", "current"):
            if reasoning_summary_text:
                message["reasoning_summary"] = reasoning_summary_text
            if reasoning_full_text:
                message["reasoning"] = reasoning_full_text
        else:
            rtxt_parts: List[str] = []
            if isinstance(reasoning_summary_text, str) and reasoning_summary_text.strip():
                rtxt_parts.append(reasoning_summary_text)
            if isinstance(reasoning_full_text, str) and reasoning_full_text.strip():
                rtxt_parts.append(reasoning_full_text)
            rtxt = "\n\n".join([p for p in rtxt_parts if p])
            if rtxt:
                think_block = f"<think>{rtxt}</think>"
                content_text = message.get("content") or ""
                if isinstance(content_text, str):
                    message["content"] = think_block + (content_text or "")

        completion = {
            "id": response_id or "chatcmpl",
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                }
            ],
        }
        resp = make_response(jsonify(completion), upstream.status_code)
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    @app.route("/v1/models", methods=["GET", "OPTIONS"])
    def list_models() -> Response:
        if request.method == "OPTIONS":
            resp = make_response("", 204)
            for k, v in build_cors_headers().items():
                resp.headers[k] = v
            return resp
        models = {
        "object": "list",
        "data": [
            {"id":"gpt-5","object":"model","owned_by":"owner"}
        ]
        }

        resp = make_response(jsonify(models), 200)
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp


    def _start_upstream_request(
        model: str,
        input_items: List[Dict[str, Any]],
        instructions: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        parallel_tool_calls: bool = False,
        reasoning_param: Dict[str, Any] | None = None,
    ):
        access_token, account_id = get_effective_chatgpt_auth()
        if not access_token or not account_id:
            resp = make_response(
                jsonify(
                    {
                        "error": {
                            "message": "Missing ChatGPT credentials. Run 'python3 chatmock.py login' first.",
                        }
                    }
                ),
                401,
            )
            for k, v in build_cors_headers().items():
                resp.headers.setdefault(k, v)
            return None, resp

        reasoning_param = reasoning_param if isinstance(reasoning_param, dict) else _build_reasoning_param()
        include: List[str] = []
        if isinstance(reasoning_param, dict) and reasoning_param.get("effort") != "none":
            include.append("reasoning.encrypted_content")

        responses_payload = {
            "model": model,
            "instructions": instructions if isinstance(instructions, str) and instructions.strip() else BASE_INSTRUCTIONS,
            "input": input_items,
            "tools": tools or [],
            "tool_choice": tool_choice if tool_choice in ("auto", "none") or isinstance(tool_choice, dict) else "auto",
            "parallel_tool_calls": bool(parallel_tool_calls),
            "store": False,
            "stream": True,
            "include": include,
        }

        if reasoning_param is not None:
            responses_payload["reasoning"] = reasoning_param

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "chatgpt-account-id": account_id,
        }
        headers["OpenAI-Beta"] = "responses=experimental"

        try:
            upstream = requests.post(
                CHATGPT_RESPONSES_URL,
                headers=headers,
                json=responses_payload,
                stream=True,
                timeout=600,
            )
        except requests.RequestException as e:
            resp = make_response(jsonify({"error": {"message": f"Upstream ChatGPT request failed: {e}"}}), 502)
            for k, v in build_cors_headers().items():
                resp.headers.setdefault(k, v)
            return None, resp
        return upstream, None

    def _normalize_model_name(name: str | None) -> str:
        if isinstance(debug_model, str) and debug_model.strip():
            return debug_model.strip()
        if not isinstance(name, str) or not name.strip():
            return "gpt-5"
        base = name.split(":", 1)[0].strip()
        mapping = {
            "gpt5": "gpt-5",
            "gpt-5-latest": "gpt-5",
            "gpt-5": "gpt-5",
            "codex": "codex-mini-latest",
            "codex-mini": "codex-mini-latest",
            "codex-mini-latest": "codex-mini-latest"
        }
        return mapping.get(base, base)

    @app.route("/v1/completions", methods=["POST", "OPTIONS"])
    def completions() -> Response:
        if request.method == "OPTIONS":
            resp = make_response("", 204)
            for k, v in build_cors_headers().items():
                resp.headers[k] = v
            return resp

        raw = request.get_data(cache=True, as_text=True) or ""
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            return jsonify({"error": {"message": "Invalid JSON body"}}), 400

        model = _normalize_model_name(payload.get("model"))
        prompt = payload.get("prompt")
        if isinstance(prompt, list):
            prompt = "".join([p if isinstance(p, str) else "" for p in prompt])
        if not isinstance(prompt, str):
            prompt = payload.get("suffix") or ""
        stream_req = bool(payload.get("stream", False))

        messages = [{"role": "user", "content": prompt or ""}]
        input_items = convert_chat_messages_to_responses_input(messages)

        reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else None
        upstream, error_resp = _start_upstream_request(
            model,
            input_items,
            instructions=BASE_INSTRUCTIONS,
            reasoning_param=_build_reasoning_param(reasoning_overrides),
        )
        if error_resp is not None:
            return error_resp

        created = int(time.time())
        if upstream.status_code >= 400:
            try:
                err_body = json.loads(upstream.content.decode("utf-8", errors="ignore")) if upstream.content else {"raw": upstream.text}
            except Exception:
                err_body = {"raw": upstream.text}
            return (
                jsonify({"error": {"message": (err_body.get("error", {}) or {}).get("message", "Upstream error")}}),
                upstream.status_code,
            )

        if stream_req:
            resp = Response(
                sse_translate_text(upstream, model, created, verbose=verbose, vlog=vlog),
                status=upstream.status_code,
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
            for k, v in build_cors_headers().items():
                resp.headers.setdefault(k, v)
            return resp

        full_text = ""
        response_id = "cmpl"
        try:
            for raw_line in upstream.iter_lines(decode_unicode=False):
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
                if not line.startswith("data: "):
                    continue
                data = line[len("data: "):].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    evt = json.loads(data)
                except Exception:
                    continue
                if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                    response_id = evt["response"].get("id") or response_id
                kind = evt.get("type")
                if kind == "response.output_text.delta":
                    full_text += evt.get("delta") or ""
                elif kind == "response.completed":
                    break
        finally:
            upstream.close()

        completion = {
            "id": response_id or "cmpl",
            "object": "text_completion",
            "created": created,
            "model": model,
            "choices": [
                {"index": 0, "text": full_text, "finish_reason": "stop", "logprobs": None}
            ],
        }
        resp = make_response(jsonify(completion), upstream.status_code)
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    return app


def cmd_login(no_browser: bool, verbose: bool) -> int:
    home_dir = get_home_dir()
    client_id = CLIENT_ID_DEFAULT
    if not client_id:
        eprint("ERROR: No OAuth client id configured. Set CHATGPT_LOCAL_CLIENT_ID.")
        return 1

    try:
        httpd = OAuthHTTPServer(("127.0.0.1", REQUIRED_PORT), OAuthHandler, home_dir=home_dir, client_id=client_id, verbose=verbose)
    except OSError as e:
        eprint(f"ERROR: {e}")
        if e.errno == errno.EADDRINUSE:
            return 13
        return 1

    auth_url = httpd.auth_url()
    with httpd:
        eprint(f"Starting local login server on {URL_BASE}")
        if not no_browser:
            try:
                webbrowser.open(auth_url, new=1, autoraise=True)
            except Exception as e:
                eprint(f"Failed to open browser: {e}")
        eprint(f"If your browser did not open, navigate to:\n{auth_url}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            eprint("\nKeyboard interrupt received, exiting.")
        return httpd.exit_code


def cmd_serve(
    host: str,
    port: int,
    verbose: bool,
    reasoning_effort: str,
    reasoning_summary: str,
    reasoning_compat: str,
    debug_model: str | None,
) -> int:
    app = create_app(
        verbose=verbose,
        reasoning_effort=reasoning_effort,
        reasoning_summary=reasoning_summary,
        reasoning_compat=reasoning_compat,
        debug_model=debug_model,
    )

    app.run(host=host, debug=False, use_reloader=False, port=port, threaded=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="ChatGPT Local: login & OpenAI-compatible proxy")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Authorize with ChatGPT and store tokens")
    p_login.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    p_login.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    p_serve = sub.add_parser("serve", help="Run local OpenAI-compatible server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    p_serve.add_argument(
        "--debug-model",
        dest="debug_model",
        default=os.getenv("CHATGPT_LOCAL_DEBUG_MODEL"),
        help="Forcibly override requested 'model' with this value",
    )
    p_serve.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high", "none"],
        default=os.getenv("CHATGPT_LOCAL_REASONING_EFFORT", "medium").lower(),
        help="Reasoning effort level for Responses API (default: medium)",
    )
    p_serve.add_argument(
        "--reasoning-summary",
        choices=["auto", "concise", "detailed", "none"],
        default=os.getenv("CHATGPT_LOCAL_REASONING_SUMMARY", "auto").lower(),
        help="Reasoning summary verbosity (default: auto)",
    )
    p_serve.add_argument(
        "--reasoning-compat",
        choices=["legacy", "o3", "think-tags", "current"],
        default=os.getenv("CHATGPT_LOCAL_REASONING_COMPAT", "think-tags").lower(),
        help="Compatibility mode for exposing reasoning to clients (legacy|o3|think-tags). 'current' is accepted as an alias for 'legacy'",
    )

    p_info = sub.add_parser("info", help="Print current stored tokens and derived account id")
    p_info.add_argument("--json", action="store_true", help="Output raw auth.json contents")

    args = parser.parse_args()

    if args.command == "login":
        sys.exit(cmd_login(no_browser=args.no_browser, verbose=args.verbose))
    elif args.command == "serve":
        sys.exit(
            cmd_serve(
                host=args.host,
                port=args.port,
                verbose=args.verbose,
                reasoning_effort=args.reasoning_effort,
                reasoning_summary=args.reasoning_summary,
                reasoning_compat=args.reasoning_compat,
                debug_model=args.debug_model,
            )
        )
    elif args.command == "info":
        auth = read_auth_file()
        if getattr(args, "json", False):
            print(json.dumps(auth or {}, indent=2))
            sys.exit(0)
        access_token, account_id, id_token = load_chatgpt_tokens()
        if not access_token or not id_token:
            print("👤 Account")
            print("  • Not signed in")
            print("  • Run: python3 chatmock.py login")
            sys.exit(0)

        id_claims = parse_jwt_claims(id_token) or {}
        access_claims = parse_jwt_claims(access_token) or {}

        email = id_claims.get("email") or id_claims.get("preferred_username") or "<unknown>"
        plan_raw = (access_claims.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type") or "unknown"
        plan_map = {
            "plus": "Plus",
            "pro": "Pro",
            "free": "Free",
            "team": "Team",
            "enterprise": "Enterprise",
        }
        plan = plan_map.get(str(plan_raw).lower(), str(plan_raw).title() if isinstance(plan_raw, str) else "Unknown")

        print("👤 Account")
        print("  • Signed in with ChatGPT")
        print(f"  • Login: {email}")
        print(f"  • Plan: {plan}")
        if account_id:
            print(f"  • Account ID: {account_id}")
        sys.exit(0)
    else:
        parser.error("Unknown command")


if __name__ == "__main__":
    main()
