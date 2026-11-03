from datetime import date


def calculate_age(birth_date: date) -> int:
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def required_document_types(payload: dict) -> set[str]:
    request = payload["request"]
    health = payload["health"]
    socioeconomic = payload["socioeconomic"]
    members = payload["householdMembers"]
    required = {
        "applicant_id",
        "address",
        "school_records",
        "health_card",
        "ute_receipt",
        "passport_photo",
    }
    affidavit_statuses = {"self_employed", "informal", "seasonal"}
    if members:
        required.add("household_ids")
    if (
        health["currentlyWorks"]
        or health["receivesScholarship"]
        or any((parent.get("childSupportIncome") or 0) > 0 for parent in payload.get("outsideParents", []))
        or any(
            member["employmentStatus"] in {"formal_employee", "retired"}
            or (member["employmentStatus"] not in affidavit_statuses and (member.get("monthlyIncome") or 0) > 0)
            for member in members
        )
    ):
        required.add("income_proofs")
    if socioeconomic["motorcycle"]["owns"] or socioeconomic["car"]["owns"]:
        required.add("transit_certificate")
    if any(member["employmentStatus"] in {"self_employed", "informal", "seasonal"} for member in members):
        required.add("income_affidavits")
    if any(member["employmentStatus"] == "unemployed" and member["age"] >= 18 for member in members):
        required.add("unemployment_affidavits")
    if health["hasChronicDisease"] or health["takesControlledMedication"]:
        required.add("medical_certificate")
    housing = socioeconomic["housingStatus"]
    if housing == "tenant":
        required.add("rent_receipt")
    elif housing == "owner":
        required.add("property_tax")
    elif housing == "promissory_buyer":
        required.add("installment_receipt")
    if socioeconomic["rural"]["ownsField"] or socioeconomic["rural"]["leasesField"]:
        required.add("dicose")
    if request["type"] == "renewal" and request["sameHome"]:
        required -= {"applicant_id", "address", "school_records", "ute_receipt"}
    return required
