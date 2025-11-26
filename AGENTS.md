# Repository Guidelines

## Project Structure & Modules
- `src/rfb_cnpj_etl/`: core package; CLI entry in `main.py`, orchestration in `orchestrator.py`, ETL helpers in `cnpj_data/`, DB logic in `db/`, shared utilities in `utils/`.
- `etl.py`: thin wrapper that runs the CLI (`python etl.py ...`).
- `data/`: local workspace for downloads; treat as ephemeral and do not commit.
- `sql/`: auxiliary SQL scripts for indexes, materialized views, and improvements; also contains `query_postgres.md` with query examples.

## Setup, Build, and Run
- Python 3.9+ recommended. Create a venv and install deps: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- List available data months: `python etl.py get-availables`; latest month: `python etl.py get-latest`.
- Download data: `python etl.py download --month MM/AAAA [--workers N --clean --download-dir PATH]`.
- Initialize DB schema: `python etl.py db init [--db-name NAME]`.
- Load data into DB: `python etl.py db load [--month MM/AAAA --download-dir PATH --skip-index --skip-validation --low-memory --parallel]`.
- Full pipeline: `python etl.py complete [--month MM/AAAA ...]` (downloads + load + indexes).
- For flags and defaults, run `python etl.py --help` or check the README.

## Coding Style & Naming
- Python, 4-space indentation, type hints where practical.
- Modules/functions use `snake_case`; configuration constants are `UPPER_SNAKE_CASE` (`config.py`).
- Keep CLI messages concise and reuse `utils.logger.print_log` for consistent output.
- Keep Portuguese naming for user-facing strings/CLI parity with existing code.

## Testing Guidelines
- No automated test suite yet; validate changes by exercising CLI flows on a small month (e.g., `--month 01/2024`) and checking logs.
- Before pushing, confirm downloads land in `data/downloads/YYYY-MM` and loaders create/populate the target DB without errors.
- If adding tests, colocate under a new `tests/` directory and prefer deterministic fixtures over real downloads.

## Commit & PR Guidelines
- Commit messages: short, imperative, and scoped (e.g., `Adjust postgres batch patching`); conventional commits are welcome but not enforced.
- PRs should include: summary of changes, commands run (download/load/index), any config tweaks, and links to related issues.
- Avoid committing artifacts from `data/` or large logs; keep diffs focused on code/docs.

## Security & Configuration Notes
- Update database credentials locally; do not commit real secrets. `config.py` holds defaults—override via environment or local config without pushing credentials.
- The pipeline is I/O and storage heavy (~50GB end-to-end); document any flags you change for low-memory or parallelism so others can reproduce.
