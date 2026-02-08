# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ETL pipeline that downloads, transforms, and loads Brazilian CNPJ (company registry) data from Receita Federal (RFB) into PostgreSQL. Handles ~200M records across multiple tables. Written in Python with full Docker support.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# CLI entry point
python etl.py <command> [options]

# Key commands
python etl.py get-availables                          # List available months from RFB
python etl.py get-latest                              # Show latest available month
python etl.py download --month MM/AAAA --workers 10   # Download data files
python etl.py db init --db-name dados_cnpj            # Initialize database schema
python etl.py db load --month MM/AAAA --parallel       # Load data into PostgreSQL
python etl.py db patch                                # Apply data fixes
python etl.py db pk / db fk / db index                # Add PKs, FKs, indexes
python etl.py db views create                         # Create materialized views
python etl.py db views refresh --concurrent           # Refresh views without locks
python etl.py complete --month MM/AAAA --parallel     # Full pipeline (all stages)

# Docker
docker compose up -d postgres
ETL_UID=$(id -u) ETL_GID=$(id -g) docker compose up --abort-on-container-exit etl-init-permissions
docker compose run --rm etl complete --month MM/AAAA --parallel
```

There is no automated test suite. Validate changes by running CLI flows on a small month (e.g., `--month 01/2024`) and checking logs in `data/logs/`.

## Architecture

**Pipeline stages** (sequential, resumable from any stage):
`download → init → load → patch → pk → index → fk → views`

**Core flow:**
1. **Scraper** (`cnpj_data/cnpj_public_data.py`) — scrapes RFB site for available data months
2. **Downloader** (`cnpj_data/cnpj_downloader.py`) — concurrent threaded downloads with resume support
3. **Orchestrator** (`orchestrator.py`) — routes CLI commands, runs pipeline stages in order
4. **Loader** (`db/postgres_loader.py`) — producer-consumer pattern with queue-based backpressure; uses PostgreSQL COPY for bulk insertion
5. **Batch Producer** (`utils/db_batch_producer.py`) — reads ZIPs in-memory, parses CSV (latin1/semicolon), transforms rows, enriches with IBGE geo codes
6. **Builder** (`db/postgres_builder.py`) — schema DDL, PKs/FKs, indexes (including GIN/BRIN/HASH), materialized views

**Key modules:**
- `src/rfb_cnpj_etl/main.py` — CLI argument parsing (argparse)
- `src/rfb_cnpj_etl/config.py` — all constants and defaults (batch size, threads, paths)
- `src/rfb_cnpj_etl/db/schema.py` — table definitions (source of truth for DB structure)
- `src/rfb_cnpj_etl/db/advanced_indexes.py` — GIN/BRIN/HASH index specifications
- `src/rfb_cnpj_etl/utils/db_transformers.py` — row-level data transformations
- `sql/materialized_views/` — 13 pre-built aggregation views

**Data characteristics:**
- File encoding: latin1, delimiter: semicolon (;)
- Largest table: `estabelecimento` (~35M rows), uses 40% batch size ratio
- `estab_cnae_sec` (~50M+ rows) extracted from comma-separated field in estabelecimento

## Coding Conventions

- Python 3.9+, 4-space indentation, type hints where practical
- `snake_case` for functions/modules; `UPPER_SNAKE_CASE` for constants in `config.py`
- Portuguese for user-facing strings and CLI messages
- Use `utils.logger.print_log(msg, level="success|error|warning|docs|debug")` for output
- Database connections via context manager: `with get_connection(config) as conn:`
- Configuration: override defaults via environment variables (see `.env.example`)
- Commit messages: short, imperative, scoped (e.g., "Adjust postgres batch patching")
- Do not commit `data/` artifacts, secrets, or large logs
