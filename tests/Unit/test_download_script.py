"""`make data`'s policies: a document that may be downloaded is saved only when the answer really is a PDF; anything else becomes a manual step saying where to save a copy, and a document already in place is left alone. Offline: the publishers are answered by a fake transport."""

import importlib.util
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("download", ROOT / "data" / "scripts" / "download.py")
assert spec is not None
assert spec.loader is not None
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)

CORPUS = """documents:
  - id: allowed
    title: A publisher that allows scripted downloads
    jurisdiction: GB
    path: data/raw/policies/allowed.pdf
    url: https://allowed.example/policy.pdf
  - id: blocked
    title: A publisher that blocks them
    jurisdiction: IN
    path: data/raw/policies/blocked.pdf
    page: https://blocked.example/publications
    url: https://blocked.example/policy.pdf
  - id: in-repo
    title: A policy kept in the repository
    jurisdiction: GB
    path: data/policies/in_repo.md
"""


def publisher(request: httpx.Request) -> httpx.Response:
    if request.url.host == "allowed.example":
        return httpx.Response(200, content=b"%PDF-1.7 the policy")
    return httpx.Response(200, text="<html>Please verify you are a human</html>")


def test_policies_are_saved_only_as_pdfs_and_the_rest_are_manual_steps(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "policy_corpus.yaml").write_text(CORPUS, encoding="utf-8")
    (tmp_path / "data" / "policies").mkdir(parents=True)
    (tmp_path / "data" / "policies" / "in_repo.md").write_text("POL-1", encoding="utf-8")

    with httpx.Client(transport=httpx.MockTransport(publisher)) as client:
        steps = download.policies(client, tmp_path)

    assert (tmp_path / "data/raw/policies/allowed.pdf").read_bytes().startswith(b"%PDF")
    assert not (tmp_path / "data/raw/policies/blocked.pdf").exists()
    assert steps == ["save 'A publisher that blocks them' from https://blocked.example/publications as data/raw/policies/blocked.pdf"]
