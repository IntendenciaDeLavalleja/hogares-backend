import re
from datetime import date

from flask import current_app
from marshmallow import RAISE, Schema, ValidationError, fields, pre_load, validate, validates_schema

PHONE_PATTERN = re.compile(r"^\d{6,20}$")
CI_PATTERN = re.compile(r"^[0-9.-]+$")


def valid_uruguayan_id(value: str) -> None:
    digits = re.sub(r"\D", "", value)
    if len(digits) not in {7, 8}:
        raise ValidationError("La cédula debe contener 7 u 8 dígitos.")
    normalized = digits.zfill(8)
    factors = (2, 9, 8, 7, 6, 3, 4)
    verifier = (10 - sum(int(normalized[index]) * factor for index, factor in enumerate(factors)) % 10) % 10
    if verifier != int(normalized[7]):
        raise ValidationError("El dígito verificador de la cédula no es válido.")


def valid_optional_uruguayan_id(value: str) -> None:
    if value:
        valid_uruguayan_id(value)


def valid_person_name(value: str) -> None:
    if any(character.isdigit() for character in value) or "<" in value or ">" in value:
        raise ValidationError("El nombre no puede contener números ni etiquetas.")


class NullableInteger(fields.Integer):
    def deserialize(self, value, attr=None, data=None, **kwargs):
        return None if value == "" else super().deserialize(value, attr, data, **kwargs)


class NullableFloat(fields.Float):
    def deserialize(self, value, attr=None, data=None, **kwargs):
        return None if value == "" else super().deserialize(value, attr, data, **kwargs)


class StrictSchema(Schema):
    class Meta:
        unknown = RAISE


class RequestSchema(StrictSchema):
    home = fields.String(required=True, validate=validate.OneOf(["minas", "mariscala", "montevideo"]))
    type = fields.String(required=True, validate=validate.OneOf(["new", "renewal"]))
    renewalYear = NullableInteger(load_default=None, allow_none=True, validate=validate.Range(min=2000, max=2100))
    sameHome = fields.Boolean(load_default=None, allow_none=True)

    @validates_schema
    def validate_renewal(self, data, **kwargs):
        if data.get("renewalYear") and data["renewalYear"] > current_app.config["APPLICATION_YEAR"]:
            raise ValidationError("El año de ingreso no puede superar la convocatoria.", field_name="renewalYear")
        if data.get("type") == "renewal" and (data.get("renewalYear") is None or data.get("sameHome") is None):
            raise ValidationError(
                "El año de ingreso y la continuidad en el hogar son obligatorios para renovaciones.",
                field_name="renewalYear",
            )


class ApplicantSchema(StrictSchema):
    fullName = fields.String(required=True, validate=[validate.Length(min=3, max=180), valid_person_name])
    nationalId = fields.String(
        required=True, validate=[validate.Length(min=7, max=12), validate.Regexp(CI_PATTERN), valid_uruguayan_id]
    )
    birthDate = fields.Date(required=True)
    address = fields.String(required=True, validate=validate.Length(min=3, max=255))
    locality = fields.String(required=True, validate=validate.Length(min=2, max=120))
    phone = fields.String(required=True, validate=validate.Regexp(PHONE_PATTERN))
    ownMobile = fields.String(load_default="", validate=validate.Regexp(r"^$|^\d{6,20}$"))
    responsibleMobile = fields.String(load_default="", validate=validate.Regexp(r"^$|^\d{6,20}$"))

    @validates_schema
    def validate_applicant(self, data, **kwargs):
        if data.get("birthDate") and data["birthDate"] > date.today():
            raise ValidationError("La fecha de nacimiento no puede ser futura.", field_name="birthDate")
        if data.get("birthDate") and (date.today() - data["birthDate"]).days > 121 * 366:
            raise ValidationError("La fecha de nacimiento está fuera del rango permitido.", field_name="birthDate")


