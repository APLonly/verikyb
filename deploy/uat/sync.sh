#!/usr/bin/env bash
# Invoked only by the protected CD jobs. No Azure CLI or Docker operations.
set +x
set -euo pipefail
python3 - "$@" <<'PY'
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEV = "APLonly/verikyb"
UAT = "abhishek-tiwari-nitrr/verikyb-qa"
BRANCH = "refs/heads/main"
SHA = r"[0-9a-f]{40}"
CLEARANCE_FIELDS = {
    "schemaVersion", "approved", "approvedBy", "approvedAtUtc", "expiresAtUtc",
    "scanReference", "scope", "sourceRepository", "sourceCommit", "sourceTree",
    "uatRepository", "uatBranch", "expectedUatCommit",
}


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit("Required evidence is missing or malformed.")
    require(isinstance(value, dict), "Evidence must be a JSON object.")
    return value


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value),
            "Clearance timestamps must use YYYY-MM-DDTHH:MM:SSZ.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise SystemExit("Clearance timestamps must use YYYY-MM-DDTHH:MM:SSZ.")


def validate_clearance(clearance, release, now=None):
    require(set(clearance) == CLEARANCE_FIELDS, "Unexpected or incomplete clearance schema.")
    require(type(clearance["schemaVersion"]) is int and clearance["schemaVersion"] == 1 and clearance["approved"] is True,
            "Approved reachable-history secret-scan clearance is required.")
    for key in ("approvedBy", "scanReference"):
        require(isinstance(clearance[key], str) and bool(clearance[key].strip()), "Missing clearance approval evidence.")
    require(clearance["scope"] == "reachable-history", "A working-tree-only scan is insufficient.")
    now = now or datetime.now(timezone.utc)
    require(utc(clearance["approvedAtUtc"]) <= now < utc(clearance["expiresAtUtc"]), "Clearance is not currently valid.")
    require(clearance["sourceRepository"] == DEV and clearance["uatRepository"] == UAT
            and clearance["uatBranch"] == BRANCH, "Clearance repository or branch mismatch.")
    require(clearance["sourceCommit"] == release["sourceCommit"]
            and clearance["sourceTree"] == release["sourceTree"], "Clearance does not cover this source history.")
    previous = clearance["expectedUatCommit"]
    require(previous is None or (isinstance(previous, str) and re.fullmatch(SHA, previous)), "Invalid expected UAT tip.")


def validate_evidence(release, dev, environment, release_hash):
    require(environment.get("BUILD_REASON") == "Manual", "Only a manual CD run may promote source.")
    require(release.get("uatSyncRequested") is True, "UAT synchronization was not requested.")
    require(release.get("sourceRepository") == DEV, "Unexpected Dev repository.")
    for key in ("sourceCommit", "sourceTree"):
        require(isinstance(release.get(key), str) and re.fullmatch(SHA, release[key]), "Invalid source identity.")
    require(release["sourceCommit"] == environment["BUILD_SOURCEVERSION"]
            and release.get("sourceBranch") == environment["BUILD_SOURCEBRANCH"]
            and release["sourceBranch"] in ("refs/heads/dev", "refs/heads/main"), "Release source mismatch.")
    require(release.get("ciDefinitionId") == 2 and type(release.get("ciRunId")) is int
            and release["ciRunId"] > 0 and release.get("ciStatus") == "completed"
            and release.get("ciResult") == "succeeded", "Successful exact-commit CI evidence is missing.")
    require(str(release["ciRunId"]) == environment["CI_RUN_ID"], "CI run mismatch.")
    require(release.get("cdRunId") == environment["BUILD_BUILDID"]
            and release.get("cdDefinitionId") == environment["SYSTEM_DEFINITIONID"]
            and release.get("repositoryId") == environment["BUILD_REPOSITORY_ID"], "Producer run/repository mismatch.")
    require(release.get("registry") == "verikybacrps2026.azurecr.io"
            and release.get("imageRepository") == "verikyb-backend"
            and re.fullmatch(r"sha256:[0-9a-f]{64}", release.get("digest", ""))
            and release.get("imageReference") == f'{release["registry"]}/{release["imageRepository"]}@{release["digest"]}',
            "Unexpected immutable image reference.")
    require(dev.get("status") == "succeeded" and dev.get("healthVerified") is True
            and dev.get("targetApp") == "verikyb-dev" and dev.get("healthEndpoint") == "/health"
            and isinstance(dev.get("revision"), str) and dev["revision"].startswith("verikyb-dev--"),
            "Successful Dev deployment and observed health evidence are required.")
    for key in ("sourceRepository", "sourceCommit", "sourceTree", "sourceBranch", "repositoryId",
                "ciDefinitionId", "ciRunId", "cdDefinitionId", "cdRunId", "digest", "imageReference"):
        require(dev.get(key) == release.get(key), "Dev evidence does not match this release.")
    require(dev.get("releaseSha256") == release_hash, "Dev receipt is bound to a different release artifact.")


