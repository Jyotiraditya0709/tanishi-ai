"""
Tanishi Windows Automation — Control your PC.

Open apps, manage clipboard, control windows, monitor processes.
Works on Windows. Partial support for Mac/Linux.
"""

import os
import sys
import platform
import subprocess
import asyncio
from pathlib import Path

from tanishi.tools.registry import ToolDefinition


# ============================================================
# App Launcher
# ============================================================

# Common Windows apps and their paths/commands
WINDOWS_APPS = {
    "chrome": "start chrome",
    "google chrome": "start chrome",
    "browser": "start chrome",
    "firefox": "start firefox",
    "edge": "start msedge",
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "explorer": "explorer",
    "file explorer": "explorer",
    "files": "explorer",
    "terminal": "wt",
    "cmd": "cmd",
    "powershell": "powershell",
    "settings": "start ms-settings:",
    "task manager": "taskmgr",
    "spotify": "start spotify:",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "word": "start winword",
    "excel": "start excel",
    "powerpoint": "start powerpnt",
    "outlook": "start outlook",
    "teams": "start msteams:",
    "discord": "start discord:",
    "steam": "start steam:",
    "paint": "mspaint",
    "snipping tool": "snippingtool",
    "camera": "start microsoft.windows.camera:",
    "clock": "start ms-clock:",
    "maps": "start bingmaps:",
    "store": "start ms-windows-store:",
}


async def open_app(app_name: str) -> str:
    """Open an application by name."""
    app_lower = app_name.lower().strip()

    # Check known apps
    if app_lower in WINDOWS_APPS:
        cmd = WINDOWS_APPS[app_lower]
    else:
        # Try to open it directly (works for apps in PATH)
        cmd = f"start {app_name}" if platform.system() == "Windows" else app_name

    try:
        if platform.system() == "Windows":
            process = await asyncio.create_subprocess_shell(
                cmd, shell=True,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=5)
            return f"Opened {app_name}"
        else:
            process = await asyncio.create_subprocess_shell(
                f"open {app_name}" if platform.system() == "Darwin" else f"xdg-open {app_name}",
            )
            return f"Opened {app_name}"
    except asyncio.TimeoutError:
        return f"Opened {app_name} (still loading)"
    except Exception as e:
        return f"Failed to open {app_name}: {str(e)}"


async def open_url(url: str) -> str:
    """Open a URL in the default browser."""
    import webbrowser
    try:
        webbrowser.open(url)
        return f"Opened {url} in browser"
    except Exception as e:
        return f"Failed to open URL: {str(e)}"


# ============================================================
# Clipboard
# ============================================================

