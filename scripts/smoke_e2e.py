import json
import os
import sys
import uuid
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image
from pypdf import PdfWriter

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")
if urlparse(BASE_URL).scheme not in {"http", "https"}:
    raise ValueError("La URL del smoke test debe usar HTTP o HTTPS.")


def request_json(
    path: str,
    method: str = "GET",
    payload: dict | None = None,
    token: str | None = None,
    extra_headers: dict | None = None,
):
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Draft-Token"] = token
    headers.update(extra_headers or {})
    request = Request(  # noqa: S310 - BASE_URL is restricted to HTTP(S) above.
        f"{BASE_URL}{path}", data=body if method != "GET" else None, headers=headers, method=method
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - validated HTTP(S) request.
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def upload(path: str, token: str, document_type: str, name: str, media_type: str, content: bytes):
    boundary = f"----hogares-{uuid.uuid4().hex}"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\n{document_type}\r\n'.encode(),
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    request = Request(  # noqa: S310 - BASE_URL is restricted to HTTP(S) above.
        f"{BASE_URL}{path}",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Draft-Token": token},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:  # noqa: S310 - validated HTTP(S) request.
        return response.status, json.loads(response.read())


payload = {
    "contactEmail": os.environ.get("SMOKE_EMAIL", ""),
    "request": {"home": "montevideo", "type": "new", "renewalYear": "", "sameHome": None},
    "applicant": {
        "fullName": "Prueba Integración Hogares",
        "nationalId": "4.567.890-5",
        "birthDate": "2010-01-01",
        "address": "Dirección de prueba 123",
        "locality": "Minas",
        "phone": "44421234",
        "ownMobile": "099123456",
        "responsibleMobile": "099111222",
    },
    "studies": {
        "grade": "5to",
        "institution": "Institución de prueba",
        "previousInstitution": "Centro anterior",
        "average": 9.5,
        "hasPendingSubjects": False,
        "pendingSubjectsCount": "",
    },
    "health": {
        "hasDisability": True,
        "dailyLivingSupports": "Apoyo para desplazarse.",
        "healthCenter": "ASSE",
        "hasChronicDisease": False,
        "chronicDisease": "",
        "inTreatment": None,
        "takesControlledMedication": False,
        "controlledMedication": "",
        "hasNeurodevelopmentDiagnosis": False,
        "hasAllergy": False,
        "allergy": "",
        "emergencyName": "Responsable de prueba",
        "emergencyPhone": "44420000",
        "emergencyMobile": "099111222",
        "receivesScholarship": False,
        "scholarship": "",
        "currentlyWorks": False,
        "workplace": "",
        "monthlySalary": "",
    },
    "household": {"livesAlone": False, "motherSituation": "deceased", "fatherSituation": "unknown"},
    "householdMembers": [
        {
            "id": str(uuid.uuid4()),
            "surnames": "Apellido",
            "names": "Responsable",
            "relationship": "Abuela",
            "age": 65,
            "occupation": "Docente",
            "monthlyIncome": 45000,
            "employmentStatus": "retired",
            "nationalId": "3.456.789-4",
            "address": "",
        }
    ],
    "outsideParents": [],
    "socioeconomic": {
        "housingStatus": "usufruct",
        "rentAmount": "",
        "installmentAmount": "",
        "motorcycle": {"owns": False, "quantity": "", "brand": "", "year": ""},
        "car": {"owns": False, "quantity": "", "brand": "", "year": ""},
        "rural": {"ownsField": False, "leasesField": False, "leaseAmount": ""},
        "notes": "Prueba automática de integración.",
    },
    "documents": [],
    "acceptedDeclaration": True,
}

if not payload["contactEmail"]:
    raise SystemExit("Configurá SMOKE_EMAIL con un correo de pruebas. Este test envía una confirmación.")

status, draft = request_json("/api/applications/drafts", "POST")
assert status == 201, draft
draft_id = draft["id"]
token = draft["token"]

invalid = {**payload, "applicant": {**payload["applicant"], "phone": "no-numérico"}}
status, invalid_response = request_json(f"/api/applications/drafts/{draft_id}/submit", "POST", invalid, token)
assert status == 422 and "fields" in invalid_response, invalid_response

image = Image.new("RGB", (2400, 1400), "white")
image_buffer = BytesIO()
image.save(image_buffer, "WEBP", quality=80)
image_content = image_buffer.getvalue()

pdf_buffer = BytesIO()
writer = PdfWriter()
writer.add_blank_page(width=595, height=842)
writer.write(pdf_buffer)

document_types = [
    "applicant_id",
    "household_ids",
    "address",
    "health_card",
    "ute_receipt",
    "passport_photo",
    "income_proofs",
]
for document_type in document_types:
    status, response = upload(
        f"/api/applications/drafts/{draft_id}/documents",
        token,
        document_type,
        f"{document_type}.webp",
        "image/webp",
        image_content,
    )
    assert status == 201 and response["document"]["mimeType"] == "image/webp", response

status, response = upload(
    f"/api/applications/drafts/{draft_id}/documents",
    token,
    "school_records",
    "escolaridad.pdf",
    "application/pdf",
    pdf_buffer.getvalue(),
)
assert status == 201 and response["document"]["mimeType"] == "application/pdf", response

status, challenge = request_json(f"/api/applications/drafts/{draft_id}/captcha", token=token)
assert status == 200, challenge
left, right = challenge["question"].split(" + ")
headers = {"X-Captcha-Token": challenge["token"], "X-Captcha-Answer": str(int(left) + int(right))}
status, receipt = request_json(f"/api/applications/drafts/{draft_id}/submit", "POST", payload, token, headers)
assert status == 201, receipt
status, verified = request_json(f"/api/applications/receipts/{receipt['trackingCode']}", "GET", token=token)
assert status == 200 and verified["trackingCode"] == receipt["trackingCode"], verified
print(json.dumps({"id": draft_id, "trackingCode": receipt["trackingCode"], "documents": 8, "status": "ok"}))
