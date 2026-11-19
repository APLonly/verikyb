# Controlled Dev-to-UAT source promotion

The backend CD pipeline remains manual-only. `syncUat` and `promoteQa` both
default to `false`; the UAT stages and their protected resource references are
removed during template expansion when disabled. Existing Dev build, push and
deployment behavior remains available without configuring GitHub write access.
No GitHub Actions, second image build, Azure resources or public ingress are added.

## Release sequence

1. Select an approved `dev` or `main` commit in `APLonly/verikyb` and supply its
   successful branch CI run from definition `2`. PR merge-validation runs do not qualify.
2. Build and smoke-test once, push once, and record the immutable ACR digest.
3. Deploy that digest to `verikyb-dev`. Only an observed running, healthy revision
   with the configured `/health` probes produces `dev-verified.json`.
4. If `syncUat=true`, pass the protected `verikyb-uat-sync` approval and lock.
   Validate release evidence, reviewed clearance, authenticated CI and Dev-stage
   results, expected UAT tip and Git ancestry before pushing any source.
5. Initialize an empty UAT repository by pushing the original commit directly to
   `refs/heads/main`. Future updates use ordinary fast-forward pushes only.
6. Verify UAT commit SHA and tree, then publish `uat-provenance.json` bound to
   this run's release and Dev receipt hashes, revision and image digest.
7. If `promoteQa=true`, pass the separate protected `verikyb-qa` approval. Recheck
   CI, Dev evidence, approval configuration and the actual UAT branch SHA/tree,
   then deploy the identical Dev-tested image digest. There is no QA rebuild.

`promoteQa=true` without `syncUat=true` fails the initial gate before building.
For a source-only promotion, enable `syncUat` and leave `promoteQa=false`.
Enabled stages proceed automatically after their checks and external approvals;
there are no automatic pipeline triggers.

## Administrator prerequisites — not configured by these files

- Review and commit these DevOps changes through the existing Dev PR process,
  together with approved Docker build inputs, then run CI for that exact commit.
  Untracked local files cannot be released by the hosted pipeline.
- Verify/authorize the Azure Pipelines GitHub App for both repositories. Public
  readability does not prove installation access. The WIF connection
  `verikyb-azure-wif` authenticates Azure only, not GitHub.
- Pre-create Azure DevOps environment `verikyb-uat-sync`, restrict its pipeline
  permissions, and configure an enabled Approval with designated approvers,
  **self-approval disabled**, plus an **Exclusive lock** check. `lockBehavior:
  sequential` works only when that external check exists; the script checks for it.
- Keep the protected `verikyb-qa` environment approval, designated approvers and
  self-approval disabled. Restrict environment use and approval/check editing.
- Create protected variable group `verikyb-uat-github` with secret variable
  `uatGitHubToken`. Authorize only this reviewed pipeline and restrict group
  editing/use. Prefer a short-lived GitHub App installation token scoped only to
  `abhishek-tiwari-nitrr/verikyb-qa`, with **Contents: write** and metadata read.
  Arrange secure token issuance/refresh after approvals; token acquisition is
  not implemented here. An explicitly approved fine-grained, expiring token
  restricted to that repository is an alternative. Never grant Dev write access,
  administration, force-push permissions, or broad repository access for this job.
- Upload the reviewed non-secret clearance JSON below as Azure DevOps secure file
  `verikyb-uat-clearance.json`. Restrict upload/replacement, pipeline permissions
  and approval/check administration to the security/release reviewers. Apply a
  separate approval/check to this protected resource. A queue-time Boolean is
  not secret-scan clearance. The script can validate the document, but cannot
  independently prove that the named human actually reviewed a scan.
- Grant the project Build Service read access to CI builds, current-run timeline,
  environment/check metadata and same-run artifacts. Map `System.AccessToken`
  only to evidence-reading tasks. Do not grant GitHub writes through this token.
- Preserve Azure RBAC: registry-scoped AcrPush, Reader on the existing resource
  group, and deployment rights limited to the existing Dev/QA apps. These changes
  do not require subscription/resource-group Contributor or role-assignment rights.
- Git, Bash and Python 3 must be available on the Ubuntu 24.04 agent. The code
  uses only Python's standard library. GitHub and Azure DevOps must be reachable.

The repositories are currently public, so source fetches and QA provenance reads
use unauthenticated HTTPS. If either becomes private, stop and review separate,
read-only GitHub authentication; do not reuse Azure WIF or silently broaden the
UAT writer's access. Current code will fail closed on those read failures.

