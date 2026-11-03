import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(  # noqa: S104 - explicit container binding; debug and reloader remain disabled.
        host=os.getenv("FLASK_RUN_HOST", "0.0.0.0"),  # noqa: S104 - required container binding.
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )
