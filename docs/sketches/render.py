"""Renders every page sketch to docs/sketches/html/ with the fake data in fake_data.yaml.

    uv run python docs/sketches/render.py

then open docs/sketches/html/index.html in a browser. The sketches are plain Jinja with no API
and no app behind them: they show what each page in the README's screen map contains, so the
backend knows the fields each page needs (fields.md). A field the data does not have stops the
render (StrictUndefined), which keeps the sketches, the data and fields.md in step.
"""

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# Template: (title, the page's address in the app, the workspace it belongs to)
PAGES = {
    "login.html": ("Sign in", "/login", None),
    "due_diligence_index.html": ("Requests", "/due-diligence", None),
    "due_diligence_new.html": ("New request", "/due-diligence/new", None),
    "dd_summary.html": ("Summary", "/due-diligence/KYB-01/summary", "due_diligence"),
    "dd_questionnaire.html": (
        "Questionnaire",
        "/due-diligence/KYB-01/questionnaire",
        "due_diligence",
    ),
    "dd_report.html": ("Report", "/due-diligence/KYB-01/report", "due_diligence"),
    "customers_index.html": ("Customers", "/customers", None),
    "customer_summary.html": ("Summary", "/customers/KYB-06/summary", "customers"),
    "customer_profile.html": ("Profile", "/customers/KYB-06/profile", "customers"),
    "customer_finances.html": ("Finances", "/customers/KYB-06/finances", "customers"),
    "customer_risks.html": ("Risks", "/customers/KYB-06/risks", "customers"),
    "customer_insights.html": ("Insights", "/customers/KYB-06/insights", "customers"),
    "customer_concentration.html": (
        "Concentration",
        "/customers/KYB-06/concentration",
        "customers",
    ),
}


def main() -> None:
    data = yaml.safe_load((HERE / "fake_data.yaml").read_text(encoding="utf-8"))
    questions = yaml.safe_load((ROOT / "config" / "questionnaire.yaml").read_text(encoding="utf-8"))
    env = Environment(loader=FileSystemLoader(HERE / "templates"), undefined=StrictUndefined, autoescape=True)
    out = HERE / "html"
    out.mkdir(exist_ok=True)
    for name, (title, route, workspace) in PAGES.items():
        tabs = [(page, info[0]) for page, info in PAGES.items() if info[2] == workspace]
        html = env.get_template(name).render(
            data,
            questions=questions["questions"],
            page=name,
            title=title,
            route=route,
            workspace=workspace,
            case=data[workspace] if workspace else None,
            tabs=tabs if workspace else [],
        )
        (out / name).write_text(html, encoding="utf-8")
    index = env.get_template("index.html").render(data, pages=PAGES, title="All sketches", route="")
    (out / "index.html").write_text(index, encoding="utf-8")
    print(f"{len(PAGES)} sketches in {out}")


if __name__ == "__main__":
    main()
