# gamatrix

[![CI](https://github.com/eniklas/gamatrix/actions/workflows/ci.yml/badge.svg?branch=master&event=push)](https://github.com/eniklas/gamatrix/actions/workflows/ci.yml)

Gamatrix compares game libraries across multiple users and shows what they have in common. It requires all users to use [GOG Galaxy](https://www.gog.com/galaxy), which aggregates games from Steam, Epic, and other platforms into a single local SQLite database. Users upload their GOG Galaxy DB through the web UI; multiplayer support and max-player counts are filled in automatically from [IGDB](https://www.igdb.com).

The app runs as a FastAPI service on AWS Lambda, backed by DynamoDB. IGDB enrichment happens asynchronously in a background worker so the game list loads immediately and updates live as data arrives.

## Features

- Compare game libraries across any number of users, with filters for multiplayer-only, installed-only, and per-platform exclusions
- Multiplayer support and max players populated from IGDB automatically
- Game list and game grid views; option to pick a random game
- User accounts with email/password login and password reset via email
- GOG Galaxy DB upload from the browser or a scheduled script

## Screenshots

### Game list

![Game list](/doc/images/gamatrix-game-list.png)

- Titles supporting fewer players than selected are greyed out
- Under `Installed`, a checkmark means all selected users have the game installed; otherwise the names of users who have it installed are shown

### Game grid

![Game grid](/doc/images/gamatrix-game-grid.png)

- Green cells indicate the user owns the game; red indicates they don't
- A checkmark means the user has the game installed

## Uploading your DB

Use the **Upload DB** link to upload your GOG Galaxy database. The file is at `C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db` on Windows.

## Local development

Everything runs in Docker — you don't install Python or the app on your host.

**Prerequisites:**

- **Docker** (Desktop or Engine) — the only hard host requirement.
- **A local GOG Galaxy DB.** Sample data is generated from *your own* library, so you
  need GOG Galaxy installed with some games. The DB lives at
  `C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db` (Windows) or
  `~/Library/Application Support/GOG.com/Galaxy/storage/galaxy-2.0.db` (macOS). Gamatrix
  is a GOG Galaxy tool, so this is a genuine prerequisite for meaningful local work.
- **IGDB credentials** for multiplayer metadata (see [below](#igdb-credentials)).
- **[just](#just)** — optional; every recipe is a thin `docker compose` wrapper you can
  also run by hand.

```bash
cp .env-sample .env          # then set IGDB_CLIENT_ID / IGDB_CLIENT_SECRET (and JWT_SECRET)
just up                      # start app + dynamodb-local + minio + mailhog + worker
just bootstrap db="C:/path/to/galaxy-2.0.db"   # generate sample users + create tables + seed
```

`bootstrap` generates git-ignored fixtures from your GOG DB (under `scripts/sample_data/`),
creates the local DynamoDB tables and S3 bucket, and seeds 3 test users with overlapping
libraries. The bundled worker then enriches the games from IGDB automatically. Open
http://localhost:8088 and log in as `user1@example.com` / `changeme` (the first user is the
admin). Password-reset emails are captured by mailhog at http://localhost:8025.

The sample-data shape is configurable — more users, more games, different overlaps:

```bash
just gen-fixtures db="C:/path/to/galaxy-2.0.db" users="4" games="25" common="6" pair="4"
just seed-local              # re-seed from the regenerated fixtures (idempotent)
```

| flag | meaning | default |
|------|---------|---------|
| `users` | number of test users | `3` |
| `games` | games per user | `20` |
| `common` | games owned by **all** users | `5` |
| `pair` | games shared by **each unique pair** | `5` |
| `usernames` | comma/space emails (first = admin) | `user1@example.com`… |

Without `just`, run the underlying commands directly (use forward slashes; the path after
`app` is *inside* the Linux container):

```bash
docker compose up -d
docker compose run --rm -v "C:/path/to/galaxy-2.0.db:/data/source.db:ro" app \
  python scripts/sample_data/generate_fixtures.py --source /data/source.db --output scripts/sample_data
docker compose run --rm app python scripts/init_local.py
docker compose run --rm app python scripts/seed_sample_data.py
```

### just

[just](https://github.com/casey/just) runs this repo's task recipes (see the `justfile`).
It's a small standalone binary, not a Python tool:

```bash
brew install just            # macOS
winget install Casey.Just    # Windows
cargo install just           # anywhere with Rust
```

`just --list` shows every recipe. Each one just wraps a `docker compose` (or `uv`) command,
so you can always run the underlying command directly if you'd rather not install it.

### uv

[uv](https://docs.astral.sh/uv/) manages the Python toolchain and dependencies. You only
need it for **host-side** tooling — running tests, linters, or your editor's language
server — since the Docker dev loop above doesn't touch your host Python:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS/Linux
winget install astral-sh.uv                       # Windows
uv sync --extra dev                               # create .venv with dev deps
```

(Avoid `pip install -e .[dev]` on a bleeding-edge host Python — some pinned deps have no
matching wheels and fall back to a source build that fails. `uv` provisions a compatible
Python for you.)

### IGDB credentials

IGDB provides multiplayer metadata. To get credentials:

1. Register a Twitch app at [dev.twitch.tv/console](https://dev.twitch.tv/console)
2. Enable IGDB API access
3. Copy the `Client ID` and `Client Secret` into `.env`

In AWS, credentials are stored in Secrets Manager (see [deployment](#deployment)).

### Running checks

```bash
just check     # black + flake8 + mypy + pytest
```

## Deployment

Infrastructure is managed with AWS CDK. See [`infrastructure/cdk/README.md`](infrastructure/cdk/README.md) for full instructions.

```bash
just deploy    # cdk deploy to ca-central-1
```

After deploying, store your IGDB credentials in Secrets Manager:

```bash
just set-igdb-secret <client_id> <client_secret>
```

Then seed user accounts by running `scripts/seed_users.py` against the deployed tables.

## Contributing

PRs are welcome. If you're making non-trivial changes, please include test output. Before opening a PR, run `just check`. Versioning is automatic: merging to `master` tags the commit with the next patch version. For a bigger bump, add the `new minor version` or `new major version` label to your PR.

### Development setup

Host-side tooling (tests, linters, editor LSP) uses [uv](#uv):

```bash
git clone https://github.com/eniklas/gamatrix
cd gamatrix
uv sync --extra dev          # creates .venv with the dev dependencies
```

You don't need this just to run the app — that's fully containerized (see
[Local development](#local-development)).

### Building a wheel

```bash
python -m pip install .[ci]
python -m build --wheel        # produces dist/gamatrix-*-none-any.whl
```
