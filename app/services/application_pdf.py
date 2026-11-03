from io import BytesIO
from xml.sax.saxutils import escape

from flask import current_app
from PIL import Image as PILImage
from PIL import ImageOps
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer

from .admin_applications import application_data
from .encryption import EncryptionService
from .storage import StorageService

SECTIONS = (
    ("request", "Solicitud"),
    ("applicant", "Persona aspirante"),
    ("studies", "Escolaridad"),
    ("health", "Salud"),
    ("household", "Convivencia"),
    ("householdMembers", "Integrantes del núcleo conviviente"),
    ("outsideParents", "Progenitores fuera del hogar"),
    ("socioeconomic", "Situación socioeconómica"),
)


def build_application_pdf(application):
    data = application_data(application)
    label_template = current_app.jinja_env.get_template("admin/_data.html").module
    labels, values = label_template.labels, label_template.values
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Field", parent=styles["BodyText"], fontSize=9, leading=13, spaceAfter=7))
    styles["Heading1"].textColor = colors.HexColor("#312e81")
    styles["Heading2"].textColor = colors.HexColor("#312e81")
    story = []

    def paragraph(text, style="Field"):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), styles[style])

    def fields(value, prefix=""):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "id":
                    continue
                label = labels.get(key, key)
                fields(item, f"{prefix} / {label}" if prefix else label)
        elif isinstance(value, list):
            if not value:
                story.append(paragraph(f"{prefix}: Sin registros" if prefix else "Sin registros"))
            for index, item in enumerate(value, 1):
                fields(item, f"{prefix} {index}".strip())
        else:
            if value is None or value == "":
                display = "Sin informar"
            elif isinstance(value, bool):
                display = "Sí" if value else "No"
            else:
                display = values.get(value, value) if isinstance(value, str) else value
            story.append(paragraph(f"{prefix}: {display}" if prefix else display))

    story.append(paragraph("Hogares Estudiantiles", "Heading1"))
    story.append(paragraph("Ficha de inscripción", "Heading2"))
    story.append(paragraph(f"Código: {application.tracking_code or application.id}"))
    story.append(
        paragraph(f"Convocatoria: {application.application_year} | Hogar: {application.home or 'Sin informar'}")
    )
    story.append(paragraph(f"Tipo: {'Primera inscripción' if application.application_type == 'new' else 'Renovación'}"))
    story.append(paragraph(f"Correo de contacto: {data.get('contactEmail') or 'Sin informar'}"))
    story.append(paragraph(f"Recepción: {application.submitted_at.strftime('%d/%m/%Y %H:%M')} UTC"))
    certificate = application.good_conduct_received_at
    story.append(
        paragraph(
            "Certificado de buena conducta: "
            + (f"recibido el {certificate.strftime('%d/%m/%Y %H:%M')} UTC" if certificate else "no recibido")
        )
    )
    for key, title in SECTIONS:
        story.append(paragraph(title, "Heading2"))
        fields(data.get(key))
    story.append(paragraph("Declaración final", "Heading2"))
    fields(data.get("acceptedDeclaration"), "Declaración aceptada")
    story.append(paragraph("Documentos adjuntos", "Heading2"))
    documents = sorted(application.documents, key=lambda item: (item.document_type, item.id))
    if not documents:
        story.append(paragraph("Sin documentos adjuntos."))
    for doc in documents:
        story.append(paragraph(f"{doc.original_name} ({doc.media_type})"))

    # Fail the whole export if an attachment is missing, rather than return an incomplete student record.
    attachments = []
    storage = StorageService() if documents else None
    for doc in documents:
        content = EncryptionService.decrypt_file(storage.get(doc.object_key))
        if doc.media_type.startswith("image/"):
            with PILImage.open(BytesIO(content)) as source:
                converted = ImageOps.exif_transpose(source).convert("RGB")
                image_file = BytesIO()
                converted.save(image_file, format="PNG")
                width, height = converted.size
            image_file.seek(0)
            scale = min(170 * mm / width, 210 * mm / height)
            story.append(PageBreak())
            story.append(paragraph("Documento adjunto", "Heading2"))
            story.append(paragraph(doc.original_name[:255]))
            story.append(Spacer(1, 4 * mm))
            story.append(Image(image_file, width=width * scale, height=height * scale))
        elif doc.media_type == "application/pdf":
            attachments.append(content)
        else:
            raise ValueError("Formato de adjunto no admitido en la ficha")

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(20 * mm, 12 * mm, "Hogares Estudiantiles - Uso administrativo")
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Página {document.page}")
        canvas.restoreState()

    output = BytesIO()
    SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title=f"Ficha {application.tracking_code}",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    if not attachments:
        return output.getvalue()
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(output.getvalue())), import_outline=False)
    for content in attachments:
        writer.append(PdfReader(BytesIO(content)), import_outline=False, excluded_fields=["/Annots", "/AA"])
    combined = BytesIO()
    writer.write(combined)
    return combined.getvalue()
