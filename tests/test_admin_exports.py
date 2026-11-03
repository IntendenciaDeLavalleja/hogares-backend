import csv
import secrets
from datetime import date, timedelta
from io import BytesIO, StringIO

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from test_admin_panel import form, login
from test_auth import create_user

from app.extensions import db
from app.models import ActivityLog, Application, ApplicationDocument
from app.models.application import utcnow
from app.services import EncryptionService
from app.services.admin_applications import csv_cell, household_income, household_summary
from app.services.requirements import required_document_types


def add_application(payload, *, home="minas", kind="new", year=2026, code="EXPORT-001", status="submitted"):
    item = Application(
        tracking_code=code,
        home=home,
        application_type=kind,
        application_year=year,
        status=status,
        draft_token_hash=secrets.token_hex(32),
        expires_at=utcnow() + timedelta(days=1),
        submitted_at=utcnow() if status == "submitted" else None,
        payload_encrypted=EncryptionService.encrypt(payload),
    )
    db.session.add(item)
    db.session.commit()
    return item


@pytest.fixture()
def admin_client(app, client, monkeypatch):
    with app.app_context():
        create_user(role="super_admin")
    login(client, monkeypatch)
    return client


def test_csv_exports_all_matching_rows_and_type_filter(app, admin_client, payload):
    with app.app_context():
        for number in range(27):
            add_application(payload, code=f"MATCH-{number:03d}")
        add_application(payload, code="MATCH-RENEW", kind="renewal")
        add_application(payload, code="MATCH-OTHER", home="montevideo")
        add_application(payload, code="MATCH-YEAR", year=2027)
        add_application(payload, code="MATCH-DRAFT", status="draft")
    query = "home=minas&type=new&status=submitted&year=2026&q=MATCH"
    page = admin_client.get("/admin/applications?" + query)
    assert "27 registros" in page.text
    assert "MATCH-RENEW" not in page.text
    response = admin_client.get("/admin/applications/export.csv?" + query + "&page=2")
    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(StringIO(response.data.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 28
    assert all(row[3] == "Primera inscripción" for row in rows[1:])
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"
    with app.app_context():
        assert ActivityLog.query.filter_by(action="admin:export_applications_csv").count() == 1


@pytest.mark.parametrize("value", ["=SUM(1)", "+123", "-1", "@formula", "  =1", "\tformula"])
def test_csv_escapes_formulas(value):
    assert csv_cell(value).startswith("'")


def test_income_does_not_treat_missing_as_zero_or_double_count():
    data = {"householdMembers": [{"monthlyIncome": 0}, {"monthlyIncome": 12500}], "health": {"monthlySalary": 12500}}
    assert household_income(data)[0] == 12500
    data["householdMembers"].append({"monthlyIncome": None})
    assert household_income(data) == (None, 1)


def test_comparison_keeps_cohorts_separate(app, admin_client, payload):
    with app.app_context():
        add_application(payload, code="FIRST")
        add_application(payload, code="RENEW", kind="renewal")
        add_application(payload, code="OTHER", home="montevideo")
        add_application(payload, code="NEXT", year=2027)
    page = admin_client.get("/admin/comparison?home=minas&type=renewal&year=2026")
    assert page.status_code == 200
    assert "RENEW" in page.text
    assert "FIRST" not in page.text and "OTHER" not in page.text and "NEXT" not in page.text
    assert "El aspirante cuenta una vez" in page.text
    assert admin_client.get("/admin/comparison?type=invalid").status_code == 400


def test_income_per_person_counts_applicant_and_salary_once(payload):
    payload["health"].update(currentlyWorks=True, monthlySalary=15000)
    payload["householdMembers"][0]["monthlyIncome"] = 45000
    payload["householdMembers"].append({"monthlyIncome": 0})
    payload["outsideParents"] = [{"childSupportIncome": 99999}]
    payload["studies"]["average"] = 9.5
    summary = household_summary(payload)
    assert summary["residents"] == 3
    assert summary["total_income"] == 60000
    assert summary["income_per_person"] == 20000
    assert summary["average"] == 9.5
    payload["health"]["currentlyWorks"] = False
    assert household_summary(payload)["total_income"] == 45000


@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", -1, 1_000_000_001])
def test_missing_or_invalid_income_is_not_inferred(payload, value):
    payload["householdMembers"][0]["monthlyIncome"] = value
    summary = household_summary(payload)
    assert summary["income_per_person"] is None and summary["total_income"] is None
    assert summary["residents"] == 2 and summary["missing_income"] == 1


def test_living_alone_and_zero_income_are_supported(payload):
    payload["householdMembers"] = []
    payload["household"]["livesAlone"] = True
    payload["studies"]["average"] = 0
    summary = household_summary(payload)
    assert summary["residents"] == 1 and summary["income_per_person"] == 0
    assert summary["average"] == 0
    payload["household"]["livesAlone"] = False
    assert household_summary(payload)["residents"] is None
    assert household_summary(payload)["income_per_person"] is None


def test_missing_salary_or_average_stays_incomplete(payload):
    payload["health"].update(currentlyWorks=True, monthlySalary="")
    assert household_summary(payload)["income_per_person"] is None
    assert household_summary(payload)["missing_income"] == 1
    for value in [None, "", "NaN", 13]:
        payload["studies"]["average"] = value
        assert household_summary(payload)["average"] is None


def test_review_filters_multiple_homes_types_and_exports_same_results(app, admin_client, payload):
    payload["studies"]["average"] = 9
    with app.app_context():
        add_application(payload, code="KEEP-FIRST")
        add_application(payload, code="KEEP-RENEW", kind="renewal")
        add_application(payload, code="KEEP-MONTEVIDEO", home="montevideo")
        add_application(payload, code="SKIP-MARISCALA", home="mariscala")
        add_application(payload, code="SKIP-NEXT", year=2027)
        add_application(payload, code="SKIP-DRAFT", status="draft")
    query = "home=minas&home=montevideo&type=new&type=renewal&year=2026"
    page = admin_client.get("/admin/comparison?" + query)
    assert page.status_code == 200
    assert "3 inscripciones encontradas" in page.text
    assert "Ranking ponderado" in page.text
    assert all(code in page.text for code in ["KEEP-FIRST", "KEEP-RENEW", "KEEP-MONTEVIDEO"])
    assert all(code not in page.text for code in ["SKIP-MARISCALA", "SKIP-NEXT", "SKIP-DRAFT"])
    assert "home=minas&amp;home=montevideo" in page.text
    assert "type=new&amp;type=renewal" in page.text
    assert "$ 22500.00" in page.text

    ponderador = admin_client.get("/admin/ponderador")
    assert ponderador.status_code == 200
    assert "Ponderador" in ponderador.text
    assert "Ingreso mensual conviviente (%)" in ponderador.text
    assert "Promedio de escolaridad (%)" in ponderador.text
    assert "KEEP-FIRST" not in ponderador.text
    assert "3 inscripciones encontradas" not in ponderador.text
    assert "Ranking ponderado" not in ponderador.text

    response = admin_client.get("/admin/comparison/export.csv?" + query + "&page=99")
    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    assert response.headers["Cache-Control"] == "no-store"
    rows = list(csv.DictReader(StringIO(response.data.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 3
    assert {row["Código"] for row in rows} == {"KEEP-FIRST", "KEEP-RENEW", "KEEP-MONTEVIDEO"}
    assert all(row["Ingreso mensual por persona"] == "22500.00" for row in rows)
    assert all(row["Personas en el hogar (incluye aspirante)"] == "2" for row in rows)
    assert all(row["Promedio escolar declarado"] == "9.00" for row in rows)
    with app.app_context():
        assert ActivityLog.query.filter_by(action="admin:export_household_review_csv").count() == 1


@pytest.mark.parametrize("query", ["home=minas&home=invalid", "type=new&type=invalid", "year=oops", "year=2101"])
def test_review_rejects_invalid_filters_in_pages_and_csv(admin_client, query):
    for path in [
        "/admin/comparison",
        "/admin/comparison/export.csv",
        "/admin/comparison/ranking.csv",
        "/admin/ponderador/export.csv",
        "/admin/ponderador/ranking.csv",
    ]:
        assert admin_client.get(path + "?" + query).status_code == 400


def test_review_export_incomplete_values_formula_protection_and_chronology(app, admin_client, payload):
    with app.app_context():
        payload["applicant"]["fullName"] = "=FORMULA"
        payload["householdMembers"][0]["monthlyIncome"] = None
        add_application(payload, code="EARLY-INCOMPLETE")
        payload["applicant"]["fullName"] = "Otra Persona"
        payload["householdMembers"][0]["monthlyIncome"] = 10000
        payload["studies"]["average"] = 10
        add_application(payload, code="LATER-COMPLETE")
    response = admin_client.get("/admin/comparison/export.csv")
    rows = list(csv.DictReader(StringIO(response.data.decode("utf-8-sig")), delimiter=";"))
    assert [row["Código"] for row in rows] == ["EARLY-INCOMPLETE", "LATER-COMPLETE"]
    assert rows[0]["Nombre"] == "'=FORMULA"
    assert rows[0]["Ingreso mensual por persona"] == "" and rows[0]["Promedio escolar declarado"] == ""
    assert rows[0]["Datos completos"] == "No" and rows[1]["Datos completos"] == "Sí"
    assert rows[1]["Ingreso mensual por persona"] == "5000.00"


def test_review_and_csv_require_admin_session(client):
    for path in [
        "/admin/ponderador",
        "/admin/ponderador/export.csv",
        "/admin/comparison",
        "/admin/comparison/export.csv",
    ]:
        assert client.get(path).location.endswith("/admin/login")


def test_certificate_receipt_requires_csrf_and_is_audited(app, admin_client, payload):
    with app.app_context():
        item_id = add_application(payload).id
    path = f"/admin/applications/{item_id}/good-conduct"
    assert admin_client.post(path, data={"received": "true"}).status_code == 400
    assert form(admin_client, path, {"received": "true"}).status_code == 302
    with app.app_context():
        item = db.session.get(Application, item_id)
        assert item.good_conduct_received_at and item.good_conduct_received_by
        first_time = item.good_conduct_received_at
    assert form(admin_client, path, {"received": "true"}).status_code == 302
    with app.app_context():
        assert db.session.get(Application, item_id).good_conduct_received_at == first_time
    assert "Registrado por" in admin_client.get(f"/admin/applications/{item_id}").text
    assert form(admin_client, path, {"received": "false"}).status_code == 302
    with app.app_context():
        assert db.session.get(Application, item_id).good_conduct_received_at is None
        assert ActivityLog.query.filter_by(action="admin:good_conduct_receipt").count() == 3


def test_institution_email_is_editable_and_public(admin_client):
    assert admin_client.get("/admin/settings").status_code == 200
    assert form(admin_client, "/admin/settings", {"good_conduct_email": "institucion@example.uy"}).status_code == 302
    assert admin_client.get("/api/applications/institution").json == {"goodConductEmail": "institucion@example.uy"}
    assert (
        "Ingresá un correo institucional válido"
        in form(
            admin_client,
            "/admin/settings",
            {"good_conduct_email": "invalid"},
        ).text
    )


def test_adults_no_longer_upload_good_conduct(payload):
    payload["applicant"]["birthDate"] = date(1990, 1, 1)
    assert "good_conduct" not in required_document_types(payload)


def test_private_exports_require_login(client):
    for path in [
        "/admin/applications/export.csv",
        "/admin/applications/id/file.pdf",
        "/admin/comparison",
        "/admin/comparison/export.csv",
        "/admin/ponderador/export.csv",
    ]:
        assert client.get(path).location.endswith("/admin/login")


def pdf_fixture(app, payload):
    payload["socioeconomic"]["notes"] = "Observación extensa de prueba. " * 180
    item = add_application(payload)
    image = BytesIO()
    Image.new("RGB", (1000, 600), color="#517ba5").save(image, format="PNG")
    attachment = BytesIO()
    page = canvas.Canvas(attachment)
    page.drawString(60, 700, "Escolaridad adjunta - prueba")
    page.save()
    content = {"test/image": image.getvalue(), "test/pdf": attachment.getvalue()}
    for key, mime, name in [
        ("test/image", "image/png", "Fotografía.png"),
        ("test/pdf", "application/pdf", "Escolaridad.pdf"),
    ]:
        db.session.add(
            ApplicationDocument(
                application_id=item.id,
                document_type="passport_photo" if mime == "image/png" else "school_records",
                object_key=key,
                original_name=name,
                stored_name=name,
                media_type=mime,
                size_bytes=len(content[key]),
                checksum_sha256="a" * 64,
            )
        )
    db.session.commit()
    return item.id, {key: EncryptionService.encrypt_file(value) for key, value in content.items()}


def test_pdf_contains_fields_images_and_pdf_pages(app, admin_client, payload, monkeypatch):
    from app.services.application_pdf import StorageService

    payload["health"].update(hasDisability=True, dailyLivingSupports="Apoyo para desplazarse.")
    with app.app_context():
        item_id, objects = pdf_fixture(app, payload)
    monkeypatch.setattr(StorageService, "get", lambda _self, key: objects[key])
    response = admin_client.get(f"/admin/applications/{item_id}/file.pdf")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    pdf = PdfReader(BytesIO(response.data))
    text = "\n".join(page.extract_text() for page in pdf.pages)
    assert "Persona Aspirante" in text
    assert "Apoyo para desplazarse." in text
    assert "Situación de discapacidad" in text
    assert "Escolaridad adjunta - prueba" in text
    assert "Observación extensa de prueba." in text
    assert any(len(page.images) for page in pdf.pages)
    assert response.headers["Cache-Control"] == "no-store"
    monkeypatch.setattr(StorageService, "get", lambda _self, _key: b"invalid")
    assert admin_client.get(f"/admin/applications/{item_id}/file.pdf").status_code == 503
