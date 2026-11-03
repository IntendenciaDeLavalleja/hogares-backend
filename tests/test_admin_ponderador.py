import csv
from decimal import Decimal
from io import StringIO

import pytest
from test_admin_exports import add_application
from test_admin_panel import form, login
from test_auth import create_user

from app.models import ActivityLog, PonderadorSettings


@pytest.fixture()
def admin_client(app, client, monkeypatch):
    with app.app_context():
        create_user(role="super_admin")
    login(client, monkeypatch)
    return client


def add_rankable(payload, code, income, average, *, home="minas", kind="new", name=None):
    payload["householdMembers"][0]["monthlyIncome"] = income
    payload["studies"]["average"] = average
    payload["applicant"]["fullName"] = name or f"Persona {code}"
    return add_application(payload, code=code, home=home, kind=kind)


def csv_rows(response):
    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    assert response.headers["Cache-Control"] == "no-store"
    return list(csv.DictReader(StringIO(response.data.decode("utf-8-sig")), delimiter=";"))


def test_ponderador_only_configures_weights_and_comparison_ranks(app, admin_client, payload):
    with app.app_context():
        add_rankable(payload, "LOW-INCOME", 10000, 5)
        add_rankable(payload, "HIGH-INCOME", 30000, 10)

    assert form(
        admin_client,
        "/admin/ponderador",
        {"income_weight": "100", "schooling_weight": "0"},
    ).status_code == 302
    with app.app_context():
        settings = PonderadorSettings.query.one()
        assert settings.income_weight == Decimal("100.00")
        assert settings.schooling_weight == Decimal("0.00")
        assert ActivityLog.query.filter_by(action="admin:update_ponderador_weights").count() == 1

    ponderador = admin_client.get("/admin/ponderador")
    assert ponderador.status_code == 200
    assert "Ingreso mensual conviviente (%)" in ponderador.text
    assert "Promedio de escolaridad (%)" in ponderador.text
    assert 'name="income_weight"' in ponderador.text and 'value="100"' in ponderador.text
    assert 'name="schooling_weight"' in ponderador.text and 'value="0"' in ponderador.text
    assert "LOW-INCOME" not in ponderador.text and "HIGH-INCOME" not in ponderador.text
    assert "Ranking ponderado" not in ponderador.text
    assert "inscripciones encontradas" not in ponderador.text

    comparison = admin_client.get("/admin/comparison")
    assert comparison.status_code == 200
    assert "Ranking ponderado" in comparison.text
    assert "100.00% ingreso / 0.00% escolaridad" in comparison.text
    assert comparison.text.index("LOW-INCOME") < comparison.text.index("HIGH-INCOME")

    rows = csv_rows(admin_client.get("/admin/comparison/ranking.csv"))
    assert [row["Código"] for row in rows] == ["LOW-INCOME", "HIGH-INCOME"]
    assert rows[0]["Posición"] == "1"
    assert rows[0]["Ingreso mensual conviviente (por persona)"] == "5000.00"
    assert rows[0]["Promedio de escolaridad"] == "5.00"
    assert rows[0]["Puntaje ingreso (0-100)"] == "100.00"
    assert rows[0]["Puntaje escolaridad (0-100)"] == "0.00"
    assert rows[0]["Puntaje ponderado (0-100)"] == "100.00"
    assert rows[0]["Ponderación ingreso (%)"] == "100.00"
    assert rows[0]["Ponderación escolaridad (%)"] == "0.00"
    assert rows[1]["Puntaje ponderado (0-100)"] == "0.00"
    with app.app_context():
        assert ActivityLog.query.filter_by(action="admin:export_ponderador_csv").count() == 1

    assert form(
        admin_client,
        "/admin/ponderador",
        {"income_weight": "0", "schooling_weight": "100"},
    ).status_code == 302
    rows = csv_rows(admin_client.get("/admin/comparison/ranking.csv"))
    assert [row["Código"] for row in rows] == ["HIGH-INCOME", "LOW-INCOME"]
    assert rows[0]["Puntaje ponderado (0-100)"] == "100.00"
    assert rows[0]["Ponderación ingreso (%)"] == "0.00"
    assert rows[0]["Ponderación escolaridad (%)"] == "100.00"
    comparison = admin_client.get("/admin/comparison")
    assert "0.00% ingreso / 100.00% escolaridad" in comparison.text
    assert comparison.text.index("HIGH-INCOME") < comparison.text.index("LOW-INCOME")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"income_weight": "60", "schooling_weight": "30"}, "deben sumar 100%"),
        ({"income_weight": "101", "schooling_weight": "0"}, "entre 0 y 100"),
        ({"income_weight": "abc", "schooling_weight": "100"}, "debe ser un número"),
        ({"income_weight": "", "schooling_weight": "100"}, "Ingresá el porcentaje de ingreso"),
    ],
)
def test_ponderador_rejects_invalid_weights(app, admin_client, data, message):
    response = form(admin_client, "/admin/ponderador", data)
    assert response.status_code == 200
    assert message in response.text
    with app.app_context():
        assert PonderadorSettings.query.count() == 0


