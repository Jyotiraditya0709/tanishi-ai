async def count_tokens(text: str) -> str:
    words = len((text or "").split())
    estimate = int(words * 1.3)
    return (
        f"Approx token estimate: {estimate}\n"
        f"Words: {words}\n"
        "Heuristic: len(text.split()) * 1.3"
    )
