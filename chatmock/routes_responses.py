from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
import threading
from collections import OrderedDict
from pathlib import Path
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, make_response, request, stream_with_context
from requests.exceptions import ChunkedEncodingError, ConnectionError, ReadTimeout
try:
    from urllib3.exceptions import ProtocolError
except Exception:
    ProtocolError = Exception

from .config import BASE_INSTRUCTIONS, GPT5_CODEX_INSTRUCTIONS
from .prompts import mark_prompt_invalid
from .http import build_cors_headers
from .limits import record_rate_limits_from_response
from .reasoning import build_reasoning_param, extract_reasoning_from_model_name
from .upstream import normalize_model_name, start_upstream_request
from .utils import convert_chat_messages_to_responses_input, convert_tools_chat_to_responses


responses_bp = Blueprint("responses", __name__)

# Simple in-memory store for Responses objects (FIFO, size-limited)
_STORE_LOCK = threading.Lock()
_STORE: OrderedDict[str, Dict[str, Any]] = OrderedDict()

# Simple in-memory threads map: response_id -> list of input items representing the
# conversation so far to prepend for previous_response_id simulation (non-stream focus)
_THREADS_LOCK = threading.Lock()
_THREADS: Dict[str, List[Dict[str, Any]]] = {}


def _store_response(obj: Dict[str, Any], *, max_items: int = 200) -> None:
    try:
        rid = obj.get("id")
        if not isinstance(rid, str) or not rid:
            return
        with _STORE_LOCK:
            # Move to end if exists, else insert
            if rid in _STORE:
                _STORE.pop(rid, None)
            _STORE[rid] = obj
            # Trim to max_items
            while len(_STORE) > max_items:
                _STORE.popitem(last=False)
    except Exception:
        pass


def _get_response(rid: str) -> Dict[str, Any] | None:
    with _STORE_LOCK:
        return _STORE.get(rid)


def _set_thread(rid: str, items: List[Dict[str, Any]]) -> None:
    try:
        if not (isinstance(rid, str) and rid and isinstance(items, list)):
            return
        # Clamp thread length to prevent runaway growth
        MAX_ITEMS = 40
        trimmed = items[-MAX_ITEMS:]
        with _THREADS_LOCK:
            _THREADS[rid] = trimmed
    except Exception:
        pass


def _get_thread(rid: str) -> List[Dict[str, Any]] | None:
    with _THREADS_LOCK:
        return _THREADS.get(rid)


def _collect_ids_with_rs_prefix(obj: Any, parent_key: str | None = None, out: List[str] | None = None) -> List[str]:
    """Collect strings that look like upstream response ids (rs_*) only in structural fields.

    We deliberately ignore plain text under common text fields like 'content'/'text'.
    """
    if out is None:
        out = []
    try:
        if isinstance(obj, str):
            key = (parent_key or "").lower()
            structural_keys = {
                "previous_response_id",
                "response_id",
                "reference_id",
                "item_id",
            }
            if key in structural_keys and obj.strip().startswith("rs_"):
                out.append(obj.strip())
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _collect_ids_with_rs_prefix(v, k, out)
        elif isinstance(obj, list):
            for v in obj:
                _collect_ids_with_rs_prefix(v, parent_key, out)
    except Exception:
        pass
    return out