class StudiesSchema(StrictSchema):
    grade = fields.String(required=True, validate=validate.Length(min=1, max=100))
    institution = fields.String(required=True, validate=validate.Length(min=2, max=180))
    previousInstitution = fields.String(load_default="", validate=validate.Length(max=180))
    average = NullableFloat(load_default=None, allow_none=True, validate=validate.Range(min=0, max=12))
    hasPendingSubjects = fields.Boolean(required=True)
    pendingSubjectsCount = NullableInteger(load_default=None, allow_none=True, validate=validate.Range(min=1, max=100))

    @validates_schema
    def validate_pending(self, data, **kwargs):
        if data.get("hasPendingSubjects") and data.get("pendingSubjectsCount") is None:
            raise ValidationError(
                "La cantidad de materias pendientes es obligatoria.", field_name="pendingSubjectsCount"
            )


class HealthSchema(StrictSchema):
    healthCenter = fields.String(required=True, validate=validate.Length(min=2, max=180))
    hasDisability = fields.Boolean(required=True)
    dailyLivingSupports = fields.String(load_default="", validate=validate.Length(max=2000))
    hasChronicDisease = fields.Boolean(required=True)
    chronicDisease = fields.String(load_default="", validate=validate.Length(max=500))
    inTreatment = fields.Boolean(load_default=None, allow_none=True)
    takesControlledMedication = fields.Boolean(required=True)
    controlledMedication = fields.String(load_default="", validate=validate.Length(max=500))
    hasNeurodevelopmentDiagnosis = fields.Boolean(required=True)
    hasAllergy = fields.Boolean(required=True)
    allergy = fields.String(load_default="", validate=validate.Length(max=500))
    emergencyName = fields.String(required=True, validate=validate.Length(min=2, max=180))
    emergencyPhone = fields.String(load_default="", validate=validate.Regexp(r"^$|^\d{6,20}$"))
    emergencyMobile = fields.String(required=True, validate=validate.Regexp(PHONE_PATTERN))
    receivesScholarship = fields.Boolean(required=True)
    scholarship = fields.String(load_default="", validate=validate.Length(max=250))
    currentlyWorks = fields.Boolean(required=True)
    workplace = fields.String(load_default="", validate=validate.Length(max=180))
    monthlySalary = NullableFloat(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000_000))

    @validates_schema
    def validate_conditionals(self, data, **kwargs):
        errors = {}
        if data.get("hasDisability") and not data.get("dailyLivingSupports", "").strip():
            errors["dailyLivingSupports"] = ["Describa los apoyos necesarios o indique que no necesita apoyos."]
        if not data.get("hasDisability"):
            data["dailyLivingSupports"] = ""
        if data.get("hasChronicDisease") and not data.get("chronicDisease"):
            errors["chronicDisease"] = ["Especifique la enfermedad crónica."]
        if data.get("hasChronicDisease") and data.get("inTreatment") is None:
            errors["inTreatment"] = ["Indique si se encuentra en tratamiento."]
        if data.get("takesControlledMedication") and not data.get("controlledMedication"):
            errors["controlledMedication"] = ["Especifique la medicación."]
        if data.get("hasAllergy") and not data.get("allergy"):
            errors["allergy"] = ["Especifique la alergia."]
        if data.get("receivesScholarship") and not data.get("scholarship"):
            errors["scholarship"] = ["Especifique la beca."]
        if data.get("currentlyWorks") and (not data.get("workplace") or data.get("monthlySalary") is None):
            errors["workplace"] = ["Lugar de trabajo y remuneración son obligatorios."]
        if errors:
            raise ValidationError(errors)


