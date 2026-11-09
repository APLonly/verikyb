from flask import Blueprint, current_app, render_template

from verikyb.web.api_client import ApiRejectedError, ApiUnavailableError, get_api_client

bp = Blueprint("home", __name__)


@bp.get("/")
def index() -> str:
    """The page always renders: telling the user whether the API is up is its job."""
    health, refused = None, None
    try:
        health = get_api_client().health()
    except ApiUnavailableError as exc:
        current_app.logger.warning("API health check failed: %s", exc)
    except ApiRejectedError as exc:
        current_app.logger.warning("API refused the health check: %s", exc)
        refused = exc.reason
    return render_template("home.html", health=health, refused=refused)
