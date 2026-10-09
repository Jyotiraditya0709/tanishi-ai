# CS1 · migration .sql files are not in an installed wheel

`tanishi/core_state/migrations/` holds `.sql` files only (no `__init__.py`), and `pyproject.toml` has no
`[tool.setuptools.package-data]` entry. A checkout or an editable install works. A built wheel would ship no migrations,
and `migrate()` would then return 0 and create nothing.

Fix (not done in CS1 because `pyproject.toml` is outside the node's file list):

```toml
[tool.setuptools.package-data]
"tanishi.core_state" = ["migrations/*.sql"]
```
