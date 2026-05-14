# Contributing Skills

Create each skill in `tanishi/skills/<skill_name>/` using `snake_case`.

Required files:
- `skill.json` (manifest)
- `handler.py` (runtime logic)

Required `skill.json` fields:
- `name`, `version`, `description`, `author`
- `category`, `risk_level`, `requires_approval`
- `input_schema`, `enabled`

Handler contract:
- Must expose `async def <skill_name>(**kwargs) -> str`
- `<skill_name>` must exactly match `skill.json["name"]`

Local validation:
```bash
python -m tanishi.skills.skill_loader --validate tanishi/skills/<skill_name>
```

## What makes a good skill description

Lead with the user outcome, not the mechanic.

Bad:
- `count_tokens: counts tokens in text`
- `summarize_clipboard: summarizes your clipboard`

Good:
- `Estimate how many tokens a piece of text will consume before sending it to an API`
- `Instantly condense whatever text you've copied into clean bullet points`

Checklist before PR:
- Manifest is valid JSON and includes all required fields
- Handler function name matches skill name and is async
- `--validate` passes locally
- Description is outcome-first and specific
- No new dependency unless justified in PR notes
