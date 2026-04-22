"""
Tanishi File System Tools — Hands on your files.

Read, write, list, and search files on the user's machine.
Safety-first: dangerous operations require approval.
"""

import os
import json
from pathlib import Path
from datetime import datetime

from tanishi.tools.registry import ToolDefinition


async def read_file(path: str) -> str:
    """Read the contents of a file."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"File not found: {path}"
        if not p.is_file():
            return f"Not a file: {path}"
        if p.stat().st_size > 1_000_000:  # 1MB limit
            return f"File too large ({p.stat().st_size} bytes). I read fast, but not THAT fast."

        content = p.read_text(encoding="utf-8", errors="replace")
        return f"Contents of {path} ({len(content)} chars):\n\n{content}"
    except PermissionError:
        return f"Permission denied: {path}. Even I have limits."
    except Exception as e:
        return f"Error reading {path}: {str(e)}"


async def write_file(path: str, content: str, mode: str = "write") -> str:
    """Write content to a file. Mode: 'write' (overwrite) or 'append'."""
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append":
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
            return f"Appended {len(content)} chars to {path}"
        else:
            p.write_text(content, encoding="utf-8")
            return f"Written {len(content)} chars to {path}"

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error writing {path}: {str(e)}"


async def list_directory(path: str = ".", pattern: str = "*", max_items: int = 50) -> str:
    """List files and directories at the given path."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Directory not found: {path}"
        if not p.is_dir():
            return f"Not a directory: {path}"

        items = []
        for item in sorted(p.glob(pattern))[:max_items]:
            stat = item.stat()
            item_type = "DIR " if item.is_dir() else "FILE"
            size = f"{stat.st_size:>10,} B" if item.is_file() else "           "
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            items.append(f"  {item_type}  {size}  {modified}  {item.name}")

        if not items:
            return f"Empty directory or no matches for '{pattern}' in {path}"

        header = f"Contents of {p} ({len(items)} items):\n"
        return header + "\n".join(items)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error listing {path}: {str(e)}"


async def search_files(directory: str = ".", pattern: str = "*.py", text: str = "", max_results: int = 20) -> str:
    """Search for files by name pattern and optionally grep for text content."""
    try:
        p = Path(directory).expanduser().resolve()
        results = []

        for filepath in p.rglob(pattern):
            if len(results) >= max_results:
                break

            # Skip hidden dirs and common ignore patterns
            parts = filepath.parts
            if any(part.startswith(".") or part in ("node_modules", "__pycache__", ".venv", "venv") for part in parts):
                continue

            if text:
                try:
                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                    if text.lower() in content.lower():
                        # Find line number
                        for i, line in enumerate(content.split("\n"), 1):
                            if text.lower() in line.lower():
                                results.append(f"  {filepath.relative_to(p)}:{i}  {line.strip()[:100]}")
                                break
                except Exception:
                    continue
            else:
                results.append(f"  {filepath.relative_to(p)}")

        if not results:
            return f"No files matching '{pattern}'" + (f" containing '{text}'" if text else "") + f" in {directory}"

        return f"Found {len(results)} results:\n" + "\n".join(results)

    except Exception as e:
        return f"Search error: {str(e)}"


def get_filesystem_tools() -> list[ToolDefinition]:
    """Return filesystem tool definitions."""
    return [
        ToolDefinition(
            name="read_file",
            description="Read the contents of a file on the user's computer. Use this to examine code, config files, documents, or any text file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative file path (e.g., './main.py' or 'C:/Users/J/project/file.txt').",
                    },
                },
                "required": ["path"],
            },
            handler=read_file,
            category="filesystem",
            risk_level="low",
        ),
        ToolDefinition(
            name="write_file",
            description="Write or append content to a file. Creates parent directories if needed. Use for creating scripts, configs, notes, or any text file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to write to.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["write", "append"],
                        "description": "'write' to overwrite, 'append' to add to end.",
                        "default": "write",
                    },
                },
                "required": ["path", "content"],
            },
            handler=write_file,
            category="filesystem",
            risk_level="medium",
            requires_approval=True,
        ),
        ToolDefinition(
            name="list_directory",
            description="List files and folders in a directory. Shows type, size, and modification date.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path. Defaults to current directory.",
                        "default": ".",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter (e.g., '*.py', '*.json').",
                        "default": "*",
                    },
                    "max_items": {
                        "type": "integer",
                        "description": "Maximum number of directory entries to return.",
                        "default": 50,
                    },
                },
                "required": [],
            },
            handler=list_directory,
            category="filesystem",
            risk_level="low",
        ),
        ToolDefinition(
            name="search_files",
            description="Search for files by name pattern and optionally search within file contents (like grep). Useful for finding code, configs, or references across a project.",
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Root directory to search from.",
                        "default": ".",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "File name pattern (e.g., '*.py', '*.js', '*.md').",
                        "default": "*.py",
                    },
                    "text": {
                        "type": "string",
                        "description": "Optional text to search for inside matching files.",
                        "default": "",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matches to return.",
                        "default": 20,
                    },
                },
                "required": [],
            },
            handler=search_files,
            category="filesystem",
            risk_level="low",
        ),
    ]
