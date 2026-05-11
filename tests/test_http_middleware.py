from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from logmind.core.middleware import RequestLoggingMiddleware, TenantMiddleware
from logmind.core.rate_limiter import RateLimitMiddleware


def test_request_logging_middleware_adds_request_id_header():
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/ping")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


def test_tenant_middleware_initializes_request_state():
    app = FastAPI()
    app.add_middleware(TenantMiddleware)

    @app.get("/state")
    async def state(request: Request):
        return {
            "tenant_id": request.state.tenant_id,
            "user_id": request.state.user_id,
            "user_role": request.state.user_role,
        }

    with TestClient(app) as client:
        response = client.get("/state")

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": None,
        "user_id": None,
        "user_role": None,
    }


def test_rate_limit_middleware_sets_headers(monkeypatch):
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/api/v1/ping")
    async def ping():
        return {"ok": True}

    async def allow_request(key: str, max_requests: int, window_seconds: int):
        return True, 59, 0

    monkeypatch.setattr(RateLimitMiddleware, "_check_rate_limit", staticmethod(allow_request))

    with TestClient(app) as client:
        response = client.get("/api/v1/ping")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "59"
