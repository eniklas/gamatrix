# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Gamatrix is a Python web application (Flask) that compares game libraries across multiple users via GOG Galaxy SQLite databases. It supports both CLI and server (web) modes. Game metadata (multiplayer support, max players) is fetched from the IGDB API and cached locally in a JSON file.

## Development Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .[dev]
```

If you have `just` installed and a `.env` file configured (see `.env-sample`):

```bash
just dev   # runs a dev Docker container with the source mounted
```

## Key Commands

```bash
# Run all checks (what CI runs)
python -m mypy
python -m black --check .
python -m pytest

# Run a single test file
python -m pytest test/test_gogdb.py

# Run tests with verbose output
python -m pytest -v

# Auto-format code
python -m black .

# Run in server mode locally
python -m gamatrix -c config.yaml -s

# Run in CLI mode
python -m gamatrix -c config.yaml -u <userid>

# Bump version (patch by default; pass "minor" or "major" for others)
just bump-version
just bump-version minor

# Build Docker image
just build

# Build wheel
python -m pip install .[ci]
python -m build --wheel
```

## Architecture

**Entry point:** `src/gamatrix/__main__.py` — parses CLI args with docopt, builds config, initializes Flask routes, and runs either server or CLI mode.

**Helpers (`src/gamatrix/helpers/`):**
- `gogdb_helper.py` — `gogDB` class: opens GOG Galaxy SQLite DBs, queries game ownership/install status, computes common games across users, merges duplicates, and filters results. Core data extraction logic lives here.
- `igdb_helper.py` — `IGDBHelper` class: authenticates with the IGDB (Twitch) API, looks up game metadata (multiplayer modes, max players) by GOG release key or slug, respects rate limits, and stores results in the cache.
- `cache_helper.py` — `Cache` class: reads/writes the JSON cache file (`.cache.json`). The cache stores IGDB API responses keyed by GOG release key. Only saved when dirty.
- `misc_helper.py` — utility functions, notably `get_slug_from_title()` which normalizes titles to lowercase alphanumeric for fuzzy matching.
- `network_helper.py` — IP/CIDR authorization check used by Flask routes.
- `constants.py` — platform names, IGDB game mode IDs, upload settings, etc.

**Templates (`src/gamatrix/templates/`):** Jinja2 templates for the web UI — `index.html.jinja` (user selection), `game_list.html.jinja`, `game_grid.html.jinja`, `upload_status.html.jinja`.

**Config flow:** YAML config file is merged with CLI args in `build_config()`. The config dict is the single shared state object — server mode deep-copies it per request (in `gogDB.__init__`) since Flask reuses the global config.

**Data flow (server mode):**
1. `/compare` route receives user selections from the web form
2. `gogDB` reads the relevant SQLite DBs, finds games common to selected users
3. `IGDBHelper` enriches each game with multiplayer/max-player data from IGDB (with caching)
4. `set_multiplayer_status()` in `__main__.py` applies precedence rules: config metadata > IGDB cache > inferred from game modes
5. Jinja template renders the result

**Tests:** Located in `test/`, run with `pytest`. Coverage is configured in `pyproject.toml` (`--cov=gamatrix --cov-branch`).

## Configuration

Users, DB paths, IGDB credentials, and game metadata/overrides are all in the YAML config file (`config.yaml`; see `config-sample.yaml`). The `metadata` section lets you override `max_players`, add `comment`, or set a `url` per game title. The `hidden` and `single_player` lists filter titles by slug (lowercase, alphanumeric only).

## Version

Version is defined in `pyproject.toml` under `[project]`. Use `just bump-version` to update it — this edits `pyproject.toml` in place. Bump before merging a PR.