def validate_ci(run, release):
    require(run.get("id") == release["ciRunId"] and run.get("definition", {}).get("id") == 2
            and run.get("status") == "completed" and run.get("result") == "succeeded"
            and run.get("sourceVersion") == release["sourceCommit"]
            and run.get("sourceBranch") == release["sourceBranch"]
            and str(run.get("repository", {}).get("id", "")).casefold() == release["repositoryId"].casefold()
            and run.get("reason", "").casefold() != "pullrequest", "Authenticated exact-commit CI recheck failed.")


def ado_get(path, **query):
    token = os.environ.get("ADO_TOKEN", "")
    require(bool(token) and not token.startswith("$("), "Azure DevOps evidence-read token is unavailable.")
    collection = os.environ["SYSTEM_COLLECTIONURI"]
    require(collection.startswith("https://dev.azure.com/"), "Unexpected Azure DevOps collection URL.")
    base = collection.rstrip("/") + "/" + os.environ["SYSTEM_TEAMPROJECTID"] + "/_apis/"
    request = urllib.request.Request(base + path + "?" + urllib.parse.urlencode(query),
                                     headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.load(response)
    except (urllib.error.URLError, ValueError):
        raise SystemExit("Cannot authenticate/read Azure DevOps release evidence.")
    require(isinstance(value, dict), "Malformed Azure DevOps evidence response.")
    return value


def verify_checks(name, lock=False):
    rows = ado_get("distributedtask/environments", name=name, **{"api-version": "7.1"}).get("value", [])
    matches = [r for r in rows if r.get("name") == name]
    require(len(matches) == 1, "Required protected promotion environment is unavailable.")
    identity = matches[0]["id"]
    checks = ado_get("pipelines/checks/configurations", resourceType="environment", resourceId=identity,
                     **{"$expand": "settings", "api-version": "7.1-preview.1"}).get("value")
    require(isinstance(checks, list), "Cannot verify protected environment checks.")
    active = [c for c in checks if c.get("isDisabled") is False
              and c.get("resource", {}).get("type") == "environment"
              and str(c["resource"].get("id")) == str(identity)]
    require(any(c.get("type", {}).get("name", "").casefold() == "approval"
                and isinstance(c.get("settings", {}).get("approvers"), list)
                and c["settings"]["approvers"] and c["settings"].get("requesterCannotBeApprover") is True
                for c in active), "An enabled approval with self-approval disabled is required.")
    if lock:
        require(any(c.get("type", {}).get("name", "").casefold() == "exclusivelock" for c in active),
                "The UAT synchronization environment requires an exclusive lock.")


def verify_ado(release, qa=False):
    validate_ci(ado_get(f'build/builds/{release["ciRunId"]}', **{"api-version": "7.1"}), release)
    run = ado_get(f'build/builds/{release["cdRunId"]}', **{"api-version": "7.1"})
    require(str(run.get("id")) == release["cdRunId"]
            and str(run.get("definition", {}).get("id")) == release["cdDefinitionId"]
            and run.get("sourceVersion") == release["sourceCommit"]
            and run.get("sourceBranch") == release["sourceBranch"]
            and run.get("repository", {}).get("id") == release["repositoryId"], "CD producer API evidence mismatch.")
    records = ado_get(f'build/builds/{release["cdRunId"]}/timeline', **{"api-version": "7.1"}).get("records", [])
    require(any(r.get("type") == "Stage" and r.get("identifier") == "Deploy_dev"
                and r.get("state") == "completed" and r.get("result") == "succeeded" for r in records),
            "The producer Dev stage has not completed successfully.")
    verify_checks("verikyb-qa" if qa else "verikyb-uat-sync", lock=not qa)


def validate_provenance(proof, release, dev, release_hash, dev_hash):
    require(release.get("qaRequested") is True and os.environ.get("PROMOTE_QA", "").casefold() == "true",
            "QA promotion is not authorized for this run.")
    require(proof.get("schemaVersion") == 1 and proof.get("status") == "verified"
            and proof.get("uatRepository") == UAT and proof.get("uatBranch") == BRANCH,
            "Missing or unexpected UAT provenance.")
    for key in ("sourceCommit", "sourceTree", "ciRunId", "cdRunId", "digest", "imageReference"):
        require(proof.get(key) == release[key], "UAT provenance differs from the Dev-tested release.")
    require(proof.get("uatCommit") == release["sourceCommit"] and proof.get("uatTree") == release["sourceTree"]
            and proof.get("devRevision") == dev["revision"] and proof.get("releaseSha256") == release_hash
            and proof.get("devReceiptSha256") == dev_hash, "UAT/Dev provenance binding failed.")
    clearance = proof.get("clearance")
    require(isinstance(clearance, dict), "Secret-scan clearance is missing from provenance.")
    validate_clearance(clearance, release)
    require(proof.get("previousUatCommit") == clearance["expectedUatCommit"], "Unexpected previous UAT tip.")


def git(work, *args, authenticated=False):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")
                   and k not in ("GITHUB_TOKEN", "ADO_TOKEN")}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    if authenticated:
        token = os.environ.get("GITHUB_TOKEN", "")
        require(bool(token) and not token.startswith("$("), "Separate UAT-scoped GitHub write credential is unavailable.")
        header = base64.b64encode(("x-access-token:" + token).encode()).decode()
        environment.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=f"http.https://github.com/{UAT}.git.extraheader",
                           GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + header)
    command = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
               "-c", "http.followRedirects=false", "-c", "protocol.file.allow=never"]
    if work is not None:
        command += ["-C", str(work)]
    try:
        result = subprocess.run(command + list(args), env=environment, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("Git operation failed or timed out; inspect remote state before retrying.")
    # Never print stderr: Git errors and helpers can contain authentication details.
    require(result.returncode == 0, "Git operation rejected/failed; no forced retry is permitted.")
    return result.stdout.strip()


def check_tip(refs, previous):
    expected = "" if previous is None else previous + "\t" + BRANCH
    require(refs == expected, "Unexpected UAT refs or commits; refusing synchronization.")


def verify_remote(work, release):
    url = f"https://github.com/{UAT}.git"
    refs = git(work, "ls-remote", "--refs", url)
    check_tip(refs, release["sourceCommit"])
    git(work, "fetch", "--no-tags", url, BRANCH)
    require(git(work, "rev-parse", "FETCH_HEAD") == release["sourceCommit"]
            and git(work, "rev-parse", "FETCH_HEAD^{tree}") == release["sourceTree"], "Remote UAT commit/tree mismatch.")


def synchronize(work, release, clearance):
    source, previous = release["sourceCommit"], clearance["expectedUatCommit"]
    dev_url, uat_url = f"https://github.com/{DEV}.git", f"https://github.com/{UAT}.git"
    refs = git(None, "ls-remote", "--refs", uat_url)
    check_tip(refs, previous)
    git(None, "clone", "--no-checkout", "--no-tags", "--single-branch", "--branch",
        release["sourceBranch"].removeprefix("refs/heads/"), dev_url, str(work))
    require(git(work, "cat-file", "-t", source) == "commit"
            and git(work, "rev-parse", source + "^{tree}") == release["sourceTree"], "Dev commit/tree mismatch.")
    git(work, "merge-base", "--is-ancestor", source, "HEAD")
    if previous is not None:
        git(work, "fetch", "--no-tags", uat_url, BRANCH)
        require(git(work, "rev-parse", "FETCH_HEAD") == previous, "UAT changed during synchronization.")
        git(work, "merge-base", "--is-ancestor", previous, source)
    require(git(work, "ls-remote", "--refs", uat_url) == refs, "UAT changed before push.")
    if previous != source:
        git(work, "push", "--porcelain", "--no-follow-tags", uat_url, source + ":" + BRANCH, authenticated=True)
    verify_remote(work, release)


def main():
    require(len(sys.argv) == 2 and sys.argv[1] in ("validate", "sync", "verify"), "Usage: bash sync.sh validate|sync|verify")
    mode = sys.argv[1]
    directory = Path(os.environ["RELEASE_DIR"])
    dev_path = Path(os.environ["DEV_EVIDENCE_DIR"]) / "dev-verified.json"
    release_path = directory / "release.json"
    release, dev = read_json(release_path), read_json(dev_path)
    release_hash, dev_hash = checksum(release_path), checksum(dev_path)
    validate_evidence(release, dev, os.environ, release_hash)
    proof_path = Path(os.environ["UAT_PROVENANCE_DIR"]) / "uat-provenance.json"
    if mode == "verify":
        proof = read_json(proof_path)
        validate_provenance(proof, release, dev, release_hash, dev_hash)
        verify_ado(release, qa=True)
    else:
        clearance = read_json(os.environ["UAT_CLEARANCE_FILE"])
        validate_clearance(clearance, release)
        if mode == "validate":
            print("Offline release, Dev receipt and clearance validation passed; no network operations performed.")
            return
        verify_ado(release)
    temporary_root = Path(os.environ["AGENT_TEMPDIRECTORY"])
    require(temporary_root.is_dir(), "Azure Pipelines temporary workspace is required.")
    with tempfile.TemporaryDirectory(prefix="verikyb-uat-", dir=temporary_root) as temporary:
        work = Path(temporary) / "repository"
        if mode == "sync":
            synchronize(work, release, clearance)
            proof = {"schemaVersion": 1, "status": "verified", "uatRepository": UAT, "uatBranch": BRANCH,
                     "uatCommit": release["sourceCommit"], "uatTree": release["sourceTree"],
                     "previousUatCommit": clearance["expectedUatCommit"], "clearance": clearance,
                     "devRevision": dev["revision"], "releaseSha256": release_hash, "devReceiptSha256": dev_hash,
                     "verifiedAtUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            proof.update({key: release[key] for key in ("sourceCommit", "sourceTree", "ciRunId", "cdRunId", "digest", "imageReference")})
            proof_path.parent.mkdir(parents=True, exist_ok=True)
            proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        else:
            git(None, "clone", "--no-checkout", "--no-tags", "--single-branch", "--branch", "main",
                f"https://github.com/{UAT}.git", str(work))
            verify_remote(work, release)
    print("Verified UAT source commit/tree and Dev-tested immutable image provenance.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TypeError, ValueError, OSError):
        raise SystemExit("Incomplete or malformed release evidence; promotion is blocked.")
PY
