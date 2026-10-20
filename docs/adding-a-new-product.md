# Adding a new product

1. `mkdir products/<kebab-name>` with: `README.md`, `.env.example`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `config/`, `src/<package>/`, `tests/`, `docs/`, `.gitignore`.
2. **Self-contained**: no imports from other products. Copy shared helpers rather than coupling products (users should be able to copy one folder).
3. **Structure (object-oriented)**: `config.py` (env + YAML), `models.py`, `clients/` (one class per external system behind an interface, with an in-memory fake for tests), `stages/` (one class per pipeline step), `bootstrap.py` (a builder class that lists the stages + runtime factories), `cli.py` (one class per command). No loose module-level logic functions.
4. **Offline mode**: provide a `demo` command and synthetic sample data so users can try it without an Esri account.
5. **Diagrams**: generate an interactive workflow page from the real stage list (see `workflow.py` / `flowchart.py` in `gas-electric-leak-response`: nodes come from the code, scenarios are validated against it), record a small GIF for the README, and add a `--check` to CI.
5b. **Templates**: reports and e-mails are Jinja2 files in a downloadable `templates/` folder with a `preview` command; demo output uses neutral placeholders, never invented imagery.
6. **README must contain**: scenario, requirements table (what needs an Esri account), quick start, generated flow chart, outputs, verification status table (tested / not tested), safety or accuracy caveats.
7. **Fail loud**: validate configuration at start-up and list *all* missing values; never silently drop work.
8. Add the product to the table in the root README and, if it needs new CI steps, to `.github/workflows/ci.yml`.
