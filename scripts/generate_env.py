import argparse
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet


def main():
    parser = argparse.ArgumentParser(description="Generar variables privadas nuevas para Coolify.")
    parser.add_argument("--frontend-url", required=True)
    parser.add_argument("--year", type=int, default=2027)
    parser.add_argument("--output", type=Path, default=Path(".env.coolify"))
    args = parser.parse_args()
    url = urlsplit(args.frontend_url)
    if (
        url.scheme != "https" or not url.hostname or url.path not in {"", "/"}
        or url.query or url.fragment or url.username or url.password
    ):
        parser.error("--frontend-url debe ser un origen HTTPS sin rutas ni credenciales.")
    if not 2000 <= args.year <= 2100:
        parser.error("--year debe estar entre 2000 y 2100.")
    replacements = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "FILE_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "CORS_ORIGINS": args.frontend_url.rstrip("/"),
        "APPLICATION_YEAR": str(args.year),
    }
    source = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    lines = []
    for line in source.splitlines():
        key, separator, _value = line.partition("=")
        lines.append(f"{key}={replacements[key]}" if separator and key in replacements else line)
    with args.output.open("x", encoding="utf-8", newline="\n") as target:
        target.write("\n".join(lines) + "\n")
    print(f"Creado {args.output}. Completar servicios y SMTP; conservar este archivo en privado.")


if __name__ == "__main__":
    main()
