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

**Prerequisites:** Docker, [just](https://github.com/casey/just), IGDB credentials (see below).

```bash
cp .env-sample .env          # fill in IGDB_CLIENT_ID and IGDB_CLIENT_SECRET
just up                      # start app + dynamodb-local + minio + mailhog
just init-local              # create tables/bucket and seed default users
just worker                  # start the background enrichment worker
```

The app is at http://localhost:8088. Default users are seeded by `scripts/seed_users.py` with password `changeme`. Password-reset emails are captured by mailhog at http://localhost:8025.

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

```bash
git clone https://github.com/eniklas/gamatrix
cd gamatrix
python3 -m venv .venv
. .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1  # Windows
python -m pip install -U pip
python -m pip install -e .[dev]
```

Or, if you have [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
```

### Building a wheel

```bash
python -m pip install .[ci]
python -m build --wheel        # produces dist/gamatrix-*-none-any.whl
```
