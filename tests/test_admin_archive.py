import csv
from datetime import datetime
from io import StringIO

import pytest
from test_admin_exports import add_application
from test_admin_panel import form, login
from test_auth import create_user

from app.extensions import db
from app.models import ActivityLog, Application, User


@pytest.fixture()
def admin_client(app, client, monkeypatch):
    with app.app_context():
        create_user(role="super_admin")
    login(client, monkeypatch)
    return client


def csv_rows(response):
    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    return list(csv.DictReader(StringIO(response.data.decode("utf-8-sig")), delimiter=";"))


def set_submitted_at(app, code, value):
    with app.app_context():
        item = Application.query.filter_by(tracking_code=code).one()
        item.submitted_at = value
        db.session.commit()


def test_archive_selected_and_view_archived(app, admin_client, payload):
    with app.app_context():
        first = add_application(payload, code="ARCH-1")
        second = add_application(payload, code="ARCH-2")
        third = add_application(payload, code="KEEP-3")
        first_id, second_id, third_id = first.id, second.id, third.id

    assert form(
        admin_client,
        "/admin/applications/archive",
        {
            "scope": "selected",
            "archive_name": "Convocatoria Primera 2026",
            "application_id": [first_id, second_id],
        },
    ).status_code == 302

    with app.app_context():
        for application_id in (first_id, second_id):
            item = db.session.get(Application, application_id)
            assert item.archived_at is not None
            assert item.archive_name == "Convocatoria Primera 2026"
            assert item.archived_by is not None
        assert db.session.get(Application, third_id).archived_at is None
        assert ActivityLog.query.filter_by(action="admin:archive_applications").count() == 1

    applications = admin_client.get("/admin/applications")
    assert "ARCH-1" not in applications.text and "ARCH-2" not in applications.text
    assert "KEEP-3" in applications.text
    comparison = admin_client.get("/admin/comparison")
    assert "ARCH-1" not in comparison.text and "ARCH-2" not in comparison.text
    assert "KEEP-3" in comparison.text

    archived = admin_client.get("/admin/archived")
    assert archived.status_code == 200
    assert "Convocatoria Primera 2026" in archived.text
    assert "ARCH-1" in archived.text and "ARCH-2" in archived.text
    assert "KEEP-3" not in archived.text

    rows = csv_rows(admin_client.get("/admin/archived/export.csv"))
    assert {row["Código"] for row in rows} == {"ARCH-1", "ARCH-2"}
    assert all(row["Convocatoria / archivo"] == "Convocatoria Primera 2026" for row in rows)
    assert all(row["Fecha de archivo (UTC)"] for row in rows)
    with app.app_context():
        assert ActivityLog.query.filter_by(action="admin:export_archived_csv").count() == 1


def test_archive_all_filtered_respects_filters_and_only_submitted(app, admin_client, payload):
    with app.app_context():
        add_application(payload, code="MINAS-NEW", home="minas", kind="new")
        add_application(payload, code="MINAS-RENEW", home="minas", kind="renewal")
        add_application(payload, code="MONTEVIDEO-NEW", home="montevideo", kind="new")
        add_application(payload, code="MINAS-DRAFT", home="minas", kind="new", status="draft")

    query = "home=minas&type=new&year=2026"
    response = form(
        admin_client,
        "/admin/applications/archive?" + query,
        {"scope": "filtered", "archive_name": "Convocatoria Primera 2026"},
    )
    assert response.status_code == 302

    with app.app_context():
        archived = {item.tracking_code for item in Application.query.filter(Application.archived_at.is_not(None))}
        assert archived == {"MINAS-NEW"}
        assert Application.query.filter_by(tracking_code="MINAS-DRAFT").one().archived_at is None

    page = admin_client.get("/admin/archived")
    assert "MINAS-NEW" in page.text
    assert "MINAS-RENEW" not in page.text
    assert "MONTEVIDEO-NEW" not in page.text
    assert "MINAS-DRAFT" not in page.text


def test_archive_validation_and_csrf(app, admin_client, payload):
    with app.app_context():
        item = add_application(payload, code="VALIDATION-1")
        item_id = item.id

    assert admin_client.post("/admin/applications/archive", data={}).status_code == 400

    cases = [
        (
            {"scope": "selected", "archive_name": "", "application_id": [item_id]},
            "Ingresá un nombre",
        ),
        (
            {"scope": "selected", "archive_name": "AB", "application_id": [item_id]},
            "entre 3 y 120",
        ),
        (
            {"scope": "selected", "archive_name": "X" * 121, "application_id": [item_id]},
            "entre 3 y 120",
        ),
        (
            {"scope": "selected", "archive_name": "Convocatoria válida"},
            "Seleccioná al menos una",
        ),
    ]
    for data, message in cases:
        response = form(admin_client, "/admin/applications/archive", data)
        assert response.status_code == 302
        page = admin_client.get(response.location)
        assert message in page.text
        with app.app_context():
            assert db.session.get(Application, item_id).archived_at is None

    assert form(
        admin_client,
        "/admin/applications/archive",
        {"scope": "invalid", "archive_name": "Convocatoria válida", "application_id": [item_id]},
    ).status_code == 400


