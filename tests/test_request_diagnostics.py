import logging

from flask import jsonify


def test_liveness_identifies_release_without_touching_applications(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}
    assert response.headers["X-App-Release"] == "2026-11-03.2"


def test_handled_service_failure_has_correlated_log_without_private_values(app, caplog):
    @app.post("/api/test-failure/<private_id>")
    def simulated_failure(private_id):
        return jsonify({"error": "No se pudo guardar el documento."}), 503

    with caplog.at_level(logging.ERROR):
        response = app.test_client().post(
            "/api/test-failure/private-application-id",
            headers={"X-Draft-Token": "private-draft-token"},
            json={"contactEmail": "private@example.uy"},
        )

    assert response.status_code == 503, response.json
    request_id = response.headers["X-Request-ID"]
    assert request_id
    assert f"request_id={request_id}" in caplog.text
    assert "method=POST route=/api/test-failure/<private_id> status=503" in caplog.text
    for private_value in ("private-application-id", "private-draft-token", "private@example.uy"):
        assert private_value not in caplog.text