class HouseholdMemberSchema(StrictSchema):
    id = fields.String(required=True, validate=validate.Length(min=1, max=64))
    surnames = fields.String(required=True, validate=[validate.Length(min=1, max=120), valid_person_name])
    names = fields.String(required=True, validate=[validate.Length(min=1, max=120), valid_person_name])
    relationship = fields.String(required=True, validate=validate.Length(min=1, max=80))
    age = fields.Integer(required=True, validate=validate.Range(min=0, max=120))
    occupation = fields.String(load_default="", validate=validate.Length(max=180))
    monthlyIncome = NullableFloat(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000_000))
    employmentStatus = fields.String(
        required=True,
        validate=validate.OneOf(
            ["formal_employee", "retired", "self_employed", "informal", "seasonal", "unemployed", "student", "other"]
        ),
    )
    nationalId = fields.String(
        load_default="", validate=[validate.Regexp(r"^$|^[0-9.-]{7,12}$"), valid_optional_uruguayan_id]
    )
    address = fields.String(load_default="", validate=validate.Length(max=255))


class OutsideParentSchema(StrictSchema):
    id = fields.String(required=True, validate=validate.Length(min=1, max=64))
    role = fields.String(required=True, validate=validate.OneOf(["mother", "father"]))
    surnames = fields.String(required=True, validate=[validate.Length(min=1, max=120), valid_person_name])
    name = fields.String(required=True, validate=[validate.Length(min=1, max=120), valid_person_name])
    relationship = fields.String(required=True, validate=validate.Length(min=1, max=80))
    age = fields.Integer(required=True, validate=validate.Range(min=0, max=120))
    occupation = fields.String(load_default="", validate=validate.Length(max=180))
    childSupportIncome = NullableFloat(
        load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000_000)
    )
    nationalId = fields.String(
        load_default="", validate=[validate.Regexp(r"^$|^[0-9.-]{7,12}$"), valid_optional_uruguayan_id]
    )


class HouseholdSchema(StrictSchema):
    livesAlone = fields.Boolean(load_default=False)
    motherSituation = fields.String(
        required=True, validate=validate.OneOf(["lives_with", "outside", "deceased", "unknown", "no_contact"])
    )
    fatherSituation = fields.String(
        required=True, validate=validate.OneOf(["lives_with", "outside", "deceased", "unknown", "no_contact"])
    )
    livesWithMother = fields.Boolean(required=True)
    livesWithFather = fields.Boolean(required=True)

    @pre_load
    def normalize_parent_situations(self, data, **kwargs):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for role, suffix in [("mother", "Mother"), ("father", "Father")]:
            situation_key, lives_key = f"{role}Situation", f"livesWith{suffix}"
            if situation_key not in data and isinstance(data.get(lives_key), bool):
                data[situation_key] = "lives_with" if data[lives_key] else "outside"
            if data.get(situation_key) in {"lives_with", "outside", "deceased", "unknown", "no_contact"}:
                data[lives_key] = data[situation_key] == "lives_with"
        return data


class VehicleSchema(StrictSchema):
    owns = fields.Boolean(required=True)
    quantity = NullableInteger(load_default=None, allow_none=True, validate=validate.Range(min=1, max=100))
    brand = fields.String(load_default="", validate=validate.Length(max=100))
    year = NullableInteger(load_default=None, allow_none=True, validate=validate.Range(min=1900, max=2100))

    @validates_schema
    def validate_owned(self, data, **kwargs):
        if data.get("year") and data["year"] > current_app.config["APPLICATION_YEAR"]:
            raise ValidationError("El año del vehículo no puede superar la convocatoria.", field_name="year")
        if data.get("owns") and (data.get("quantity") is None or not data.get("brand") or data.get("year") is None):
            raise ValidationError("Cantidad, marca y año son obligatorios.")


class RuralSchema(StrictSchema):
    ownsField = fields.Boolean(required=True)
    leasesField = fields.Boolean(required=True)
    leaseAmount = NullableFloat(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000_000))

    @validates_schema
    def validate_lease(self, data, **kwargs):
        if data.get("leasesField") and data.get("leaseAmount") is None:
            raise ValidationError("El monto del arrendamiento es obligatorio.", field_name="leaseAmount")


