import csv
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import StringIO

from flask import abort, current_app

from ..models import Application
from .encryption import EncryptionService


def suggested_archive_name(args):
    year = str(args.get("year", "")).strip()
    if not year:
        date_from = str(args.get("date_from", "")).strip()
        date_to = str(args.get("date_to", "")).strip()
        if len(date_from) >= 4 and date_from[:4].isdigit():
            year = date_from[:4]
        elif len(date_to) >= 4 and date_to[:4].isdigit():
            year = date_to[:4]
    year = year or str(current_app.config["APPLICATION_YEAR"])
    kind = args.get("type", "")
    if kind == "new":
        return f"Convocatoria Primera {year}"
    if kind == "renewal":
        return f"Convocatoria Renovación {year}"
    return f"Convocatoria {year}"


def filtered_applications(args, *, archived=False):
    query = Application.query
    if archived:
        query = query.filter(Application.archived_at.is_not(None))
    else:
        query = query.filter(Application.archived_at.is_(None))
    for parameter, column, allowed in (
        ("status", Application.status, {"draft", "submitted"}),
        ("home", Application.home, {"minas", "mariscala", "montevideo"}),
        ("type", Application.application_type, {"new", "renewal"}),
    ):
        value = args.get(parameter, "")
        if value:
            if value not in allowed:
                abort(400, description="Filtro inválido.")
            query = query.filter(column == value)
    year = args.get("year", "")
    if year:
        if not str(year).isdigit() or not 2000 <= int(year) <= 2100:
            abort(400, description="Año inválido.")
        query = query.filter(Application.application_year == int(year))
    date_from = args.get("date_from", "").strip()
    date_to = args.get("date_to", "").strip()
    from_date = _filter_date(date_from, "desde") if date_from else None
    to_date = _filter_date(date_to, "hasta") if date_to else None
    if from_date and to_date and from_date > to_date:
        abort(400, description="El rango de fechas es inválido.")
    if from_date:
        query = query.filter(Application.submitted_at >= datetime.combine(from_date, time.min))
    if to_date:
        query = query.filter(Application.submitted_at <= datetime.combine(to_date, time.max))
    search = args.get("q", "").strip()[:24]
    if search:
        query = query.filter(Application.tracking_code.contains(search, autoescape=True))
    return query


def _filter_date(value, label):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        abort(400, description=f"Fecha {label} inválida.")


def archived_applications(args):
    query = filtered_applications(args, archived=True)
    archive = args.get("archive", "").strip()[:120]
    if archive:
        query = query.filter(Application.archive_name == archive)
    return query


def application_data(item):
    data = EncryptionService.decrypt(item.payload_encrypted)
    if item.payload_encrypted and data is None:
        abort(503, description="No se pudo descifrar una solicitud. No se generó una exportación incompleta.")
    return data or {}


def household_income(data):
    """Only sum cohabiting members; never infer missing incomes or double-count applicant salary."""
    members = data.get("householdMembers", [])
    total = Decimal("0")
    missing = 0
    for member in members:
        value = member.get("monthlyIncome")
        try:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount < 0:
                raise InvalidOperation
            total += amount
        except (InvalidOperation, ValueError):
            missing += 1
    return (total if members and not missing else None), missing


def nonnegative_amount(value):
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and 0 <= amount <= 1_000_000_000 else None
    except (InvalidOperation, ValueError):
        return None


def household_summary(data):
    """Describe declared income per resident, including the applicant once and no outside parents."""
    members = data.get("householdMembers", [])
    health = data.get("health", {})
    lives_alone = data.get("household", {}).get("livesAlone")
    residents = len(members) + 1 if members or lives_alone is True else None
    if lives_alone is True and members:
        residents = None
    total = Decimal("0")
    missing = 0
    for member in members:
        amount = nonnegative_amount(member.get("monthlyIncome"))
        if amount is None:
            missing += 1
        else:
            total += amount
    if health.get("currentlyWorks") is True:
        salary = nonnegative_amount(health.get("monthlySalary"))
        if salary is None:
            missing += 1
        else:
            total += salary
    elif health.get("currentlyWorks") is not False:
        missing += 1
    average = nonnegative_amount(data.get("studies", {}).get("average"))
    if average is not None and average > 12:
        average = None
    complete = residents is not None and missing == 0
    return {
        "residents": residents,
        "total_income": total if complete else None,
        "income_per_person": total / residents if complete else None,
        "missing_income": missing,
        "average": average,
    }