def test_ponderador_requires_csrf_and_ignores_comparison_filters(admin_client):
    response = admin_client.post(
        "/admin/ponderador",
        data={"income_weight": "50", "schooling_weight": "50"},
    )
    assert response.status_code == 400
    response = form(
        admin_client,
        "/admin/ponderador?home=minas&type=new",
        {"income_weight": "70", "schooling_weight": "30"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/admin/ponderador")


def test_ranking_filters_and_excludes_incomplete_from_comparison(app, admin_client, payload):
    with app.app_context():
        add_rankable(payload, "MINAS-NEW", 10000, 8, home="minas", kind="new")
        add_rankable(payload, "MINAS-RENEW", 20000, 9, home="minas", kind="renewal")
        add_rankable(payload, "MONTEVIDEO", 15000, 7, home="montevideo", kind="new")
        add_rankable(payload, "INCOMPLETE", 10000, "", home="minas", kind="new")

    query = "home=minas&type=new&year=2026"
    page = admin_client.get("/admin/comparison?" + query)
    assert page.status_code == 200
    assert "Ranking ponderado" in page.text
    assert "MINAS-NEW" in page.text
    assert "INCOMPLETE" in page.text
    assert "Solicitudes no ponderables" in page.text
    assert "MINAS-RENEW" not in page.text
    assert "MONTEVIDEO" not in page.text

    ponderador = admin_client.get("/admin/ponderador")
    assert "MINAS-NEW" not in ponderador.text
    assert "INCOMPLETE" not in ponderador.text

    rows = csv_rows(admin_client.get("/admin/comparison/ranking.csv?" + query))
    assert [row["Código"] for row in rows] == ["MINAS-NEW"]

    rows = csv_rows(admin_client.get("/admin/comparison/ranking.csv?home=minas&home=montevideo&type=new"))
    assert {row["Código"] for row in rows} == {"MINAS-NEW", "MONTEVIDEO"}

    rows = csv_rows(admin_client.get("/admin/comparison/ranking.csv"))
    assert {row["Código"] for row in rows} == {"MINAS-NEW", "MINAS-RENEW", "MONTEVIDEO"}

    rows = csv_rows(admin_client.get("/admin/ponderador/export.csv"))
    assert {row["Código"] for row in rows} == {"MINAS-NEW", "MINAS-RENEW", "MONTEVIDEO"}


def test_ranking_keeps_zero_income_and_zero_average_as_valid_values(app, admin_client, payload):
    payload["householdMembers"] = []
    payload["household"]["livesAlone"] = True
    payload["health"]["currentlyWorks"] = False
    payload["studies"]["average"] = 0
    with app.app_context():
        add_application(payload, code="ZERO-VALUES")

    rows = csv_rows(admin_client.get("/admin/comparison/ranking.csv"))
    assert [row["Código"] for row in rows] == ["ZERO-VALUES"]
    assert rows[0]["Ingreso mensual conviviente (por persona)"] == "0.00"
    assert rows[0]["Promedio de escolaridad"] == "0.00"
    assert rows[0]["Puntaje ponderado (0-100)"] == "100.00"
    assert rows[0]["Personas en el hogar (incluye aspirante)"] == "1"


def test_ranking_ties_and_csv_formula_protection(app, admin_client, payload):
    with app.app_context():
        add_rankable(payload, "FIRST-TIE", 10000, 6, name="=FORMULA")
        add_rankable(payload, "SECOND-TIE", 20000, 10, name="Otra Persona")

    rows = csv_rows(admin_client.get("/admin/comparison/ranking.csv"))
    assert [row["Código"] for row in rows] == ["FIRST-TIE", "SECOND-TIE"]
    assert all(row["Puntaje ponderado (0-100)"] == "50.00" for row in rows)
    assert rows[0]["Nombre"] == "'=FORMULA"
    assert rows[0]["Posición"] == "1"
    assert rows[1]["Posición"] == "2"
