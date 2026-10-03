import logging
import os
from pathlib import Path

from flask import Flask

from .models import add_missing_columns, db


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "instance"))
        data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{data_dir / 'cml.db'}"
    # Railway hands out postgres:// or postgresql:// URLs; use the psycopg 3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def create_app() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-not-secret"),
        SQLALCHEMY_DATABASE_URI=_database_url(),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.environ.get("RAILWAY_ENVIRONMENT")),
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    )
    # Fail the deploy (and keep the previous one running) if the RA roster is
    # missing or malformed, rather than silently notifying nobody.
    from .ras import load_ras
    roster = load_ras()
    logging.getLogger(__name__).info("Loaded %d RAs: %s", len(roster), ", ".join(roster))

    db.init_app(app)
    with app.app_context():
        db.create_all()
        for col in add_missing_columns():
            logging.getLogger(__name__).info("Added column %s", col)

    from .routes import bp
    app.register_blueprint(bp)

    from .cli import register
    register(app)
    return app
