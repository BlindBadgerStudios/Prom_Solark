# Changelog

## 0.2.2 - unreleased

Version aligned with PySolark 0.2.2. `requirements.txt` still pins `pysolark==0.2.1` (the current PyPI release);
bump the pin to `0.2.2` once it is published. The code works with both: on 0.2.1 it falls back to password
login and HTTP 401/403 auth detection; on 0.2.2 it uses `renew()` (refresh token first) and `SolArkAuthError`
(auth failures inside HTTP 200 envelopes).

### Fixed
- Startup no longer logs in before serving `/metrics`. The first login runs inside the poll loop with jittered
  exponential backoff (poll interval doubling up to 15 min), so a Sol-Ark outage no longer causes a
  crash/restart login storm. The main thread exits non-zero if the poll thread dies.
- HTTP retries apply only to GETs (429/502/503/504, `Retry-After` honored). `POST /oauth/token` is never retried.
- Re-authentication after an auth error happens on the next (backed-off) iteration instead of immediately.
- Sol-Ark API errors are logged as code/message only (no payloads).

### Changed
- `solark_exporter_up` is explicitly set to 0 before `/metrics` is served (no metric name/label change).
- A warning is logged when the plant is auto-selected because `SOLARK_PLANT_ID` is unset.
- pytest moved out of `requirements.txt` (and the Docker image) into `requirements-dev.txt` / the `dev` extra.
- Module logger in `app.main`; ruff config added and the codebase formatted with `ruff format`.
- README: TEST-NET placeholder instead of a LAN IP; `SOLARK_PLANT_ID` documented as recommended.
- `pyproject.toml` version updated from the stale `0.1.0` (latest tag is `v0.2.0`).