def test_date_range_filters_applications_comparison_and_exports(app, admin_client, payload):
    with app.app_context():
        payload["studies"]["average"] = 8
        for code in ["JANUARY", "FEBRUARY", "MARCH"]:
            add_application(payload, code=code)
        db.session.commit()
    set_submitted_at(app, "JANUARY", datetime(2026, 1, 15, 12, 0))
    set_submitted_at(app, "FEBRUARY", datetime(2026, 2, 15, 12, 0))
    set_submitted_at(app, "MARCH", datetime(2026, 3, 15, 12, 0))

    query = "date_from=2026-01-01&date_to=2026-01-31"
    page = admin_client.get("/admin/applications?" + query)
    assert page.status_code == 200
    assert "JANUARY" in page.text
    assert "FEBRUARY" not in page.text and "MARCH" not in page.text
    assert "15/01/2026" in page.text

    comparison = admin_client.get("/admin/comparison?" + query)
    assert "JANUARY" in comparison.text
    assert "15/01/2026" in comparison.text
    assert "FEBRUARY" not in comparison.text and "MARCH" not in comparison.text

    rows = csv_rows(admin_client.get("/admin/applications/export.csv?" + query))
    assert {row["Código"] for row in rows} == {"JANUARY"}
    rows = csv_rows(admin_client.get("/admin/comparison/ranking.csv?" + query))
    assert {row["Código"] for row in rows} == {"JANUARY"}

    assert "FEBRUARY" in admin_client.get("/admin/applications?date_from=2026-02-01").text
    february_page = admin_client.get("/admin/applications?date_to=2026-02-28")
    assert "JANUARY" in february_page.text and "FEBRUARY" in february_page.text
    assert "MARCH" not in february_page.text

    for invalid in ["date_from=oops", "date_to=2026-13-01", "date_from=2026-12-31&date_to=2026-01-01"]:
        for path in ["/admin/applications", "/admin/comparison", "/admin/applications/export.csv"]:
            assert admin_client.get(path + "?" + invalid).status_code == 400


def test_archived_filters_and_restore(app, admin_client, payload):
    with app.app_context():
        first = add_application(payload, code="GROUP-A", home="minas", kind="new")
        second = add_application(payload, code="GROUP-B", home="montevideo", kind="renewal")
        first_id, second_id = first.id, second.id
    set_submitted_at(app, "GROUP-A", datetime(2026, 1, 15, 12, 0))
    set_submitted_at(app, "GROUP-B", datetime(2026, 2, 15, 12, 0))

    form(
        admin_client,
        "/admin/applications/archive",
        {"scope": "selected", "archive_name": "Convocatoria A", "application_id": [first_id]},
    )
    form(
        admin_client,
        "/admin/applications/archive",
        {"scope": "selected", "archive_name": "Convocatoria B", "application_id": [second_id]},
    )

    page = admin_client.get("/admin/archived?archive=Convocatoria+A")
    assert "GROUP-A" in page.text and "GROUP-B" not in page.text
    page = admin_client.get("/admin/archived?home=montevideo")
    assert "GROUP-B" in page.text and "GROUP-A" not in page.text
    page = admin_client.get("/admin/archived?type=renewal")
    assert "GROUP-B" in page.text and "GROUP-A" not in page.text
    page = admin_client.get("/admin/archived?date_from=2026-02-01&date_to=2026-02-28")
    assert "GROUP-B" in page.text and "GROUP-A" not in page.text
    page = admin_client.get("/admin/archived?q=GROUP-A")
    assert "GROUP-A" in page.text and "GROUP-B" not in page.text

    assert form(
        admin_client,
        "/admin/archived/restore",
        {"archive_name": "Convocatoria A"},
    ).status_code == 302
    with app.app_context():
        assert db.session.get(Application, first_id).archived_at is None
        assert db.session.get(Application, first_id).archive_name is None
        assert db.session.get(Application, second_id).archived_at is not None
        assert ActivityLog.query.filter_by(action="admin:restore_archive").count() == 1
    assert "GROUP-A" in admin_client.get("/admin/applications").text
    assert "GROUP-B" not in admin_client.get("/admin/applications").text


def test_dashboard_and_applications_page_reflect_archive(app, admin_client, payload):
    with app.app_context():
        submitted = add_application(payload, code="ACTIVE-SUBMITTED")
        draft = add_application(payload, code="ACTIVE-DRAFT", status="draft")
        archived_item = add_application(payload, code="ARCHIVED-ONE")
        archived_item.archived_at = datetime(2026, 1, 1, 12, 0)
        archived_item.archive_name = "Convocatoria Vieja"
        archived_item.archived_by = User.query.one().id
        db.session.commit()
        submitted_id, draft_id = submitted.id, draft.id

    dashboard = admin_client.get("/admin/dashboard")
    assert dashboard.status_code == 200
    assert "Archivadas" in dashboard.text
    assert "ARCHIVED-ONE" not in dashboard.text

    page = admin_client.get("/admin/applications")
    assert 'name="date_from"' in page.text
    assert 'name="archive_name"' in page.text
    assert "Inscripción" in page.text
    assert f'value="{submitted_id}"' in page.text
    assert f'value="{draft_id}"' not in page.text

    page = admin_client.get("/admin/applications?type=new&date_from=2025-03-01&date_to=2025-12-31")
    assert 'value="Convocatoria Primera 2025"' in page.text


def test_archived_routes_require_admin(app, admin_client):
    anonymous = app.test_client()
    for path in ["/admin/archived", "/admin/archived/export.csv"]:
        assert anonymous.get(path).location.endswith("/admin/login")
    assert admin_client.post("/admin/archived/restore", data={"archive_name": "X"}).status_code == 400


@pytest.mark.parametrize(
    "query",
    [
        "home=invalid",
        "type=invalid",
        "year=oops",
        "date_from=oops",
        "date_from=2026-12-31&date_to=2026-01-01",
    ],
)
def test_archived_rejects_invalid_filters(admin_client, query):
    for path in ["/admin/archived", "/admin/archived/export.csv"]:
        assert admin_client.get(path + "?" + query).status_code == 400
