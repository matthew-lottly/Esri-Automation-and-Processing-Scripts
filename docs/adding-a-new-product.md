# Adding a new product

1. `mkdir products/<kebab-name>` with: `README.md`, `.env.example`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `config/`, `src/<package>/`, `tests/`, `docs/`, `.gitignore`.
2. **Self-contained**: no imports from other products. Copy shared helpers rather than coupling products (users should be able to copy one folder).
3. **Structure**: `config.py` (env + YAML), `models.py`, `clients/` (one class per external system, behind an interface; an in-memory/fake implementation for tests), `stages/` or `tasks/` (one class per step), `cli.py`.
4. **Offline mode**: provide a `demo` command and synthetic sample data so users can try it without an Esri account.
5. **Flow charts**: generate Mermaid from the code (see `flowchart.py` in `gas-electric-leak-response`) and inject between README markers; add a `--check` to CI.
6. **README must contain**: scenario, requirements table (what needs an Esri account), quick start, generated flow chart, outputs, verification status table (tested / not tested), safety or accuracy caveats.
7. **Fail loud**: validate configuration at start-up and list *all* missing values; never silently drop work.
8. Add the product to the table in the root README and, if it needs new CI steps, to `.github/workflows/ci.yml`.
