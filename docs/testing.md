# Test harness

HTTP endpoint tests import `TestClient` from `fastapi.testclient` and use ordinary synchronous tests. Tests are async only when they directly await provider or worker internals. This follows the official [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/) and [async testing](https://fastapi.tiangolo.com/advanced/async-tests/) patterns.

Pytest enables strict configuration/markers, function-scoped async loops, and warnings-as-errors. There is one narrow compatibility exception for the locked Starlette 1.7.0 HTTPX fallback:

- Exact message: ``Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.``
- Category: `starlette.exceptions.StarletteDeprecationWarning`.
- Origin: Starlette's `testclient.py` import fallback, emitted with `stacklevel=2` through `fastapi.testclient`.
- Filter: anchored exact message, exact category, and only module `fastapi.testclient`. Other warnings and the same message from Post Chief remain errors. Harness tests check both boundaries.

The exception retains the current official FastAPI TestClient/HTTPX pattern with the pinned dependency set. Revisit it when updating Starlette/FastAPI or deliberately migrating TestClient's transport. Do not add broad deprecation filters.

Default temporary state is `root/.pytest-tmp`, ignored by Git and outside source. Container checks may override it to `/app/data/pytest`, still inside the writable application runtime. Each PostgreSQL fixture creates one random schema and removes only that schema. SQLite and PostgreSQL run the same API/provider/scheduler suite in CI.

Celery integration uses TCP Redis with a unique queue and key prefix per test run. Tests do not create AF_UNIX sockets and do not inspect or clean user-profile Docker sockets. If a future test needs a socket, create its exact path under `tmp_path`, track whether that test created it, reject symlinks/reparse points, tolerate an already-removed path, and unlink only that owned resource. Do not recursively remove socket directories.

Gate 7 acceptance requires scheduler, Celery/Redis integration, duplicate dispatch, leases, retry isolation, API and migrations to pass locally, plus a successful remote CI run. Transport mocks alone do not prove queue delivery or live social publishing.

Frontend tests use at most two Vitest workers. This bounds simultaneous jsdom startup on development machines while retaining the ordinary five-second per-test timeout.
