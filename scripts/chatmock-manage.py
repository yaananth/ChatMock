#!/usr/bin/env python3
"""
ChatMock CLI management script for server operations.
Handles start, stop, restart, status with full error handling and logging.
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ANSI colors - using cyan instead of blue for better readability
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;36m'  # Cyan - much more readable than dark blue
NC = '\033[0m'  # No Color

# Paths
CHATMOCK_DIR = Path.home() / "dev" / "yaananth" / "ChatMock"
PID_FILE = CHATMOCK_DIR / "chatmock.pid"
LOG_FILE = CHATMOCK_DIR / "chatmock.log"


def colored(text: str, color: str) -> str:
    """Wrap text in color codes."""
    return f"{color}{text}{NC}"


def get_pid() -> Optional[int]:
    """Get PID from PID file if it exists and process is running."""
    if not PID_FILE.exists():
        return None
    
    try:
        pid = int(PID_FILE.read_text().strip())
        # Check if process exists
        os.kill(pid, 0)  # Signal 0 just checks if process exists
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        # PID file is stale or process doesn't exist
        if PID_FILE.exists():
            PID_FILE.unlink()
        return None


def is_port_in_use(port: int = 8000) -> bool:
    """Check if port is in use."""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) == 0
    except Exception:
        return False


def get_orphaned_pids() -> list[int]:
    """Find orphaned gunicorn processes."""
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'gunicorn.*wsgi:app'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return [int(pid) for pid in result.stdout.strip().split('\n') if pid]
    except Exception:
        pass
    return []


def stop_server(force: bool = False) -> bool:
    """Stop the server gracefully or forcefully."""
    pid = get_pid()
    
    if pid:
        print(colored(f"Stopping ChatMock (PID: {pid})...", YELLOW))
        try:
            # Try graceful shutdown first
            os.kill(pid, signal.SIGTERM)
            
            # Wait for graceful shutdown
            for _ in range(10):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
            else:
                # Still running, force kill
                if force:
                    print(colored("Forcing shutdown...", YELLOW))
                    os.kill(pid, signal.SIGKILL)
            
            if PID_FILE.exists():
                PID_FILE.unlink()
            
            print(colored("✓ ChatMock server stopped", GREEN))
            return True
            
        except ProcessLookupError:
            if PID_FILE.exists():
                PID_FILE.unlink()
            print(colored("Process not running, cleaned up PID file", YELLOW))
            return True
        except Exception as e:
            print(colored(f"✗ Error stopping server: {e}", RED))
            return False
    
    # Check for orphaned processes
    orphaned = get_orphaned_pids()
    if orphaned:
        print(colored(f"Found orphaned processes: {orphaned}", YELLOW))
        for opid in orphaned:
            try:
                os.kill(opid, signal.SIGTERM)
                time.sleep(0.5)
                try:
                    os.kill(opid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except Exception:
                pass
        print(colored("✓ Cleaned up orphaned processes", GREEN))
        return True
    
    print(colored("Server is not running", YELLOW))
    return True


def start_server(verbose: bool = False) -> bool:
    """Start the server."""
    # Check if already running
    pid = get_pid()
    if pid:
        print(colored(f"ChatMock already running (PID: {pid})", YELLOW))
        return False
    
    # Clean up orphaned processes
    orphaned = get_orphaned_pids()
    if orphaned:
        print(colored("Cleaning up orphaned processes...", YELLOW))
        stop_server(force=True)
        time.sleep(2)
    
    # Change to ChatMock directory
    os.chdir(CHATMOCK_DIR)
    
    # Set environment variables
    env = os.environ.copy()
    env['CHATMOCK_VERBOSE'] = '1' if verbose else '0'
    env['CHATMOCK_EXPOSE_REASONING'] = '1'
    env['CHATMOCK_ENABLE_WEB_SEARCH'] = '0'  # Disabled by default
    env['CHATMOCK_ENABLE_RESPONSES_API'] = '1'
    
    print(colored("Starting ChatMock with high-performance configuration...", BLUE))
    
    # Get CPU count
    try:
        import multiprocessing
        cpu_count = multiprocessing.cpu_count()
        workers = cpu_count * 2 + 1
    except Exception:
        workers = 5
    
    print(colored(f"✓ Async workers: gevent", GREEN))
    print(colored(f"✓ Workers: {workers}", GREEN))
    print(colored(f"✓ Features: reasoning, web search, responses API", GREEN))
    print()
    
    # Start gunicorn
    try:
        # Redirect output to log file
        with open(LOG_FILE, 'w') as log:
            process = subprocess.Popen(
                ['gunicorn', '-c', 'gunicorn_config.py', 'wsgi:app'],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=CHATMOCK_DIR
            )
        
        # Write PID file
        PID_FILE.write_text(str(process.pid))
        
        # Wait a bit and verify it started
        time.sleep(2)
        
        if get_pid() == process.pid:
            print(colored("✓ ChatMock server started successfully", GREEN))
            print(colored(f"PID: {process.pid}", BLUE))
            print(colored(f"Port: 8000", BLUE))
            print(colored(f"Logs: {LOG_FILE}", BLUE))
            print()
            print(f"View logs: {colored('chatmock-logs', GREEN)}")
            print(f"Check status: {colored('chatmock-status', BLUE)}")
            print(f"Stop server: {colored('chatmock-stop', YELLOW)}")
            return True
        else:
            print(colored("✗ Failed to start ChatMock", RED))
            print(colored(f"Check logs: tail -f {LOG_FILE}", YELLOW))
            return False
            
    except FileNotFoundError:
        print(colored("✗ gunicorn not found. Install with: pip install gunicorn gevent", RED))
        return False
    except Exception as e:
        print(colored(f"✗ Error starting server: {e}", RED))
        return False


def show_status() -> None:
    """Show server status."""
    print(colored("=== ChatMock Server Status ===", BLUE))
    print()
    
    pid = get_pid()
    
    if pid:
        print(colored("✓ Server is running", GREEN))
        print(colored(f"PID: {pid}", BLUE))
        print(colored("Port: 8000", BLUE))
        
        # Get uptime
        try:
            result = subprocess.run(
                ['ps', '-o', 'etime=', '-p', str(pid)],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                uptime = result.stdout.strip()
                print(colored(f"Uptime: {uptime}", BLUE))
        except Exception:
            pass
        
        # Show workers
        print()
        print(colored("Workers:", BLUE))
        orphaned = get_orphaned_pids()
        if orphaned:
            for wpid in orphaned:
                print(f"  • PID {wpid}")
        
        print()
        print(colored("Test: curl http://localhost:8000/health", BLUE))
        
    else:
        orphaned = get_orphaned_pids()
        if orphaned:
            print(colored("⚠ Server running without PID file (orphaned)", YELLOW))
            print(colored("Run 'chatmock-stop' to clean up", YELLOW))
        else:
            print(colored("✗ Server is not running", YELLOW))
            print(colored("Run 'chatmock-start' to start", BLUE))


def tail_logs() -> None:
    """Tail ALL server logs (access + error combined)."""
    import sys
    
    # Get all log files
    access_log = LOG_FILE.parent / "chatmock.access.log"
    error_log = LOG_FILE.parent / "chatmock.error.log"
    
    logs_to_tail = []
    
    if access_log.exists():
        logs_to_tail.append(str(access_log))
    if error_log.exists():
        logs_to_tail.append(str(error_log))
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 0:
        logs_to_tail.append(str(LOG_FILE))
    
    if not logs_to_tail:
        print(colored("No log files found", YELLOW))
        print(colored(f"Expected locations:", BLUE))
        print(f"  • {LOG_FILE}")
        print(f"  • {access_log}")
        print(f"  • {error_log}")
        return
    
    print(colored("=== Tailing ALL ChatMock Logs ===", BLUE))
    print(colored(f"Watching {len(logs_to_tail)} log file(s):", BLUE))
    for log in logs_to_tail:
        print(f"  • {log}")
    print(colored("Press Ctrl+C to stop", YELLOW))
    print()
    
    try:
        # Try to use tail with multiple files (shows file headers)
        # Use -F to follow even if files are rotated
        cmd = ['tail', '-F'] + logs_to_tail
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print()
        print(colored("Stopped tailing logs", BLUE))


def main():
    parser = argparse.ArgumentParser(description="ChatMock Server Management")
    parser.add_argument('action', choices=['start', 'stop', 'restart', 'status', 'logs'],
                        help='Action to perform')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose logging')
    parser.add_argument('-f', '--force', action='store_true',
                        help='Force stop (SIGKILL)')
    
    args = parser.parse_args()
    
    if args.action == 'start':
        success = start_server(verbose=args.verbose)
        sys.exit(0 if success else 1)
    
    elif args.action == 'stop':
        success = stop_server(force=args.force)
        sys.exit(0 if success else 1)
    
    elif args.action == 'restart':
        print(colored("Restarting ChatMock...", BLUE))
        stop_server(force=args.force)
        time.sleep(2)
        success = start_server(verbose=args.verbose)
        sys.exit(0 if success else 1)
    
    elif args.action == 'status':
        show_status()
        sys.exit(0)
    
    elif args.action == 'logs':
        tail_logs()
        sys.exit(0)


if __name__ == '__main__':
    main()
