"""
Health check and monitoring utilities for ChatMock.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, Any
from flask import jsonify, Response

# Global metrics
_metrics = {
    'start_time': time.time(),
    'requests_total': 0,
    'requests_success': 0,
    'requests_error': 0,
    'last_request_time': None,
}


def increment_request():
    """Increment request counter."""
    _metrics['requests_total'] += 1
    _metrics['last_request_time'] = datetime.now().isoformat()


def increment_success():
    """Increment success counter."""
    _metrics['requests_success'] += 1


def increment_error():
    """Increment error counter."""
    _metrics['requests_error'] += 1


def get_metrics() -> Dict[str, Any]:
    """Get current metrics."""
    uptime = time.time() - _metrics['start_time']
    
    return {
        'uptime_seconds': int(uptime),
        'uptime_human': format_uptime(uptime),
        'requests': {
            'total': _metrics['requests_total'],
            'success': _metrics['requests_success'],
            'error': _metrics['requests_error'],
            'success_rate': (
                _metrics['requests_success'] / _metrics['requests_total'] * 100
                if _metrics['requests_total'] > 0 else 0
            )
        },
        'last_request': _metrics['last_request_time'],
    }


def format_uptime(seconds: float) -> str:
    """Format uptime in human-readable format."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    
    return " ".join(parts)


def create_health_response() -> Response:
    """Create a health check response with metrics."""
    try:
        from .prompts import get_cache_info
        prompt_cache = get_cache_info()
    except Exception:
        prompt_cache = {'error': 'Failed to load prompt cache info'}
    
    health_data = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'metrics': get_metrics(),
        'prompt_cache': prompt_cache,
    }
    
    return jsonify(health_data)
