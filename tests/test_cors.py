import pytest

ALLOWED_ORIGIN = "http://localhost:5173"


@pytest.mark.parametrize("path", ["/api/applications/institution", "/api/applications/track"])
def test_public_api_allows_configured_frontend(client, path):
    response = client.options(path, headers={
        "Origin": ALLOWED_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-Draft-Token,X-Captcha-Token,X-Captcha-Answer",
    })
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    headers = response.headers["Access-Control-Allow-Headers"].lower()
    for name in ["content-type", "x-draft-token", "x-captcha-token", "x-captcha-answer"]:
        assert name in headers
    assert "POST" in response.headers["Access-Control-Allow-Methods"]
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_actual_response_has_cors_headers(client):
    response = client.get("/api/applications/institution", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN


def test_other_origins_are_not_allowed(client):
    response = client.options("/api/applications/drafts", headers={
        "Origin": "https://otro.example.uy", "Access-Control-Request-Method": "POST",
    })
    assert "Access-Control-Allow-Origin" not in response.headers


def test_admin_pages_do_not_expose_cross_origin_access(client):
    response = client.get("/admin/login", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