def _sanitize_input_remove_upstream_refs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def drop_ref_fields(d: Dict[str, Any]) -> Dict[str, Any]:
        try:
            for key in ("previous_response_id", "response_id", "reference_id", "item_id"):
                if isinstance(d.get(key), str) and d.get(key, "").startswith("rs_"):
                    d.pop(key, None)
        except Exception:
            pass
        return d

    out: List[Dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        # If this item itself references an rs_* id in structural fields, strip those fields but keep the item
        it2 = drop_ref_fields(dict(it))
        content = it2.get("content")
        if isinstance(content, list):
            new_parts = []
            for p in content:
                if not isinstance(p, dict):
                    new_parts.append(p)
                    continue
                # If a part carries an rs_* id in a structural field, strip those fields but keep the part
                if _collect_ids_with_rs_prefix(p):
                    p = {kk: vv for kk, vv in p.items() if kk not in ("previous_response_id", "response_id", "reference_id", "item_id")}
                new_parts.append(p)
            it2["content"] = new_parts
        out.append(it2)
    return out


def _log_event(event: str, **fields: Any) -> None:
    """Append a structured JSONL log entry.

    Fields are best-effort JSON-serialized. Large strings may be included; set
    CHATMOCK_RESPONSES_LOG_BODY=false in config to suppress raw bodies.
    """
    try:
        log_enabled = bool(current_app.config.get("VERBOSE")) or bool(current_app.config.get("CHATMOCK_RESPONSES_LOG"))
        if not log_enabled:
            return
        repo_root = Path(__file__).resolve().parent.parent
        log_path = repo_root / "responses_debug.jsonl"
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        entry: Dict[str, Any] = {"ts": ts, "event": event}
        # Optionally redact large bodies if disabled
        allow_body = str(current_app.config.get("CHATMOCK_RESPONSES_LOG_BODY") or "1").strip().lower() not in ("0", "false", "no", "off")
        def _scrub(v: Any) -> Any:
            if allow_body:
                return v
            if isinstance(v, dict):
                return {k: _scrub(v.get(k)) for k in v.keys()}
            if isinstance(v, list):
                return [_scrub(x) for x in v]
            if isinstance(v, str) and len(v) > 256:
                return v[:256] + "…"  # preview only
            return v
        for k, v in fields.items():
            entry[k] = _scrub(v)
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _instructions_for_model(model: str) -> str:
    base = current_app.config.get("BASE_INSTRUCTIONS", BASE_INSTRUCTIONS)
    if model == "gpt-5-codex":
        codex = current_app.config.get("GPT5_CODEX_INSTRUCTIONS") or GPT5_CODEX_INSTRUCTIONS
        if isinstance(codex, str) and codex.strip():
            return codex
    return base


@responses_bp.route("/v1/responses", methods=["POST"])
def responses_stream() -> Response:
    """Streaming passthrough Responses API (experimental).

    Notes:
    - Only streaming is supported. If the request sets stream=false, returns 400.
    - Events are forwarded verbatim from upstream without translation.
    - Supports function tools plus optional web_search passthrough via responses_tools.
    """

    verbose = bool(current_app.config.get("VERBOSE"))
    reasoning_effort = current_app.config.get("REASONING_EFFORT", "medium")
    reasoning_summary = current_app.config.get("REASONING_SUMMARY", "auto")
    debug_model = current_app.config.get("DEBUG_MODEL")

    raw = request.get_data(cache=True, as_text=True) or ""
    try:
        # Console preview (truncated) plus structured log
        if verbose:
            print("IN POST /v1/responses\n" + (raw[:2000] if isinstance(raw, str) else ""))
        if isinstance(raw, str):
            _log_event(
                "request_received",
                route="/v1/responses",
                bytes=len(raw.encode("utf-8")),
                body=raw,
                headers={k: v for k, v in request.headers.items() if k.lower() in ("content-type", "x-session-id", "user-agent")},
            )
    except Exception:
        pass
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        return jsonify({"error": {"message": "Invalid JSON body"}}), 400
    
    # Log request summary for debugging
    try:
        import sys
        req_summary = {
            "model": payload.get("model"),
            "stream": payload.get("stream"),
            "has_input": "input" in payload,
            "has_messages": "messages" in payload,
            "user_agent": request.headers.get("User-Agent", "unknown"),
        }
        if "input" in payload:
            inp = payload["input"]
            if isinstance(inp, list) and inp:
                first_item = inp[0] if inp else {}
                if isinstance(first_item, dict):
                    content = first_item.get("content", [])
                    if isinstance(content, list) and content:
                        first_part = content[0] if content else {}
                        if isinstance(first_part, dict):
                            req_summary["input_type"] = first_part.get("type", "unknown")
        
        print(f"[RESPONSES] Request: {json.dumps(req_summary)}", file=sys.stderr)
    except Exception:
        pass

    stream_req = payload.get("stream")
    if stream_req is None:
        stream_req = True
    stream_req = bool(stream_req)

    requested_model = payload.get("model")
    model = normalize_model_name(requested_model, debug_model)
    prompt_key = "gpt5_codex_instructions" if model == "gpt-5-codex" else "base_instructions"

    # Accept either Responses `input` or Chat-style `messages`/`prompt` fallbacks
    input_items: List[Dict[str, Any]] | None = None
    raw_input = payload.get("input")
    if isinstance(raw_input, list):
        # If this looks like a list of content parts, wrap it; else assume it's a list of items
        if raw_input and all(isinstance(x, dict) and isinstance(x.get("type"), str) for x in raw_input):
            input_items = [
                {"role": "user", "content": raw_input},
            ]
        else:
            try:
                input_items = [x for x in raw_input if isinstance(x, dict)]
            except Exception:
                input_items = None
    elif isinstance(raw_input, str):
        input_items = [
            {"role": "user", "content": [{"type": "input_text", "text": raw_input}]}
        ]
    elif isinstance(raw_input, dict):
        # Single item provided as object
        if isinstance(raw_input.get("role"), str) and isinstance(raw_input.get("content"), list):
            input_items = [raw_input]
        elif isinstance(raw_input.get("content"), list):
            input_items = [{"role": "user", "content": raw_input.get("content") or []}]
    
    # Convert input_items to ensure compatibility with upstream API
    # Some clients send "message" type which needs to be converted to proper format
    if isinstance(input_items, list):
        converted_items = []
        had_conversions = False
        
        for item in input_items:
            if not isinstance(item, dict):
                continue
            
            converted_item = dict(item)
            content = converted_item.get("content")
            
            # Convert content if needed
            if isinstance(content, list):
                new_content = []
                for part in content:
                    if not isinstance(part, dict):
                        new_content.append(part)
                        continue
                    
                    part_copy = dict(part)
                    part_type = part_copy.get("type")
                    
                    # Convert "message" type to "input_text" for compatibility
                    if part_type == "message":
                        import sys
                        print(f"[COMPATIBILITY] Converting 'message' type to 'input_text' for client: {request.headers.get('User-Agent', 'unknown')}", file=sys.stderr)
                        had_conversions = True
                        part_copy["type"] = "input_text"
                        part_copy.pop("role", None)
                        raw_content = part_copy.pop("content", None)
                        text_fragments: list[str] = []
                        if isinstance(raw_content, list):
                            for segment in raw_content:
                                if isinstance(segment, dict):
                                    text_val = segment.get("text")
                                    if isinstance(text_val, str) and text_val.strip():
                                        text_fragments.append(text_val)
                                    else:
                                        alt = segment.get("content")
                                        if isinstance(alt, str) and alt.strip():
                                            text_fragments.append(alt)
                                elif isinstance(segment, str) and segment.strip():
                                    text_fragments.append(segment)
                        elif isinstance(raw_content, str):
                            if raw_content.strip():
                                text_fragments.append(raw_content)
                        elif raw_content is not None:
                            try:
                                import json as _json
                                text_fragments.append(_json.dumps(raw_content, ensure_ascii=False))
                            except Exception:
                                pass
                        part_copy["text"] = "\n".join(fragment for fragment in text_fragments if fragment)
                        if not part_copy["text"]:
                            part_copy["text"] = ""
                    
                    new_content.append(part_copy)
                
                converted_item["content"] = [p for p in new_content if not (isinstance(p, dict) and p.get("type") == "input_text" and not p.get("text"))]
            
            converted_items.append(converted_item)
        
        if had_conversions:
            try:
                _log_event("compatibility_conversion", from_type="message", to_type="input_text", user_agent=request.headers.get("User-Agent"))
            except Exception:
                pass
        
        input_items = converted_items
    
    # Sanitize input to remove upstream 'rs_*' references in structural fields
    try:
        rs_ids = _collect_ids_with_rs_prefix(raw_input)
        if rs_ids:
            try:
                _log_event("client_input_refs_sanitized", count=len(rs_ids))
            except Exception:
                pass
            if isinstance(raw_input, list):
                try:
                    raw_input = _sanitize_input_remove_upstream_refs(raw_input)
                except Exception:
                    pass
    except Exception:
        pass

    if input_items is None:
        messages = payload.get("messages")
        if messages is None and isinstance(payload.get("prompt"), str):
            messages = [{"role": "user", "content": payload.get("prompt") or ""}]
        if isinstance(messages, list):
            input_items = convert_chat_messages_to_responses_input(messages)
    if not isinstance(input_items, list) or not input_items:
        return jsonify({"error": {"message": "Request must include non-empty 'input' (or 'messages'/'prompt')"}}), 400

    # Final safety: sanitize constructed input_items to remove any upstream rs_* references
    try:
        before_n = len(input_items)
        input_items = _sanitize_input_remove_upstream_refs(input_items)
        after_n = len(input_items)
        if after_n < before_n:
            try:
                _log_event("input_items_sanitized", removed=(before_n - after_n))
            except Exception:
                pass
    except Exception:
        pass

    # Log if any residual structural rs_* refs remain (should be zero)
    try:
        residual = _collect_ids_with_rs_prefix(input_items)
        if residual:
            _log_event("pre_upstream_refs_count", count=len(residual))
    except Exception:
        pass

    # previous_response_id threading (simulate context locally when available)
    prev_id = payload.get("previous_response_id")
    if isinstance(prev_id, str) and prev_id.strip():
        prior = _get_thread(prev_id.strip())
        if isinstance(prior, list) and prior:
            try:
                input_items = prior + input_items
            except Exception:
                pass

    # Tools and tool choice
    tools_responses: List[Dict[str, Any]] = []
    _tools = payload.get("tools")
    if isinstance(_tools, list):
        for t in _tools:
            if not isinstance(t, dict):
                continue
            # Chat-style tool: { type: "function", function: { name, parameters, ... } }
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                tools_responses.extend(convert_tools_chat_to_responses([t]))
            else:
                # Already Responses-style tool or built-in tool; pass through
                if isinstance(t.get("type"), str):
                    tools_responses.append(t)
    tool_choice = payload.get("tool_choice", "auto")
    parallel_tool_calls = bool(payload.get("parallel_tool_calls", False))

    # Passthrough Responses API tools (web_search) via extension fields, mirroring other routes
    extra_tools: List[Dict[str, Any]] = []
    rt_payload = payload.get("responses_tools") if isinstance(payload.get("responses_tools"), list) else []
    had_responses_tools = False
    if isinstance(rt_payload, list):
        for _t in rt_payload:
            if not (isinstance(_t, dict) and isinstance(_t.get("type"), str)):
                continue
            if _t.get("type") not in ("web_search", "web_search_preview"):
                return jsonify({"error": {"message": "Only web_search/web_search_preview are supported in responses_tools"}}), 400
            extra_tools.append(_t)
        if not extra_tools and bool(current_app.config.get("DEFAULT_WEB_SEARCH")):
            rtc = payload.get("responses_tool_choice")
            if not (isinstance(rtc, str) and rtc == "none"):
                extra_tools = [{"type": "web_search"}]
        if extra_tools:
            import json as _json
            MAX_TOOLS_BYTES = 32768
            try:
                size = len(_json.dumps(extra_tools))
            except Exception:
                size = 0
            if size > MAX_TOOLS_BYTES:
                return jsonify({"error": {"message": "responses_tools too large", "code": "RESPONSES_TOOLS_TOO_LARGE"}}), 400
            had_responses_tools = True
            tools_responses = (tools_responses or []) + extra_tools

    rtc = payload.get("responses_tool_choice")
    if isinstance(rtc, str) and rtc in ("auto", "none"):
        tool_choice = rtc

    # Instructions & reasoning
    # Flag to disable base-instructions injection for /v1/responses
    no_base = bool(current_app.config.get("RESPONSES_NO_BASE_INSTRUCTIONS"))
    base_inst = _instructions_for_model(model)
    user_inst = payload.get("instructions") if isinstance(payload.get("instructions"), str) else None
    if no_base:
        # Forward client 'instructions' as-is; if missing, inject a minimal stub
        if isinstance(user_inst, str) and user_inst.strip():
            instructions = user_inst
        else:
            instructions = "You are a helpful assistant."
    else:
        # Default behavior: send base instructions; move client 'instructions' into input
        instructions = base_inst
        if isinstance(user_inst, str) and user_inst.strip():
            lead_item = {"role": "user", "content": [{"type": "input_text", "text": user_inst}]}
            input_items = [lead_item] + (input_items or [])

    instructions_used = instructions if isinstance(instructions, str) else None
    active_prompt_type = prompt_key if (not no_base and isinstance(instructions, str)) else None

    model_reasoning = extract_reasoning_from_model_name(requested_model)
    reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else model_reasoning
    reasoning_param = build_reasoning_param(reasoning_effort, reasoning_summary, reasoning_overrides)

    # Pass-through of additional Responses API fields
    # Note: we intentionally DO NOT forward "store" upstream.
    # The ChatGPT codex/responses upstream rejects it (400 "Store must be set to false").
    # We still honor client "store" locally for non-stream aggregation and GET retrieval.
    passthrough_keys = [
        "temperature",
        "top_p",
        # tokens params are not forwarded to ChatGPT codex/responses; it rejects both
        "seed",
        "stop",
        "text",
        "metadata",
        "include",
        "top_logprobs",
        "truncation",
    ]
    extra_fields: Dict[str, Any] = {}
    # Strip any tokens params that upstream rejects
    if "max_output_tokens" in payload and payload.get("max_output_tokens") is not None:
        try:
            _log_event("param_stripped", param="max_output_tokens", reason="unsupported_by_upstream")
        except Exception:
            pass
    if "max_completion_tokens" in payload and payload.get("max_completion_tokens") is not None:
        try:
            _log_event("param_stripped", param="max_completion_tokens", reason="unsupported_by_upstream")
        except Exception:
            pass
    for k in passthrough_keys:
        if k in payload and payload.get(k) is not None:
            extra_fields[k] = payload.get(k)

    # Never forward client "store" to upstream; keep it local-only.
    # Upstream codex/responses requires store=false and 400s otherwise.
    if "store" in payload and payload.get("store") is not None:
        try:
            _log_event("param_stripped", param="store", reason="local_only_not_forwarded")
        except Exception:
            pass

    # Never forward client "previous_response_id" upstream; handle threading locally only.
    if "previous_response_id" in payload and payload.get("previous_response_id") is not None:
        try:
            _log_event("param_stripped", param="previous_response_id", reason="local_thread_only")
        except Exception:
            pass

    upstream, error_resp = start_upstream_request(
        model,
        input_items,
        instructions=instructions,
        tools=tools_responses,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        reasoning_param=reasoning_param,
        extra_fields=extra_fields,
    )
    if error_resp is not None:
        return error_resp

    record_rate_limits_from_response(upstream)

    if upstream.status_code >= 400:
        _log_event(
            "upstream_error",
            status=upstream.status_code,
            model=model,
            include=extra_fields.get("include"),
        )
        try:
            err_body = json.loads(upstream.content.decode("utf-8", errors="ignore")) if upstream.content else {"raw": upstream.text}
        except Exception:
            err_body = {"raw": upstream.text}
        try:
            _log_event("upstream_error_body", status=upstream.status_code, body=(err_body if isinstance(err_body, dict) else {"raw": str(err_body)}) )
        except Exception:
            pass
        error_dict = err_body.get("error") if isinstance(err_body, dict) else None
        message = None
        code = None
        err_type = None
        if isinstance(error_dict, dict):
            message = error_dict.get("message")
            code = error_dict.get("code")
            err_type = error_dict.get("type")
        if not message and isinstance(err_body, dict):
            message = err_body.get("detail") or err_body.get("message")
        message = message or "Upstream error"
        payload_error = {"message": message}
        if code:
            payload_error["code"] = code
        if err_type:
            payload_error["type"] = err_type
        if isinstance(err_body, dict) and isinstance(err_body.get("detail"), str) and err_body.get("detail") != message:
            payload_error["detail"] = err_body.get("detail")
        raw_txt = err_body.get("raw") if isinstance(err_body, dict) else None
        if isinstance(raw_txt, str):
            payload_error["raw"] = raw_txt
        try:
            import sys
            print(
                f"[UPSTREAM_ERROR] route=/v1/responses status={upstream.status_code} message={message}",
                file=sys.stderr,
            )
        except Exception:
            pass
        failure_hint = payload_error.get("message") or payload_error.get("detail")
        if (
            active_prompt_type
            and instructions_used
            and isinstance(failure_hint, str)
            and "instruction" in failure_hint.lower()
        ):
            mark_prompt_invalid(active_prompt_type, instructions_used, failure_hint)
        return jsonify({"error": payload_error}), upstream.status_code

    if stream_req:
        def _passthrough():
            try:
                for chunk in upstream.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    yield chunk
            except (ChunkedEncodingError, ProtocolError, ConnectionError, ReadTimeout) as e:
                try:
                    _log_event("stream_truncated", reason=str(e))
                except Exception:
                    pass
                return
            except Exception as e:
                try:
                    _log_event("stream_error", error=str(e))
                except Exception:
                    pass
                return
            finally:
                try:
                    upstream.close()
                except Exception:
                    pass

        resp = Response(
            stream_with_context(_passthrough()),
            status=upstream.status_code,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        _log_event("stream_start", upstream_status=upstream.status_code, model=model)
        return resp

    # Non-stream aggregation: build a Responses object
    created = int(time.time())
    response_id = "resp_nonstream"
    usage_obj: Dict[str, int] | None = None
    full_text = ""
    output_items: List[Dict[str, Any]] = []

    def _extract_usage(evt: Dict[str, Any]) -> Dict[str, int] | None:
        try:
            usage = (evt.get("response") or {}).get("usage")
            if not isinstance(usage, dict):
                return None
            pt = int(usage.get("input_tokens") or 0)
            ct = int(usage.get("output_tokens") or 0)
            tt = int(usage.get("total_tokens") or (pt + ct))
            return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
        except Exception:
            return None

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
            mu = _extract_usage(evt)
            if mu:
                usage_obj = mu
            kind = evt.get("type")
            if kind == "response.output_text.delta":
                full_text += evt.get("delta") or ""
            elif kind == "response.output_item.done":
                item = evt.get("item") or {}
                if isinstance(item, dict) and item.get("type") in ("function_call", "web_search_call"):
                    output_items.append(item)
            elif kind == "response.failed":
                err_msg = evt.get("response", {}).get("error", {}).get("message", "response.failed")
                return jsonify({"error": {"message": err_msg}}), 502
            elif kind == "response.completed":
                break
    finally:
        try:
            upstream.close()
        except Exception:
            pass

    output: List[Dict[str, Any]] = []
    if full_text:
        output.append({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_text}],
        })
    if output_items:
        output.extend(output_items)

    resp_obj: Dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "model": requested_model or model,
        "output": output,
    }
    if usage_obj:
        # Responses usage schema uses prompt/completion/total; we attach as-is
        resp_obj["usage"] = usage_obj

    # Persist if client asked to store
    try:
        should_store = bool(payload.get("store"))
        if should_store:
            _store_response(resp_obj, max_items=int(current_app.config.get("STORE_MAX", 200)))
    except Exception:
        pass

    # Build a simple next-turn thread input for previous_response_id simulation
    try:
        # Start from the original request input (which may have been augmented by prev threading)
        thread_items = list(input_items) if isinstance(input_items, list) else []
        if full_text:
            thread_items.append({
                "role": "assistant",
                "content": [{"type": "output_text", "text": full_text}],
            })
        _set_thread(response_id, thread_items)
    except Exception:
        pass

    resp = make_response(jsonify(resp_obj), 200)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    try:
        _log_event(
            "nonstream_aggregated",
            id=response_id,
            model=(requested_model or model),
            output_text_len=len(full_text),
            output_items=len(output_items),
            usage=usage_obj,
        )
    except Exception:
        pass
    return resp


@responses_bp.route("/v1/responses/<rid>", methods=["GET"])
def responses_get(rid: str) -> Response:
    obj = _get_response(rid)
    if not obj:
        return jsonify({"error": {"message": "Not found"}}), 404
    resp = make_response(jsonify(obj), 200)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    _log_event("get_response", id=rid, found=True)
    return resp