async def get_clipboard() -> str:
    """Read the current clipboard contents."""
    try:
        if platform.system() == "Windows":
            process = await asyncio.create_subprocess_shell(
                'powershell -c "Get-Clipboard"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            content = stdout.decode("utf-8", errors="replace").strip()
            if content:
                return f"Clipboard contents:\n{content[:2000]}"
            return "Clipboard is empty."
        else:
            import subprocess
            content = subprocess.check_output(["xclip", "-selection", "clipboard", "-o"]).decode()
            return f"Clipboard contents:\n{content[:2000]}"
    except Exception as e:
        return f"Can't read clipboard: {str(e)}"


async def set_clipboard(text: str) -> str:
    """Copy text to clipboard."""
    try:
        if platform.system() == "Windows":
            process = await asyncio.create_subprocess_shell(
                f'powershell -c "Set-Clipboard -Value \'{text[:5000]}\'"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            return f"Copied to clipboard ({len(text)} chars)"
        else:
            process = await asyncio.create_subprocess_shell(
                f'echo "{text}" | xclip -selection clipboard',
            )
            await process.communicate()
            return f"Copied to clipboard ({len(text)} chars)"
    except Exception as e:
        return f"Clipboard error: {str(e)}"


# ============================================================
# Process Management
# ============================================================

async def list_processes(filter_name: str = "") -> str:
    """List running processes, optionally filtered by name."""
    try:
        if platform.system() == "Windows":
            cmd = 'powershell -c "Get-Process | Sort-Object -Property CPU -Descending | Select-Object -First 20 Name, Id, CPU, WorkingSet | Format-Table -AutoSize"'
            if filter_name:
                cmd = f'powershell -c "Get-Process | Where-Object {{$_.Name -like \'*{filter_name}*\'}} | Select-Object Name, Id, CPU, WorkingSet | Format-Table -AutoSize"'

            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            return f"Running processes:\n{stdout.decode('utf-8', errors='replace')}"
        else:
            cmd = "ps aux --sort=-%cpu | head -20"
            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            return stdout.decode()
    except Exception as e:
        return f"Process list error: {str(e)}"


async def kill_process(name: str) -> str:
    """Kill a process by name."""
    try:
        if platform.system() == "Windows":
            process = await asyncio.create_subprocess_shell(
                f'taskkill /IM "{name}" /F',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            output = stdout.decode() + stderr.decode()
            return output.strip() or f"Attempted to kill {name}"
        else:
            process = await asyncio.create_subprocess_shell(f'pkill -f "{name}"')
            await process.communicate()
            return f"Killed {name}"
    except Exception as e:
        return f"Kill error: {str(e)}"


# ============================================================
# System Control
# ============================================================

async def control_system(action: str) -> str:
    """System control actions: lock, sleep, volume, brightness."""
    action = action.lower().strip()

    if platform.system() != "Windows":
        return "System control only works on Windows currently."

    commands = {
        "lock": 'rundll32.exe user32.dll,LockWorkStation',
        "sleep": 'rundll32.exe powrprof.dll,SetSuspendState 0,1,0',
        "mute": 'powershell -c "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"',
        "volume_up": 'powershell -c "(New-Object -ComObject WScript.Shell).SendKeys([char]175)"',
        "volume_down": 'powershell -c "(New-Object -ComObject WScript.Shell).SendKeys([char]174)"',
    }

    if action in commands:
        try:
            process = await asyncio.create_subprocess_shell(commands[action])
            await process.communicate()
            return f"Done: {action}"
        except Exception as e:
            return f"Failed: {str(e)}"

    return f"Unknown action: {action}. Available: {', '.join(commands.keys())}"


def get_windows_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="open_app",
            description="Open an application on the user's computer. Knows common apps like Chrome, VS Code, Spotify, Calculator, Terminal, Settings, etc. Use when user says 'open X' or 'launch X'.",
            input_schema={
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name (e.g., 'chrome', 'vscode', 'spotify', 'calculator')."},
                },
                "required": ["app_name"],
            },
            handler=open_app,
            category="automation",
            risk_level="medium",
        ),
        ToolDefinition(
            name="open_url",
            description="Open a URL in the default web browser.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open."},
                },
                "required": ["url"],
            },
            handler=open_url,
            category="automation",
            risk_level="low",
        ),
        ToolDefinition(
            name="get_clipboard",
            description="Read the current clipboard contents. Use when user says 'what did I copy' or 'check my clipboard' or asks about something they copied.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=get_clipboard,
            category="automation",
            risk_level="low",
        ),
        ToolDefinition(
            name="set_clipboard",
            description="Copy text to the clipboard. Use when user asks to copy something or says 'copy this'.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to copy to clipboard."},
                },
                "required": ["text"],
            },
            handler=set_clipboard,
            category="automation",
            risk_level="low",
        ),
        ToolDefinition(
            name="list_processes",
            description="List running processes on the computer, sorted by CPU usage. Can filter by name.",
            input_schema={
                "type": "object",
                "properties": {
                    "filter_name": {"type": "string", "description": "Filter processes by name.", "default": ""},
                },
                "required": [],
            },
            handler=list_processes,
            category="automation",
            risk_level="low",
        ),
        ToolDefinition(
            name="kill_process",
            description="Kill/terminate a running process by name. Use when user asks to close or kill an app.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Process name to kill (e.g., 'chrome.exe')."},
                },
                "required": ["name"],
            },
            handler=kill_process,
            category="automation",
            risk_level="high",
            requires_approval=True,
        ),
        ToolDefinition(
            name="control_system",
            description="System control: lock screen, sleep, mute, volume up/down. Use when user says 'lock my PC', 'mute', 'turn volume up'.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action: lock, sleep, mute, volume_up, volume_down."},
                },
                "required": ["action"],
            },
            handler=control_system,
            category="automation",
            risk_level="medium",
            requires_approval=True,
        ),
    ]
