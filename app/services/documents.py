import hashlib
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
DOCUMENT_TYPES = {
    "applicant_id",
    "household_ids",
    "address",
    "good_conduct",
    "income_proofs",
    "income_affidavits",
    "unemployment_affidavits",
    "ute_receipt",
    "passport_photo",
    "school_records",
    "health_card",
    "housing",
    "transit_certificate",
    "dicose",
    "medical_certificate",
    "rent_receipt",
    "property_tax",
    "installment_receipt",
}


class DocumentProcessingError(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedDocument:
    content: bytes
    original_name: str
    stored_name: str
    media_type: str
    checksum: str
    width: int | None = None
    height: int | None = None


def _read_limited(upload: FileStorage, limit: int) -> bytes:
    content = upload.stream.read(limit + 1)
    if len(content) > limit:
        raise DocumentProcessingError("El archivo supera el tamaño máximo permitido.")
    if not content:
        raise DocumentProcessingError("El archivo está vacío.")
    return content


def _process_image(upload: FileStorage) -> ProcessedDocument:
    content = _read_limited(upload, current_app.config["MAX_IMAGE_SIZE"])
    Image.MAX_IMAGE_PIXELS = current_app.config["MAX_IMAGE_PIXELS"]
    try:
        with Image.open(BytesIO(content)) as source:
            if source.width * source.height > current_app.config["MAX_IMAGE_PIXELS"]:
                raise DocumentProcessingError("La imagen tiene dimensiones demasiado grandes.")
            source.verify()
        with Image.open(BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            quality = 80
            result = b""
            while quality >= 50:
                output = BytesIO()
                image.save(output, format="WEBP", quality=quality, method=6, exif=b"")
                result = output.getvalue()
                if len(result) <= current_app.config["MAX_IMAGE_SIZE"]:
                    break
                quality -= 10
            if len(result) > current_app.config["MAX_IMAGE_SIZE"]:
                raise DocumentProcessingError("La imagen sigue siendo demasiado pesada luego de optimizarla.")
            original = secure_filename(upload.filename or "imagen")[:200] or "imagen"
            stored = f"{Path(original).stem[:180] or 'imagen'}.webp"
            return ProcessedDocument(
                result, original, stored, "image/webp", hashlib.sha256(result).hexdigest(), image.width, image.height
            )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DocumentProcessingError("El archivo no es una imagen válida o segura.") from exc


def _process_pdf(upload: FileStorage) -> ProcessedDocument:
    content = _read_limited(upload, current_app.config["MAX_PDF_SIZE"])
    if not content.startswith(b"%PDF-"):
        raise DocumentProcessingError("El archivo no es un PDF válido.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise DocumentProcessingError("No se aceptan PDF protegidos con contraseña.")
        if len(reader.pages) < 1 or len(reader.pages) > 1000:
            raise DocumentProcessingError("El PDF tiene una cantidad de páginas inválida.")
        root_reference = reader.trailer.get("/Root")
        root = root_reference.get_object() if hasattr(root_reference, "get_object") else (root_reference or {})
        names_reference = root.get("/Names")
        names = names_reference.get_object() if hasattr(names_reference, "get_object") else (names_reference or {})
        if root.get("/OpenAction") or root.get("/AA") or names.get("/JavaScript"):
            raise DocumentProcessingError("El PDF contiene acciones no permitidas.")
    except DocumentProcessingError:
        raise
    except Exception as exc:
        raise DocumentProcessingError("El archivo no es un PDF válido.") from exc
    original = secure_filename(upload.filename or "documento.pdf")[:200] or "documento.pdf"
    return ProcessedDocument(
        content,
        original,
        f"{Path(original).stem[:180] or 'documento'}.pdf",
        "application/pdf",
        hashlib.sha256(content).hexdigest(),
    )


def process_document(upload: FileStorage | None) -> ProcessedDocument:
    if upload is None or not upload.filename:
        raise DocumentProcessingError("Debe seleccionar un archivo.")
    media_type = (upload.mimetype or "").lower()
    if media_type in IMAGE_MIME_TYPES:
        return _process_image(upload)
    if media_type == "application/pdf":
        return _process_pdf(upload)
    raise DocumentProcessingError("Formato no permitido. Use JPG, PNG, WebP o PDF.")


def object_key(application_id: str, document_type: str, media_type: str) -> str:
    extension = "webp" if media_type == "image/webp" else "pdf"
    year = current_app.config["APPLICATION_YEAR"]
    return f"applications/{year}/{application_id}/{document_type}/{uuid.uuid4().hex}.{extension}.enc"
