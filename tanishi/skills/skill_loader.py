"""
File-based community skill loader.

Loads tools from tanishi/skills/<skill_name>/{skill.json,handler.py}
and registers them into the existing ToolRegistry.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from tanishi.tools.registry import ToolDefinition, ToolRegistry


VALID_RISK_LEVELS = {"low", "medium", "high"}
VALID_CATEGORIES = {
    "productivity",
    "utility",
    "search",
    "filesystem",
    "system",
    "code",
    "communication",
}


class SkillLoader:
    def _validate_manifest(self, manifest: dict[str, Any]) -> str | None:
        required = {
            "name",
            "version",
            "description",
            "author",
            "category",
            "risk_level",
            "requires_approval",
            "input_schema",
            "enabled",
        }
        missing = [k for k in required if k not in manifest]
        if missing:
            return f"missing required field(s): {', '.join(missing)}"
        name = manifest.get("name")
        if not isinstance(name, str) or not name:
            return "name must be a non-empty string"
        if manifest.get("risk_level") not in VALID_RISK_LEVELS:
            return f"risk_level must be one of {sorted(VALID_RISK_LEVELS)}"
        if manifest.get("category") not in VALID_CATEGORIES:
            return f"category must be one of {sorted(VALID_CATEGORIES)}"
        if not isinstance(manifest.get("input_schema"), dict):
            return "input_schema must be a JSON object"
        if not isinstance(manifest.get("requires_approval"), bool):
            return "requires_approval must be boolean"
        if not isinstance(manifest.get("enabled"), bool):
            return "enabled must be boolean"
        return None

    def _load_handler(self, handler_path: Path, fn_name: str):
        mod_name = f"skill_{handler_path.parent.name}_handler"
        spec = importlib.util.spec_from_file_location(mod_name, handler_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to create import spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handler = getattr(module, fn_name, None)
        if handler is None:
            raise RuntimeError(f"handler.py missing async function '{fn_name}'")
        if not callable(handler):
            raise RuntimeError(f"'{fn_name}' is not callable")
        return handler

    def load_all(self, skills_dir: str | Path, registry: ToolRegistry) -> tuple[list[str], list[str]]:
        skills_path = Path(skills_dir)
        loaded: list[str] = []
        skipped: list[str] = []
        if not skills_path.exists():
            return loaded, [f"{skills_path}: not found"]

        for item in sorted(skills_path.iterdir()):
            if not item.is_dir():
                continue
            manifest_path = item / "skill.json"
            handler_path = item / "handler.py"
            if not manifest_path.exists() or not handler_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise RuntimeError("manifest root must be object")
                err = self._validate_manifest(manifest)
                if err:
                    reason = f"{manifest.get('name', item.name)}: {err}"
                    print(f"[skills] skipped {manifest.get('name', item.name)}: {err}")
                    skipped.append(reason)
                    continue
                if not manifest.get("enabled", True):
                    reason = f"{manifest['name']}: disabled"
                    print(f"[skills] skipped {manifest['name']}: disabled")
                    skipped.append(reason)
                    continue

                handler = self._load_handler(handler_path, manifest["name"])
                tool = ToolDefinition(
                    name=manifest["name"],
                    description=manifest["description"],
                    input_schema=manifest["input_schema"],
                    handler=handler,
                    requires_approval=manifest["requires_approval"],
                    category=manifest["category"],
                    risk_level=manifest["risk_level"],
                )
                registry.register(tool)
                loaded.append(manifest["name"])
                print(f"[skills] loaded {manifest['name']} v{manifest['version']}")
            except Exception as e:
                name = item.name
                print(f"[skills] skipped {name}: {e}")
                skipped.append(f"{name}: {e}")

        return loaded, skipped


def _validate_one(path: Path) -> int:
    manifest_path = path / "skill.json"
    handler_path = path / "handler.py"
    if not manifest_path.exists() or not handler_path.exists():
        print("invalid skill folder: missing skill.json or handler.py")
        return 1
    reg = ToolRegistry()
    loaded, skipped = SkillLoader().load_all(path.parent, reg)
    skill_name = path.name
    if any(x.startswith(f"{skill_name}:") for x in skipped):
        return 1
    if skill_name not in loaded:
        return 1
    print("ok")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Skill loader utility")
    parser.add_argument("--validate", type=str, default="", help="validate a single skill folder")
    args = parser.parse_args()
    if args.validate:
        raise SystemExit(_validate_one(Path(args.validate)))
    print("Use --validate <path-to-skill-folder>")


if __name__ == "__main__":
    main()
