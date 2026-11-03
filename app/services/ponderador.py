import csv
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import StringIO

from ..extensions import db
from ..models import PonderadorSettings
from .admin_applications import application_data, csv_cell, decimal_cell, household_summary

DEFAULT_INCOME_WEIGHT = Decimal("50.00")
DEFAULT_SCHOOLING_WEIGHT = Decimal("50.00")
PERCENT = Decimal("100")
CENT = Decimal("0.01")


def load_ponderador_settings():
    """Devuelve la configuración guardada, o None para usar los valores iniciales."""
    return db.session.get(PonderadorSettings, 1)


def effective_weights(settings):
    if settings is None:
        return DEFAULT_INCOME_WEIGHT, DEFAULT_SCHOOLING_WEIGHT
    return Decimal(settings.income_weight), Decimal(settings.schooling_weight)


def weight_text(value):
    """Muestra 50.00 como 50 y 33.30 como 33.3 en el formulario."""
    text = format(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP), "f")
    return text.rstrip("0").rstrip(".") or "0"


def _percentage(raw, label):
    value = str(raw if raw is not None else "").strip().replace(",", ".")
    if not value:
        raise ValueError(f"Ingresá el porcentaje de {label}.")
    if len(value) > 20:
        raise ValueError(f"El porcentaje de {label} no es válido.")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"El porcentaje de {label} debe ser un número.") from error
    if not amount.is_finite() or amount < 0 or amount > PERCENT:
        raise ValueError(f"El porcentaje de {label} debe estar entre 0 y 100.")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_weights(income_raw, schooling_raw):
    income_weight = _percentage(income_raw, "ingreso mensual conviviente")
    schooling_weight = _percentage(schooling_raw, "promedio de escolaridad")
    if income_weight + schooling_weight != PERCENT:
        raise ValueError("Los porcentajes de ingreso y escolaridad deben sumar 100%.")
    return income_weight, schooling_weight


def _relative_score(value, minimum, maximum, *, reverse=False):
    if maximum == minimum:
        return PERCENT
    if reverse:
        return (maximum - value) / (maximum - minimum) * PERCENT
    return (value - minimum) / (maximum - minimum) * PERCENT


def _missing_reason(summary):
    reasons = []
    if summary["residents"] is None:
        reasons.append("Composición del hogar incompleta")
    if summary["missing_income"]:
        label = "ingreso sin informar" if summary["missing_income"] == 1 else "ingresos sin informar"
        reasons.append(f"{summary['missing_income']} {label}")
    if summary["average"] is None:
        reasons.append("Promedio de escolaridad sin informar")
    return " / ".join(reasons) or "Datos incompletos"


def build_ranking(items, income_weight, schooling_weight):
    """Ordena las solicitudes con datos completos y devuelve también las excluidas.

    El ingreso se puntúa a la inversa (menor ingreso, mayor prioridad) y el promedio
    escolar de forma directa (mayor promedio, mayor puntaje). Ambos puntajes se
    normalizan de 0 a 100 dentro del conjunto filtrado.
    """
    income_weight = Decimal(income_weight)
    schooling_weight = Decimal(schooling_weight)
    ranked, excluded = [], []
    for item in items:
        data = application_data(item)
        summary = household_summary(data)
        row = {
            "item": item,
            "name": data.get("applicant", {}).get("fullName") or "Sin informar",
            "institution": data.get("studies", {}).get("institution") or "Sin informar",
            **summary,
        }
        if row["income_per_person"] is None or row["average"] is None:
            row["reason"] = _missing_reason(summary)
            excluded.append(row)
        else:
            ranked.append(row)

    if ranked:
        incomes = [row["income_per_person"] for row in ranked]
        averages = [row["average"] for row in ranked]
        minimum_income, maximum_income = min(incomes), max(incomes)
        minimum_average, maximum_average = min(averages), max(averages)
        for row in ranked:
            row["income_score"] = _relative_score(
                row["income_per_person"], minimum_income, maximum_income, reverse=True
            ).quantize(CENT, rounding=ROUND_HALF_UP)
            row["schooling_score"] = _relative_score(
                row["average"], minimum_average, maximum_average
            ).quantize(CENT, rounding=ROUND_HALF_UP)
            row["weighted_score"] = (
                (row["income_score"] * income_weight + row["schooling_score"] * schooling_weight) / PERCENT
            ).quantize(CENT, rounding=ROUND_HALF_UP)

        ranked.sort(
            key=lambda row: (
                -row["weighted_score"],
                row["income_per_person"],
                -row["average"],
                row["item"].submitted_at or datetime.min,
                row["item"].id,
            )
        )
        for position, row in enumerate(ranked, start=1):
            row["position"] = position

    return ranked, excluded


def export_ranking_csv(rows, income_weight, schooling_weight):
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "Posición",
            "Código",
            "Hogar",
            "Tipo",
            "Año",
            "Fecha de envío (UTC)",
            "Nombre",
            "Institución",
            "Ingreso mensual conviviente (por persona)",
            "Promedio de escolaridad",
            "Puntaje ingreso (0-100)",
            "Puntaje escolaridad (0-100)",
            "Puntaje ponderado (0-100)",
            "Ponderación ingreso (%)",
            "Ponderación escolaridad (%)",
            "Personas en el hogar (incluye aspirante)",
            "Ingreso mensual total declarado",
            "Datos completos",
        ]
    )
    for row in rows:
        item = row["item"]
        writer.writerow(
            [
                csv_cell(value)
                for value in (
                    row["position"],
                    item.tracking_code or "",
                    (item.home or "").capitalize(),
                    {"new": "Primera inscripción", "renewal": "Renovación"}.get(item.application_type, ""),
                    item.application_year,
                    item.submitted_at.isoformat() if item.submitted_at else "",
                    row["name"],
                    row["institution"],
                    decimal_cell(row["income_per_person"]),
                    decimal_cell(row["average"]),
                    decimal_cell(row["income_score"]),
                    decimal_cell(row["schooling_score"]),
                    decimal_cell(row["weighted_score"]),
                    decimal_cell(income_weight),
                    decimal_cell(schooling_weight),
                    row["residents"],
                    decimal_cell(row["total_income"]),
                    "Sí",
                )
            ]
        )
    return output.getvalue().encode("utf-8-sig")
