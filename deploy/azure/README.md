# VeriKYB Azure bootstrap

`setup.sh` provisions a shared Central India resource group, Basic ACR, a
Consumption-only Container Apps environment and Dev/QA placeholder apps. It is
an interactive administrative bootstrap, not an application deployment pipeline.
Nothing is provisioned until the exact typed confirmation is entered.

## Prerequisites

- Bash 4+ (WSL or Linux), Python 3, and a current Azure CLI with Container Apps
  commands supporting workload profiles and `--role-assignment-mode rbac` for ACR.
  Install/update tooling separately; the script does not install extensions.
- Authenticate separately to the existing public-Azure subscription. Set
  `AZURE_SUBSCRIPTION_ID`, globally unique `ACR_NAME`, `ESTIMATED_7_DAY_INR`, and
  `CLEANUP_DEADLINE_UTC` in your shell; optionally set `PREFIX` (default `verikyb`).
  IDs and resource names are non-secret. Never put keys or tokens in these files.
- `AZURE_SUBSCRIPTION_ID` must be the intended subscription's full GUID; select
  that subscription separately before running the script. `ACR_NAME` must contain
  5-50 lowercase letters/digits. For existing resources, retain their registry
  name and prefix rather than choosing new ones.
- Set `ESTIMATED_7_DAY_INR` to a separately reviewed all-in estimate for the planned
  seven-day deployment, including ACR, expected Container Apps consumption,
  storage, networking/egress, applicable taxes and other charges. Enter a finite
  decimal number greater than zero and at most 500, without currency symbols or
  thousands separators. The script validates this range, not the estimate's accuracy.
- Set `CLEANUP_DEADLINE_UTC` to the intended manual cleanup deadline using
  `YYYY-MM-DDTHH:MM:SSZ` (24-hour UTC, with a literal `T` and trailing `Z`). At the
  script's validation time, it must be in the future and no more than seven days
  away. Keep the previously agreed deadline on reruns; do not extend it merely
  because the setup is being retried.
- INR 500/month is the monthly budget target for subscription spending, including
  existing usage and other planned work. `ESTIMATED_7_DAY_INR` describes only the
  separately reviewed seven-day deployment; it is not a monthly spending estimate
  or an additional monthly allowance. Review the remaining monthly budget before
  proceeding. Azure budget alerts are not spending caps; this script does not
  enforce a spending limit. Set budget alerts separately.
- `Microsoft.App`, `Microsoft.ContainerRegistry` and
  `Microsoft.OperationalInsights` must already be registered. The last provider
  is a Container Apps prerequisite; no Log Analytics workspace is created.
- The provisioning operator needs resource creation/write permissions plus
  role-assignment read/write permissions. A typical administrative combination
  is Contributor and Role Based Access Control Administrator at the intended
  scope, subject to assignment conditions. Creating a new RG requires
  subscription-level resource-group permissions. Existing-RG checks use RG scope.
  These administrative permissions must not be granted to normal deployment CI.

## Set non-secret inputs from PowerShell

This example only sets environment variables; it does not authenticate, query
Azure or execute provisioning. Supply reviewed values when prompted; no cost
estimate or cleanup date is assumed.

```powershell
$env:AZURE_SUBSCRIPTION_ID = (Read-Host 'Intended Azure subscription GUID').Trim()
$env:ACR_NAME = (Read-Host 'Reviewed ACR name; retain the existing name on reruns').Trim()
$env:ESTIMATED_7_DAY_INR = (Read-Host 'Reviewed all-in seven-day estimate in INR; decimal number only').Trim()
$env:CLEANUP_DEADLINE_UTC = (Read-Host 'Agreed manual cleanup deadline in UTC: YYYY-MM-DDTHH:MM:SSZ').Trim()
$env:PREFIX = 'verikyb' # Optional; retain the existing resource prefix on reruns.
```

Use PowerShell 7 on Linux (for example, Azure Cloud Shell PowerShell) if Bash is
to inherit these variables directly. Bash 4+, `python3`, `tr` and the required
Azure CLI commands must be available in that same environment. In Windows
PowerShell, these assignments affect the Windows session; do not assume a WSL
Bash session inherits them. If using WSL, set/export the same reviewed variables
inside WSL before any separately approved execution.

