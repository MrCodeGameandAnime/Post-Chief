# Continuous integration

`Application checks` runs on every push, pull request, or manual dispatch. Each job has a timeout, uses read-only repository permissions and immutable GitHub Action commit pins, and uses locked application dependencies. Concurrent obsolete runs on the same ref are cancelled.

- **API (sqlite/postgres):** all API, OAuth security and provider transport tests; dependency consistency; migration upgrade, schema drift check, downgrade, and upgrade. PostgreSQL tests create a random schema per fixture and remove only that schema. JUnit results are retained for seven days.
- **Frontend:** clean npm install, TypeScript checks, production bundle and frontend tests. The current scaffold has a landing-screen smoke test; Gate 8 will extend this with dashboard interaction coverage. An empty test suite fails.
- **Container smoke:** generate unique ephemeral secrets, build the application image, start PostgreSQL/Redis, apply migrations and check database-backed API health. Containers and disposable CI volumes are removed on exit. Provider credentials and live social publishing are not used in CI.

All commands run from `root/`. Locally, run `.venv/Scripts/python.exe -m pytest -q`, `npm run typecheck`, `npm run build`, and `npm test`. For PostgreSQL fixture coverage, set `POST_CHIEF_TEST_DATABASE_URL` to a database where the test user can create schemas. Do not print credential-bearing URLs. Migrations use `DATABASE_URL`; downgrade commands in CI operate only on disposable CI databases.

Enable the job groups as required branch checks in GitHub when ready. A configured workflow is not evidence that its remote run passed; report the run URL and observed status after pushing.