class SocioeconomicSchema(StrictSchema):
    housingStatus = fields.String(
        required=True, validate=validate.OneOf(["owner", "usufruct", "succession", "tenant", "promissory_buyer"])
    )
    rentAmount = NullableFloat(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000_000))
    installmentAmount = NullableFloat(
        load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000_000)
    )
    motorcycle = fields.Nested(VehicleSchema, required=True)
    car = fields.Nested(VehicleSchema, required=True)
    rural = fields.Nested(RuralSchema, required=True)
    notes = fields.String(load_default="", validate=validate.Length(max=5000))

    @validates_schema
    def validate_housing(self, data, **kwargs):
        if data.get("housingStatus") == "tenant" and data.get("rentAmount") is None:
            raise ValidationError("El monto de alquiler es obligatorio.", field_name="rentAmount")
        if data.get("housingStatus") == "promissory_buyer" and data.get("installmentAmount") is None:
            raise ValidationError("El monto de cuota es obligatorio.", field_name="installmentAmount")


class DocumentMetadataSchema(StrictSchema):
    type = fields.String(required=True)
    name = fields.String(required=True)
    status = fields.String(required=True)
    referenceId = fields.String(load_default=None, allow_none=True)
    mimeType = fields.String(load_default=None, allow_none=True)
    originalSize = fields.Integer(load_default=None, allow_none=True)
    optimizedSize = fields.Integer(load_default=None, allow_none=True)
    width = fields.Integer(load_default=None, allow_none=True)
    height = fields.Integer(load_default=None, allow_none=True)
    wasOptimized = fields.Boolean(load_default=None, allow_none=True)
    optimizedFileName = fields.String(load_default=None, allow_none=True)


class ApplicationSchema(StrictSchema):
    contactEmail = fields.Email(
        required=True,
        validate=validate.Length(max=254),
        error_messages={"required": "El correo electrónico es obligatorio.", "invalid": "Ingresá un correo válido."},
    )
    request = fields.Nested(RequestSchema, required=True)
    applicant = fields.Nested(ApplicantSchema, required=True)
    studies = fields.Nested(StudiesSchema, required=True)
    health = fields.Nested(HealthSchema, required=True)
    household = fields.Nested(HouseholdSchema, required=True)
    householdMembers = fields.List(
        fields.Nested(HouseholdMemberSchema), required=True, validate=validate.Length(max=30)
    )
    outsideParents = fields.List(fields.Nested(OutsideParentSchema), load_default=list, validate=validate.Length(max=2))
    socioeconomic = fields.Nested(SocioeconomicSchema, required=True)
    documents = fields.List(fields.Nested(DocumentMetadataSchema), load_default=list, validate=validate.Length(max=30))
    acceptedDeclaration = fields.Boolean(required=True, validate=validate.Equal(True))

    @validates_schema
    def validate_household_parents(self, data, **kwargs):
        household = data.get("household", {})
        members = data.get("householdMembers", [])
        if household.get("livesAlone") and (
            members or household.get("livesWithMother") or household.get("livesWithFather")
        ):
            raise ValidationError("Si vive solo/a, no puede declarar convivientes.", field_name="householdMembers")
        if not household.get("livesAlone") and not members:
            raise ValidationError(
                "Registre al menos una persona conviviente o indique que vive solo/a.", field_name="householdMembers"
            )
        roles = [parent["role"] for parent in data.get("outsideParents", [])]
        if len(roles) != len(set(roles)):
            raise ValidationError("No puede repetir madre o padre fuera del núcleo.", field_name="outsideParents")
        for role in ("mother", "father"):
            situation = household.get(f"{role}Situation")
            if situation == "outside" and role not in roles:
                raise ValidationError(
                    "Complete los datos del progenitor que indicó que puede aportar.", field_name="outsideParents"
                )
            if situation != "outside" and role in roles:
                raise ValidationError(
                    "La ficha del progenitor no coincide con la situación declarada.", field_name="outsideParents"
                )


application_schema = ApplicationSchema()
