"""
Tanishi System Tools — Control the machine.

Execute shell commands, get system info, manage processes.
High-risk operations require explicit approval.
"""

import os
import sys
import json
import platform
import subprocess
import asyncio
from datetime import datetime
from pathlib import Path

from tanishi.tools.registry import ToolDefinition


async def run_command(command: str, timeout: int = 30) -> str:
    """
    Execute a shell command and return the output.
    Timeout prevents runaway processes.
    """
    try:
        # Use shell=True for Windows compatibility
        is_windows = platform.system() == "Windows"

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            return f"Command timed out after {timeout}s. Either it's stuck or you're mining Bitcoin."

        output = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()

        result_parts = []
        if output:
            result_parts.append(f"Output:\n{output}")
        if errors:
            result_parts.append(f"Stderr:\n{errors}")
        if process.returncode != 0:
            result_parts.append(f"Exit code: {process.returncode}")

        return "\n\n".join(result_parts) if result_parts else "Command completed with no output."

    except Exception as e:
        return f"Command failed: {str(e)}"


async def get_system_info() -> str:
    """Get comprehensive system information."""
    import shutil

    info = {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "hostname": platform.node(),
        "cwd": os.getcwd(),
        "user": os.getenv("USER") or os.getenv("USERNAME", "unknown"),
        "home": str(Path.home()),
    }

    # Disk space
    try:
        usage = shutil.disk_usage(Path.home())
        info["disk_total"] = f"{usage.total / (1024**3):.1f} GB"
        info["disk_free"] = f"{usage.free / (1024**3):.1f} GB"
        info["disk_used_pct"] = f"{(usage.used / usage.total) * 100:.1f}%"
    except Exception:
        pass

    lines = ["System Information:"]
    for key, value in info.items():
        lines.append(f"  {key}: {value}")

    return "\n".join(lines)


async def get_datetime(timezone: str = "local") -> str:
    """Get current date, time, and related info."""
    now = datetime.now()
    info = {
        "date": now.strftime("%A, %B %d, %Y"),
        "time": now.strftime("%I:%M:%S %p"),
        "iso": now.isoformat(),
        "timestamp": int(now.timestamp()),
        "day_of_year": now.strftime("%j"),
        "week_number": now.strftime("%W"),
    }

    lines = ["Current Date & Time:"]
    for key, value in info.items():
        lines.append(f"  {key}: {value}")

    return "\n".join(lines)


async def get_environment_variable(name: str) -> str:
    """Get an environment variable value (redacts sensitive ones)."""
    sensitive_patterns = ["key", "secret", "token", "password", "auth", "credential"]
    value = os.getenv(name, "")

    if not value:
        return f"Environment variable '{name}' is not set."

    # Redact sensitive values
    if any(p in name.lower() for p in sensitive_patterns):
        return f"{name} = {'*' * 8}...{value[-4:]} (redacted for security)"

    return f"{name} = {value}"


def get_system_tools() -> list[ToolDefinition]:
    """Return system tool definitions."""
    return [
        ToolDefinition(
            name="run_command",
            description="Execute a shell command on the user's computer. Use for running scripts, installing packages, git operations, checking processes, or any system task. Returns stdout, stderr, and exit code.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute (e.g., 'git status', 'pip install flask', 'dir' or 'ls').",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum seconds to wait for the command.",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
            handler=run_command,
            category="system",
            risk_level="high",
            requires_approval=True,
        ),
        ToolDefinition(
            name="get_system_info",
            description="Get information about the user's computer: OS, hardware, disk space, Python version, current directory, and more.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=get_system_info,
            category="system",
            risk_level="low",
        ),
        ToolDefinition(
            name="get_datetime",
            description="Get the current date, time, day of week, week number, and timestamp.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=get_datetime,
            category="system",
            risk_level="low",
        ),
    ]
