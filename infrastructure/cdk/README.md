# Gamatrix infrastructure (CDK)

AWS CDK (Python) stack for gamatrix v2.

## Prerequisites

- Node + the CDK CLI (`npm install -g aws-cdk`)
- Docker (the Lambdas are built as container images)
- AWS credentials configured

`aws_cdk` uses `jsii`, which launches `node` as a subprocess. If `node` is not
on your shell `PATH`, CDK commands and the host-side CDK pytest coverage will
fail early, often with a low-signal `FileNotFoundError` during import/synth.

## Install

`cdk.json` runs the app with the project venv (`../../.venv/bin/python`), so the
`cdk` extra has to be installed there. Sync it together with `dev` so a later
`uv sync` (e.g. `just install`) doesn't prune it back out:

```bash
uv sync --extra dev --extra cdk   # from the repo root
```

## Deploy

`just deploy` runs the sync above before deploying. To run CDK directly:

```bash
cd infrastructure/cdk
npx cdk bootstrap   # first time in an account/region
npx cdk deploy
```

## What it creates

- DynamoDB tables: `games`, `users`, `user_libraries` (+ `release_key-index` GSI),
  `enrichment_jobs`, `metadata_overrides`, `config`, `passkeys`
  (+ `user_handle-index` GSI), and TTL-enabled `auth_challenges`
  (PAY_PER_REQUEST, PITR on)
- S3 upload bucket (1-day lifecycle expiry, CORS for browser POST)
- SQS enrichment queue
- Lambdas: web (HTTP API + Mangum), enricher (SQS-triggered), parser (S3-triggered)
- HTTP API (API Gateway v2) in front of the web Lambda
- Required custom domain, ACM certificate (DNS-validated via Route 53), and
  alias A-record. The stable custom domain is the WebAuthn RP scope.
- SES domain identity is **not** managed by this stack — verify the sender
  domain in the SES console once and it persists independently.
- Secrets Manager secret `gamatrix/igdb` — populate after deploy:
  ```bash
  aws secretsmanager put-secret-value --secret-id gamatrix/igdb \
    --secret-string '{"client_id":"...","client_secret":"..."}'
  ```
- SSM params for the hidden / single-player lists and stale-days threshold

## Deployment config (domain / SES)

Domain and sender values are **not** stored in this public repo. They come from
a private config file, by default `../gamatrix-configs/cdk-config.yaml`
(override the directory with `GAMATRIX_CONFIG_DIR`):

```yaml
hosted_zone: example.com          # existing Route 53 hosted zone
site_domain: gamatrix.example.com # public hostname for the app
email_from: noreply@example.com   # SES sender for password-reset email

# Optional: extra hostnames that should also resolve to the same app.
# alias_hosted_zone must be a Route 53-managed zone; alias_domains must be
# subdomains of it. The ACM cert is extended with SANs for all alias_domains,
# so there are no cert warnings. Primary and alias zones may differ.
alias_hosted_zone: other.com
alias_domains:
  - games.other.com
  - app.other.com
```

If the file is absent (or omits `hosted_zone`/`site_domain`), stack synthesis
fails because WebAuthn
credentials are permanently scoped to the configured RP ID. Enroll passkeys
only after the canonical domain is stable; passkeys enrolled against an API
Gateway hostname or an old RP ID do not transfer automatically. Alias domains
redirect to the canonical `site_domain`. When a domain *is* configured, the
hosted zone is looked up via
`HostedZone.from_lookup`, so the stack must be deployed with an explicit
account/region (set by `app.py` from `CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION`).

## Post-deploy

1. Put the IGDB credentials in the secret (above).
2. The JWT signing key is created automatically as the `gamatrix/jwt-secret`
   Secrets Manager secret (generated on first deploy, stable thereafter) and the
   web Lambda reads it via `JWT_SECRET_NAME` — no manual step required.
3. **SES sandbox**: the domain identity and DKIM records are created by the
   stack, but a new SES account starts in the sandbox (can only send to verified
   recipients). Request production access in the SES console so reset emails
   reach all users. The cert and DKIM validations complete automatically once
   the Route 53 records propagate.
4. Seed user accounts: run `scripts/seed_users.py` against the deployed tables
   (set `TABLE_PREFIX` and AWS creds, unset the local endpoint env vars).
