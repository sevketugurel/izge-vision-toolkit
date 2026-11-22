# Verification

The [CI workflow](../.github/workflows/ci.yml) records each run and its test counts. The [security workflow](../.github/workflows/security.yml) checks history and dependencies.

The tracking demo and its score are synthetic. PostgreSQL integration tests require a disposable PostgreSQL 18 server. Build results are not deployment certification. Checks that are not run, including browser tests when unavailable, are not counted as passing.
