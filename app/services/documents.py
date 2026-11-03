import hashlib
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

register_heif_opener(thumbnails=False)
_image_processing_slots = BoundedSemaphore(1)

IMAGE_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "image/heic-sequence", "image/heif-sequence",
}
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
    # Decode one large camera image at a time per worker, not once per HTTP thread.
    with _image_processing_slots:
        return _process_image_in_slot(upload)


def _process_image_in_slot(upload: FileStorage) -> ProcessedDocument:
    content = _read_limited(upload, current_app.config["MAX_IMAGE_INPUT_SIZE"])
    Image.MAX_IMAGE_PIXELS = current_app.config["MAX_IMAGE_PIXELS"]
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP", "HEIF"}:
                raise DocumentProcessingError("Formato no permitido. Use JPG, PNG, WebP, HEIC o PDF.")
            if source.width * source.height > current_app.config["MAX_IMAGE_PIXELS"]:
                raise DocumentProcessingError(
                    "La imagen supera los 40 megapíxeles. Reducí su resolución o adjuntala como PDF."
                )
            source.verify()
        with Image.open(BytesIO(content)) as source:
            source.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
            ImageOps.exif_transpose(source, in_place=True)
            image = source
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
    except DocumentProcessingError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, RuntimeError, Image.DecompressionBombError) as exc:
        raise DocumentProcessingError("El archivo no es una imagen válida o segura.") from exc


def _process_pdf(upload: FileStorage) -> ProcessedDocument:
    content = _read_limited(upload, current_app.config["MAX_PDF_SIZE"])
    if not content.startswith(b"%PDF-"):
        raise DocumentProcessingError("El archivo no es un PDF válido.")
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise DocumentProcessingError("No se aceptan PDF protegidos con contraseña.")
        if len(reader.pages) < 1 or len(reader.pages) > 1000:
            raise DocumentProcessingError("El PDF tiene una cantidad de páginas inválida.")
        root_reference = reader.trailer.get("/Root")
        root = root_reference.get_object() if hasattr(root_reference, "get_object") else (root_reference or {})
        names_reference = root.get("/Names")
        names = names_reference.get_object() if hasattr(names_reference, "get_object") else (names_reference or {})
        open_action = root.get("/OpenAction")
        if hasattr(open_action, "get_object"):
            open_action = open_action.get_object()
        # A local opening view is common in scanned/Office PDFs and does not execute code.
        safe_open_action = (
            isinstance(open_action, ArrayObject)
            or (isinstance(open_action, DictionaryObject) and open_action.get("/S") == "/GoTo")
        )
        if (open_action and not safe_open_action) or root.get("/AA") or names.get("/JavaScript"):
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
    media_type = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "application/x-pdf": "application/pdf"}.get(
        media_type, media_type
    )
    if media_type in {"", "application/octet-stream", "binary/octet-stream"}:
        prefix = upload.stream.read(32)
        upload.stream.seek(0)
        if prefix.startswith(b"%PDF-"):
            media_type = "application/pdf"
        elif prefix.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
            media_type = "image/jpeg"
        elif prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
            media_type = "image/webp"
        elif prefix[4:8] == b"ftyp" and any(
            brand in prefix[8:32] for brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")
        ):
            media_type = "image/heif"
    if media_type in IMAGE_MIME_TYPES:
        return _process_image(upload)
    if media_type == "application/pdf":
        return _process_pdf(upload)
    raise DocumentProcessingError("Formato no permitido. Use JPG, PNG, WebP, HEIC o PDF.")


def object_key(application_id: str, document_type: str, media_type: str) -> str:
    extension = "webp" if media_type == "image/webp" else "pdf"
    year = current_app.config["APPLICATION_YEAR"]
    return f"applications/{year}/{application_id}/{document_type}/{uuid.uuid4().hex}.{extension}.enc"
