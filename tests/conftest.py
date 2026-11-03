import pytest
from cryptography.fernet import Fernet

from app import create_app
from app.config import Config
from app.extensions import db


class TestConfig(Config):
    TESTING = True
    APPLICATION_YEAR = 2026
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = "test-secret-key-with-at-least-32-characters"
    JWT_SECRET_KEY = "test-jwt-secret-key-with-at-least-32-characters"
    DATA_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    FILE_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    RATELIMIT_STORAGE_URI = "memory://"
    CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
    MINIO_ENDPOINT = "minio:9000"
    MINIO_ACCESS_KEY = "test"
    MINIO_SECRET_KEY = "test-secret"
    MINIO_SECURE = False
    FORCE_HTTPS = False
    TRUST_PROXY = False
    MAIL_SUPPRESS_SEND = True
    MAIL_DEFAULT_SENDER = "test@example.uy"


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def payload():
    return {
        "contactEmail": "aspirante@example.uy",
        "request": {"home": "minas", "type": "new", "renewalYear": "", "sameHome": None},
        "applicant": {
            "fullName": "Persona Aspirante",
            "nationalId": "4.567.890-5",
            "birthDate": "2010-01-01",
            "address": "Ruta 8 km 1",
            "locality": "Minas",
            "phone": "44421234",
            "ownMobile": "",
            "responsibleMobile": "099123456",
        },
        "studies": {
            "grade": "5to",
            "institution": "Liceo Departamental",
            "previousInstitution": "",
            "average": "",
            "hasPendingSubjects": False,
            "pendingSubjectsCount": "",
        },
        "health": {
            "hasDisability": False,
            "dailyLivingSupports": "",
            "healthCenter": "ASSE",
            "hasChronicDisease": False,
            "chronicDisease": "",
            "inTreatment": None,
            "takesControlledMedication": False,
            "controlledMedication": "",
            "hasNeurodevelopmentDiagnosis": False,
            "hasAllergy": False,
            "allergy": "",
            "emergencyName": "Persona Responsable",
            "emergencyPhone": "",
            "emergencyMobile": "099111222",
            "receivesScholarship": False,
            "scholarship": "",
            "currentlyWorks": False,
            "workplace": "",
            "monthlySalary": "",
        },
        "household": {"livesWithMother": True, "livesWithFather": True},
        "householdMembers": [
            {
                "id": "member-1",
                "surnames": "Apellido",
                "names": "Nombre",
                "relationship": "Madre",
                "age": 40,
                "occupation": "Docente",
                "monthlyIncome": 45000,
                "employmentStatus": "formal_employee",
                "nationalId": "",
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
            "notes": "",
        },
        "documents": [],
        "acceptedDeclaration": True,
    }