def selected_values(args, name, allowed):
    values = args.getlist(name) if hasattr(args, "getlist") else args.get(name, [])
    if isinstance(values, str):
        values = [values]
    values = list(dict.fromkeys(value for value in values if value != ""))
    if any(value not in allowed for value in values):
        abort(400, description="Filtro inválido.")
    return values or list(allowed)


def review_applications(args):
    homes = selected_values(args, "home", ("minas", "mariscala", "montevideo"))
    kinds = selected_values(args, "type", ("new", "renewal"))
    query = filtered_applications(
        {
            "status": "submitted",
            "year": args.get("year", ""),
            "q": args.get("q", ""),
            "date_from": args.get("date_from", ""),
            "date_to": args.get("date_to", ""),
        }
    )
    return query.filter(Application.home.in_(homes), Application.application_type.in_(kinds)), homes, kinds


def comparison_row(item):
    data = application_data(item)
    return {
        "item": item,
        "name": data.get("applicant", {}).get("fullName", "Sin informar"),
        "institution": data.get("studies", {}).get("institution", "Sin informar"),
        **household_summary(data),
    }


def decimal_cell(value):
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f") if value is not None else ""


def export_review_csv(items):
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "Código",
            "Hogar",
            "Tipo",
            "Año",
            "Fecha de inscripción (UTC)",
            "Nombre",
            "Institución",
            "Promedio escolar declarado",
            "Personas en el hogar (incluye aspirante)",
            "Ingreso mensual total declarado",
            "Ingreso mensual por persona",
            "Ingresos sin informar",
            "Datos completos",
        ]
    )
    for item in items:
        row = comparison_row(item)
        writer.writerow(
            [
                csv_cell(value)
                for value in (
                    item.tracking_code,
                    (item.home or "").capitalize(),
                    {"new": "Primera inscripción", "renewal": "Renovación"}.get(item.application_type, ""),
                    item.application_year,
                    item.submitted_at.isoformat() if item.submitted_at else "",
                    row["name"],
                    row["institution"],
                    decimal_cell(row["average"]),
                    row["residents"],
                    decimal_cell(row["total_income"]),
                    decimal_cell(row["income_per_person"]),
                    row["missing_income"],
                    "Sí" if row["income_per_person"] is not None and row["average"] is not None else "No",
                )
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def csv_cell(value):
    text = "" if value is None else str(value)
    # Spreadsheet formulas may execute even inside a quoted CSV cell.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        text = "'" + text
    return text


def export_csv(items, *, include_archive=False):
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    headers = [
        "Código",
        "Estado",
        "Hogar",
        "Tipo",
        "Año",
        "Nombre",
        "Cédula",
        "Correo",
        "Teléfono",
        "Institución",
        "Promedio declarado",
        "Ingreso mensual núcleo conviviente",
        "Certificado recibido",
        "Fecha recepción certificado (UTC)",
        "Fecha inscripción (UTC)",
    ]
    if include_archive:
        headers += ["Convocatoria / archivo", "Fecha de archivo (UTC)"]
    writer.writerow(headers)
    for item in items:
        data = application_data(item)
        applicant, studies = data.get("applicant", {}), data.get("studies", {})
        income, _missing = household_income(data)
        values = [
            item.tracking_code,
            "Enviada" if item.status == "submitted" else "Borrador",
            item.home,
            {"new": "Primera inscripción", "renewal": "Renovación"}.get(item.application_type, "Sin definir"),
            item.application_year,
            applicant.get("fullName"),
            applicant.get("nationalId"),
            data.get("contactEmail"),
            applicant.get("phone"),
            studies.get("institution"),
            studies.get("average"),
            income,
            "Sí" if item.good_conduct_received_at else "No",
            item.good_conduct_received_at.isoformat() if item.good_conduct_received_at else "",
            item.submitted_at.isoformat() if item.submitted_at else "",
        ]
        if include_archive:
            values += [
                item.archive_name or "",
                item.archived_at.isoformat() if item.archived_at else "",
            ]
        writer.writerow([csv_cell(value) for value in values])
    return output.getvalue().encode("utf-8-sig")
