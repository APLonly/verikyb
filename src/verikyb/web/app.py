"""Flask UI: server-rendered pages that call the FastAPI service and hold no AI logic.

Run with `uv run flask --app verikyb.web.app:create_app run --port 5000`.
"""

from flask import Flask

from verikyb.settings import Settings, get_setting
from verikyb.web.blueprints import home

SETTINGS_KEY = "verikyb_settings"


def create_app(settings: Settings | None = None) -> Flask:
    """Tests pass their own settings; otherwise they come from the environment."""
    app = Flask(__name__)
    app.extensions[SETTINGS_KEY] = settings or get_setting()
    app.register_blueprint(home.bp)
    return app
