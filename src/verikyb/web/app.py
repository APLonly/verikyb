"""Flask UI: server-rendered pages that call the FastAPI service and hold no AI logic.

Run with `uv run flask --app verikyb.web.app:create_app run --port 5000`.
"""

from flask import Flask

from verikyb.settings import Settings, get_settings
from verikyb.web.api_client import EXTENSION_KEY, ApiClient
from verikyb.web.blueprints import home

SETTINGS_KEY = "verikyb_settings"


def create_app(settings: Settings | None = None, *, api_client: ApiClient | None = None) -> Flask:
    """Tests pass their own settings, or an ApiClient wired straight to the FastAPI app instead of over the network; otherwise both come from the environment."""
    settings = settings or get_settings()
    app = Flask(__name__)
    app.extensions[SETTINGS_KEY] = settings
    app.extensions[EXTENSION_KEY] = api_client or ApiClient.from_settings(settings)
    app.register_blueprint(home.bp)
    return app