After review and execution approval, run `bash deploy/azure/setup.sh` from an
interactive terminal with those variables exported. Preflight performs read-only
Azure queries; only the confirmation unlocks provisioning. There is no unattended
`--yes` bypass. The script never changes the active CLI subscription.

## Resource safety and identities

Existing names must have the script's `provisionedBy` tag and expected region.
ACR must be Basic, admin login disabled, legacy registry RBAC, and ARM token
authentication enabled. The environment must use only Consumption with logging
disabled. Existing apps must use the intended environment, min=0/max=1 and their
own system-assigned identities. Incompatible resources cause a stop rather than
automatic adoption, deletion or reconfiguration. Do not add ownership tags to
unrelated resources merely to bypass these checks.

Each app receives only `AcrPull` at the registry scope. Missing assignments and
managed-identity registry bindings are added on rerun; incompatible bindings are
not overwritten. Application images/configuration are not updated on rerun.
An interrupted run can leave resources and charges in place. RBAC propagation
can take minutes; inspect and retry after propagation. Do not run concurrent
copies. Permission preflight is conservative and cannot prove Azure Policy,
deny-assignment, conditional-role, quota or regional-capacity compatibility.

## Azure DevOps deployment configuration

Copy the printed non-secret common variables into Azure DevOps configuration,
and the corresponding app variables into separate Dev and QA configurations.
Set `AZURE_SERVICE_CONNECTION` separately to each reviewed workload-identity
federated service connection name. Use it in `AzureCLI@2` via `azureSubscription`;
GitHub remains the source repository. No GitHub Actions, client secrets or service
connections are created here.

Configure pipeline RBAC separately after those WIF principal object IDs exist:

| Principal | Role and scope |
| --- | --- |
| Image build/push identity | `AcrPush` on this ACR only |
| Dev deployment identity | `Container Apps Contributor` on the Dev app only |
| QA deployment identity | `Container Apps Contributor` on the QA app only |

These are narrow built-in roles, not a claim of minimal individual actions:
Container Apps Contributor can manage/delete its scoped app. Prefer an
organization-reviewed custom role if only revision updates should be allowed.
Do not grant subscription/RG Contributor or role-assignment permissions to these
identities. Deployment tasks should update the already-created app by resource
name/ID; environment discovery or other tasks may require separately reviewed
read-only permissions. Restrict service connection use to authorized pipelines,
and configure QA approvals in Azure DevOps separately.

## Cost and hosting limitations

Cleanup is manual. `CLEANUP_DEADLINE_UTC` records and displays the intended deadline;
it does not schedule deletion or stop resources. Arrange manual cleanup by that
deadline. Resources can continue billing after seven days or after the deadline
until they are manually removed; rerunning setup does not perform cleanup.

ACR Basic bills continuously even with no running apps. INR 500/month is not
guaranteed and may be insufficient after taxes and other usage. Consumption free
grants are shared across the subscription; min replicas zero does not prevent
startup, active-compute, request or network charges. Max replicas one is a
capacity limit, not a billing cap. No Firewall, NAT Gateway, AKS, dedicated
workload profile, custom VNet, private endpoint or Log Analytics is created.

Apps start with Microsoft's public quickstart image (mutable `latest` tag),
internal-only ingress, single revision, 0.25 vCPU and 0.5 GiB RAM. Provisioning
therefore starts a temporary placeholder container and may incur small charges.
It does not build/deploy VeriKYB. Internal ingress is not end-user authentication;
public access, production sizing, immutable application images, health probes,
runtime configuration and durable monitoring require separate review. Shared
environment/registry reduce costs but do not provide strong Dev/QA isolation.
No OpenAI, Anthropic, Groq or Langfuse credentials are read or configured.

References: [ACR pricing](https://azure.microsoft.com/en-us/pricing/details/container-registry/),
[Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing),
[environment CLI](https://learn.microsoft.com/en-us/cli/azure/containerapp/env),
[managed-identity pulls](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity-image-pull),
[built-in container roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/containers).