## Reviewed clearance document

Populate this schema from the approved release and scan report. This example is
not valid approval: replace placeholders and choose actual UTC review/expiry times.

```json
{
  "schemaVersion": 1,
  "approved": true,
  "approvedBy": "<security/release reviewer identity>",
  "approvedAtUtc": "<YYYY-MM-DDTHH:MM:SSZ>",
  "expiresAtUtc": "<YYYY-MM-DDTHH:MM:SSZ>",
  "scanReference": "<reviewed redacted full-history scan report reference>",
  "scope": "reachable-history",
  "sourceRepository": "APLonly/verikyb",
  "sourceCommit": "<approved 40-character Dev commit SHA>",
  "sourceTree": "<approved 40-character Git tree ID>",
  "uatRepository": "abhishek-tiwari-nitrr/verikyb-qa",
  "uatBranch": "refs/heads/main",
  "expectedUatCommit": null
}
```

Use `null` only for the first initialization when UAT has **no refs (including tags)**. For
future promotions, use the exact UAT SHA from the last verified promotion receipt,
after independently checking the current remote. Unexpected branches/tags, changed
tips, divergent history and rollback attempts block promotion. Expired clearance
also blocks QA; allow enough reviewed validity for the approval window.

Scan every commit reachable from the approved SHA, including merged history;
scanning the checkout or filenames is insufficient. Keep raw findings and secret
values out of logs, clearance and artifacts. If secrets are found, stop, revoke
them and obtain approval for remediation in Dev. Do not rewrite history as part
of promotion while claiming to preserve original commit identity.

## Branch and concurrency protections

Before enabling synchronization, protect UAT `main` against force pushes and
deletion, apply protections to administrators where supported, and allow normal
updates only from the dedicated promotion identity. Verify the personal account's
available ruleset/branch-protection capabilities; writer restrictions are a blocker
until confirmed. Arrange rules targeting `main` before its first creation where
supported, or an explicitly controlled initialization window before enabling QA.

All writers must use the same exclusive promotion lock. A pre-push tip comparison
and ordinary Git push are not an atomic expected-old-SHA transaction against
independent writers. Server branch protections, one authorized writer and the
exclusive lock are essential. The script never uses `--force`, force-with-lease,
mirror pushes, deletion refspecs or bulk tag/branch pushes.

Preserve Dev PR reviews and required CI checks. Do not create unrelated UAT initial
commits, or squash/rebase/cherry-pick source. Do not require a UAT merge commit or
linear history for the mirrored branch: Dev merge commits must retain their SHAs.
UAT is a controlled source mirror, not an independent editing branch.

## Evidence, failures and safe retries

- `release/release.json` records source commit/tree, CI result/run, CD definition/run
  and immutable image reference. It is published before deployment and alone is
  **not** proof of Dev success.
- `dev-verified/dev-verified.json` is published only after successful Dev readiness.
  It records the revision, `/health` verification and release-file SHA-256.
- `uat-provenance/uat-provenance.json` records the verified UAT SHA/tree, previous
  tip, reviewed clearance, Dev revision, digest and release/Dev receipt hashes.
  It includes non-secret reviewer metadata; do not put credentials in the schema.
- These are trusted same-run artifacts, not signed attestations. Restrict pipeline
  edits, queue-time overrides, artifact access and protected-resource administration.
  The code rechecks Azure DevOps CI and the completed Dev stage before source writes
  and again before QA; QA rechecks the actual UAT tip/tree after its approval.
- `minReplicas=0` remains unchanged. No observed running healthy replica within the
  existing readiness window means no verified Dev receipt and no source/QA promotion.
  The code does not make ingress public, send public health requests or force scale-up.
- If a push succeeds but verification/artifact publication fails, UAT may already
  have advanced. Stop and inspect it. Obtain a new reviewed clearance with that
  expected tip before retrying; the script never forces a retry. If the approved
  tip already equals the desired commit, the push is skipped and provenance is checked.
- Another promotion can advance UAT while QA approval is pending. The older run then
  fails its QA source check; select/review the intended release instead of bypassing it.
- Retain all three artifacts for the required audit period. No automatic rollback,
  source deletion, resource cleanup or budget enforcement is provided.

For offline evidence checks only, run `bash deploy/uat/sync.sh validate` with the
documented pipeline evidence paths/environment. `sync` can write GitHub and `verify`
contacts GitHub/Azure DevOps; do not invoke either until separately approved.
