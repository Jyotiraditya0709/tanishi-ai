"""
Canvas emission tools.

Lets Tanishi intentionally emit a visual frame instead of relying only on
inline <canvas> tags in prose.
"""

from tanishi.tools.registry import ToolDefinition


async def emit_canvas(kind: str, payload: str) -> dict:
    k = (kind or "").strip().lower()
    if k not in {"mermaid", "chart", "html"}:
        raise ValueError("kind must be one of: mermaid, chart, html")
    p = (payload or "").strip()
    if not p:
        raise ValueError("payload must not be empty")
    return {"type": "canvas", "kind": k, "payload": p}


def get_canvas_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="emit_canvas",
            description=(
                "Emit a structured canvas frame to render a visual inline in chat. "
                "Use for mermaid diagrams, Chart.js config visuals, or sandboxed html widgets."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["mermaid", "chart", "html"],
                        "description": "Canvas renderer type.",
                    },
                    "payload": {
                        "type": "string",
                        "description": "Canvas content (diagram, chart config JSON, or html).",
                    },
                },
                "required": ["kind", "payload"],
            },
            handler=emit_canvas,
            category="code",
            risk_level="low",
        ),
    ]
