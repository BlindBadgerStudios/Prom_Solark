# Changelog

## 0.3.1 - 2026-10-27

### Added
- `GET /status` is a read-only HTML page for operators. `GET /status?format=json` returns the same fields.
  Overall state follows the shared helper rule: UP when the last success is within 2× the poll interval and
  there is no hard auth failure; DEGRADED when an upstream 5xx/timeout (or other non-auth failure) is backing
  off and the process is still polling; DOWN on auth failure or when there has been no success past the
  15 minute hard age cutoff.
- Sol-Ark extras: plant name, plant id, and today's energy (already fetched during the poll). DEGRADED and
  DOWN keep that last successful snapshot.
- Hints cover cred mounts, docker3 outbound, rate limits, TLS/DNS, empty daylight data, and that shared
  logins can kick other sessions. The HTML page notes localhost access on docker3 via the lab SSH hop.
- Status text redacts tokens, passwords, auth headers, tokenized URLs, and account emails.

### Notes
- `/metrics` is unchanged. No Prometheus metric renames.

## 0.3.0 - 2026-10-27

### Changed
- Dependency bump: `prometheus-client` 0.24.1 → 0.26.0.
- Minor version bump to 0.3.0.

## 0.2.2 - 2026-10-13

Requires PySolark 0.2.2 (`requirements.txt` pins `pysolark==0.2.2`). Uses `renew()` (refresh token first) and
`SolArkAuthError` (auth failures inside HTTP 200 envelopes).

### Fixed
- Startup no longer logs in before serving `/metrics`. The first login runs inside the poll loop with jittered
  exponential backoff (poll interval doubling up to 15 min), so a Sol-Ark outage no longer causes a
  crash/restart login storm. The main thread exits non-zero if the poll thread dies.
- HTTP retries apply only to GETs (429 with `Retry-After`, plus backoff). `POST /oauth/token` is never retried.
  Retries are done once, by PySolark; the exporter no longer stacks its own urllib3 retry on top (no double retry).
- Re-authentication after an auth error happens on the next (backed-off) iteration instead of immediately.
- Sol-Ark API errors are logged as code/message only (no payloads).

### Changed
- `solark_exporter_up` is explicitly set to 0 before `/metrics` is served (no metric name/label change).
- A warning is logged when the plant is auto-selected because `SOLARK_PLANT_ID` is unset.
- pytest moved out of `requirements.txt` (and the Docker image) into `requirements-dev.txt` / the `dev` extra.
- Module logger in `app.main`; ruff config added and the codebase formatted with `ruff format`.
- README: TEST-NET placeholder instead of a LAN IP; `SOLARK_PLANT_ID` documented as recommended.
- `pyproject.toml` version updated from the stale `0.1.0` (latest tag is `v0.2.0`).
