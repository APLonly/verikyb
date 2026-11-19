#!/usr/bin/env bash
# Provisioning only: never run automatically from application CI.
set -euo pipefail
trap 'printf "Stopped at line %s. Existing resources are retained; inspect before retrying.\n" "$LINENO" >&2' ERR
die() { printf '%s\n' "$*" >&2; exit 1; }
for tool in az python3 tr; do command -v "$tool" >/dev/null || die "Install $tool first."; done

# Explicit inputs; IDs below are identifiers, never credentials.
: "${AZURE_SUBSCRIPTION_ID:?Set the intended subscription ID}"
: "${ACR_NAME:?Set a globally unique registry name (5-50 lowercase letters/digits)}"
: "${ESTIMATED_7_DAY_INR:?Set a reviewed all-in 7-day estimate including ACR, Container Apps, networking, taxes and other charges}"
: "${CLEANUP_DEADLINE_UTC:?Set the intended cleanup deadline as YYYY-MM-DDTHH:MM:SSZ}"
PREFIX=${PREFIX:-verikyb}
LOCATION=centralindia
RG="${PREFIX}-rg"
ENV="${PREFIX}-env"
OWNER=verikyb-azure-setup
[[ "$AZURE_SUBSCRIPTION_ID" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]] || die 'Invalid subscription ID.'
[[ "$PREFIX" =~ ^[a-z][a-z0-9-]{1,17}[a-z0-9]$ && "$PREFIX" != *--* ]] || die 'PREFIX must be 3-19 lowercase letters/digits/hyphens, without consecutive hyphens.'
[[ "$ACR_NAME" =~ ^[a-z0-9]{5,50}$ ]] || die 'Invalid ACR_NAME.'
python3 - "$ESTIMATED_7_DAY_INR" "$CLEANUP_DEADLINE_UTC" <<'PY'
import decimal, sys
from datetime import datetime, timedelta, timezone

try:
    estimate = decimal.Decimal(sys.argv[1])
    if not estimate.is_finite() or not 0 < estimate <= 500:
        raise ValueError
except (decimal.InvalidOperation, ValueError):
    sys.exit('Reviewed all-in 7-day estimate must be positive and at most INR 500. Also review remaining monthly budget.')

try:
    deadline = datetime.strptime(
        sys.argv[2], '%Y-%m-%dT%H:%M:%SZ'
    ).replace(tzinfo=timezone.utc)
except ValueError:
    sys.exit('Cleanup deadline must use UTC format YYYY-MM-DDTHH:MM:SSZ.')

now = datetime.now(timezone.utc)
if not now < deadline <= now + timedelta(days=7):
    sys.exit('Cleanup deadline must be in the future and no more than 7 days away.')
PY

# No login, subscription switching, extension installation or provider registration.
# Disable implicit extension installs, which otherwise can happen during CLI calls.
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=no
azx() { az "$@" --subscription "$AZURE_SUBSCRIPTION_ID" --only-show-errors; }
[[ "$(azx account show --query state -o tsv)" == Enabled ]] || die 'Subscription is not enabled; authenticate separately.'
[[ "$(azx account show --query environmentName -o tsv)" == AzureCloud ]] || die 'This script targets public Azure only.'
az containerapp env create --help >/dev/null
az containerapp registry set --help >/dev/null
# list-locations uses the current subscription and does not accept --subscription.
current_subscription=$(az account show --query id -o tsv --only-show-errors) \
  || die 'Unable to read the currently selected Azure subscription; authenticate separately.'
[[ "${current_subscription,,}" == "${AZURE_SUBSCRIPTION_ID,,}" ]] \
  || die 'Currently selected Azure subscription does not match AZURE_SUBSCRIPTION_ID. Select the intended subscription separately, then retry.'
[[ "$(az account list-locations --query "[?name=='centralindia'].name | [0]" -o tsv --only-show-errors)" == centralindia ]] || die 'Central India is unavailable to this subscription.'
for provider in Microsoft.App Microsoft.ContainerRegistry Microsoft.OperationalInsights; do
  [[ "$(azx provider show -n "$provider" --query registrationState -o tsv)" == Registered ]] || die "Register $provider separately, then retry."
done
for pair in 'Microsoft.App managedEnvironments' 'Microsoft.App containerApps' 'Microsoft.ContainerRegistry registries'; do
  read -r provider kind <<< "$pair"
  supported=$(azx provider show -n "$provider" --query "resourceTypes[?resourceType=='$kind'].locations[]" -o tsv)
  [[ "$supported" == *'Central India'* ]] || die "$kind does not advertise Central India support."
done

SUB_SCOPE="/subscriptions/$AZURE_SUBSCRIPTION_ID"
RG_ID="$SUB_SCOPE/resourceGroups/$RG"
ACR_ID="$RG_ID/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME"
ENV_ID="$RG_ID/providers/Microsoft.App/managedEnvironments/$ENV"
RG_EXISTS=$(azx group exists -n "$RG" -o tsv)
PERMISSION_SCOPE=$SUB_SCOPE
[[ "$RG_EXISTS" != true ]] || PERMISSION_SCOPE=$RG_ID

# Effective allowed actions check. Azure Policy, deny assignments, conditional
# RBAC and regional capacity can still reject an operation after this check.
permissions=$(azx rest --method get --url "https://management.azure.com$PERMISSION_SCOPE/providers/Microsoft.Authorization/permissions?api-version=2022-04-01" -o json)
PERMISSIONS_JSON="$permissions" python3 - <<'PY'
import fnmatch, json, os, sys
data = json.loads(os.environ['PERMISSIONS_JSON'])
if data.get('nextLink'):
    sys.exit('Permissions response is paginated; ask an administrator to review it.')
required = ['Microsoft.Resources/subscriptions/resourceGroups/read',
            'Microsoft.Resources/subscriptions/resourceGroups/write',
            'Microsoft.ContainerRegistry/registries/read',
            'Microsoft.ContainerRegistry/registries/write',
            'Microsoft.App/managedEnvironments/read',
            'Microsoft.App/managedEnvironments/write',
            'Microsoft.App/managedEnvironments/join/action',
            'Microsoft.App/containerApps/read', 'Microsoft.App/containerApps/write',
            'Microsoft.Authorization/roleAssignments/read',
            'Microsoft.Authorization/roleAssignments/write']
def matches(action, patterns):
    return any(fnmatch.fnmatchcase(action.lower(), p.lower()) for p in patterns)
missing = [a for a in required if not any(
    matches(a, p.get('actions', [])) and not matches(a, p.get('notActions', []))
    for p in data['value'])]
if missing:
    sys.exit('Missing provisioning permissions: ' + ', '.join(missing))
PY
unset permissions

# Fail closed on name collisions. Reuse only resources tagged by this script.
# Query resource metadata only; never retrieve app secrets or registry passwords.
INVENTORY='[]'
if [[ "$RG_EXISTS" == true ]]; then
  [[ "$(azx group show -n "$RG" --query location -o tsv)" == "$LOCATION" ]] || die 'Existing resource group has a different location.'
  [[ "$(azx group show -n "$RG" --query tags.provisionedBy -o tsv)" == "$OWNER" ]] || die 'Existing resource group is not owned by this script.'
  INVENTORY=$(azx resource list -g "$RG" --query '[].{id:id,name:name,type:type,location:location,owner:tags.provisionedBy}' -o json)
fi
exists() {
  INVENTORY_JSON="$INVENTORY" python3 - "$1" "$2" "$LOCATION" "$OWNER" <<'PY'
import json, os, sys
name, kind, location, owner = sys.argv[1:]
found = [r for r in json.loads(os.environ['INVENTORY_JSON'])
         if r['name'].lower() == name.lower() and r['type'].lower() == kind.lower()]
if not found:
    print('false')
elif any(r['location'] != location or r.get('owner') != owner for r in found):
    sys.exit('Existing resource has incompatible location/ownership: ' + name)
else:
    print('true')
PY
}
# Keep a JSON envelope so an unset registry list is explicit null, not empty output.
# Inspect registry configuration only; never retrieve app secret values.
registry_binding_state() {
  local app_name="$1" registry_server="$2" registry_metadata
  registry_metadata=$(azx containerapp show -n "$app_name" -g "$RG" \
    --query '{configurationType:type(properties.configuration),registries:properties.configuration.registries}' -o json) \
    || die "Could not inspect registry configuration for $app_name; refusing to change it."
  REGISTRY_METADATA="$registry_metadata" python3 - "$app_name" "$registry_server" <<'PY'
import json, os, sys
app, server = sys.argv[1:]
def reject(reason):
    sys.exit(f'{app}: {reason}; refusing to change registry configuration.')
try:
    metadata = json.loads(os.environ['REGISTRY_METADATA'])
except json.JSONDecodeError:
    reject('registry inspection returned empty or malformed JSON')
if (not isinstance(metadata, dict) or metadata.get('configurationType') != 'object'
        or 'registries' not in metadata):
    reject('registry inspection returned an unexpected configuration structure')
registries = metadata['registries']
if registries is None:
    registries = []
if not isinstance(registries, list):
    reject('configured registries must be a list or null')
for entry in registries:
    if (not isinstance(entry, dict) or not isinstance(entry.get('server'), str)
            or not entry['server'].strip()
            or set(entry) - {'server', 'identity', 'username', 'passwordSecretRef'}):
        reject('configured registry entry has an unexpected structure')
    for field in ('identity', 'username', 'passwordSecretRef'):
        if entry.get(field) is not None and not isinstance(entry[field], str):
            reject('configured registry authentication has an unexpected structure')
entries = [entry for entry in registries if entry['server'].lower() == server.lower()]
if not entries:
    print('absent')
elif (len(entries) == 1 and entries[0].get('identity') == 'system'
      and not entries[0].get('username')
      and not entries[0].get('passwordSecretRef')):
    print('system')
else:
    sys.exit(f'{app}: existing registry entry for {server} uses username/password, '
             'another identity, or ambiguous authentication; refusing to overwrite it.')
PY
}
ACR_EXISTS=$(exists "$ACR_NAME" Microsoft.ContainerRegistry/registries)
ENV_EXISTS=$(exists "$ENV" Microsoft.App/managedEnvironments)
if [[ "$ACR_EXISTS" == true ]]; then
  [[ "$(azx acr show -n "$ACR_NAME" -g "$RG" --query sku.name -o tsv)" == Basic ]] || die 'Existing ACR is not Basic.'
  [[ "$(azx acr show -n "$ACR_NAME" -g "$RG" --query adminUserEnabled -o tsv)" == false ]] || die 'Existing ACR admin login is enabled; review separately.'
  mode=$(azx acr show -n "$ACR_NAME" -g "$RG" --query roleAssignmentMode -o tsv)
  [[ -z "$mode" || "$mode" == LegacyRegistryPermissions ]] || die 'Existing ACR uses ABAC; AcrPull/AcrPush are inappropriate.'
  [[ "$(azx acr config authentication-as-arm show -r "$ACR_NAME" --query status -o tsv)" == enabled ]] || die 'ACR ARM audience authentication must be enabled separately.'
else
  [[ "$(azx acr check-name -n "$ACR_NAME" --query nameAvailable -o tsv)" == true ]] || die 'ACR name is unavailable.'
fi
if [[ "$ENV_EXISTS" == true ]]; then
  logging_metadata=$(azx containerapp env show -n "$ENV" -g "$RG" --query properties.appLogsConfiguration -o json) \
    || die 'Could not inspect existing environment logging; refusing to continue.'
  LOGGING_METADATA="$logging_metadata" python3 - "$ENV" <<'PY'
import json, os, sys
def reject(reason):
    sys.exit(f'{sys.argv[1]}: invalid environment logging configuration ({reason}); review separately.')
try:
    configuration = json.loads(os.environ['LOGGING_METADATA'])
except json.JSONDecodeError:
    reject('empty or malformed JSON')
if (not isinstance(configuration, dict)
        or set(configuration) != {'destination', 'logAnalyticsConfiguration'}):
    reject('unexpected response structure')
if (configuration['destination'] not in ('none', None)
        or configuration['logAnalyticsConfiguration'] is not None):
    reject('expected disabled logging without a Log Analytics configuration')
PY
  [[ "$(azx containerapp env show -n "$ENV" -g "$RG" --query 'length(properties.workloadProfiles)' -o tsv)" == 1 ]] || die 'Existing environment must have only one Consumption profile.'
  [[ "$(azx containerapp env show -n "$ENV" -g "$RG" --query 'properties.workloadProfiles[0].workloadProfileType' -o tsv)" == Consumption ]] || die 'Existing environment is not Consumption-only.'
fi
for stage in dev qa; do
  app="${PREFIX}-${stage}"
  present=$(exists "$app" Microsoft.App/containerApps)
  if [[ "$present" == true ]]; then
    [[ "$(azx containerapp show -n "$app" -g "$RG" --query properties.environmentId -o tsv | tr '[:upper:]' '[:lower:]')" == "${ENV_ID,,}" ]] || die "$app belongs to another environment."
    [[ "$(azx containerapp show -n "$app" -g "$RG" --query properties.template.scale.minReplicas -o tsv)" == 0 ]] || die "$app min replicas differs."
    [[ "$(azx containerapp show -n "$app" -g "$RG" --query properties.template.scale.maxReplicas -o tsv)" == 1 ]] || die "$app max replicas differs."
    [[ -n "$(azx containerapp show -n "$app" -g "$RG" --query identity.principalId -o tsv)" ]] || die "$app needs its own system-assigned identity."
    server=$(azx acr show -n "$ACR_NAME" -g "$RG" --query loginServer -o tsv)
    binding=$(registry_binding_state "$app" "$server")
  fi
done

# Nothing above provisions resources. A fresh interactive confirmation is mandatory.
printf '\nSubscription: %s\nRegion: %s\nGroup: %s\nACR Basic: %s\nEnvironment: %s\nApps: %s-dev, %s-qa\n' "$AZURE_SUBSCRIPTION_ID" "$LOCATION" "$RG" "$ACR_NAME" "$ENV" "$PREFIX" "$PREFIX"
printf 'Reviewed all-in estimate: INR %s for 7 days; this is NOT a monthly spending estimate.\n' "$ESTIMATED_7_DAY_INR"
printf '%s\n' 'Monthly budget target remains INR 500/month; account for existing and other planned subscription spending.'
printf 'Intended manual cleanup deadline (UTC): %s\n' "$CLEANUP_DEADLINE_UTC"
printf '%s\n' 'Azure budgets are alerts, not spending caps; this script does not enforce a spending limit.'
printf '%s\n' 'No automatic cleanup is scheduled. Resources can continue billing after the deadline until manually removed.'
printf '%s\n' 'ACR has recurring charges. Startup, requests, egress, taxes and other subscription usage can exceed the estimate.'
printf '%s\n' 'Creates internal placeholder apps, min=0/max=1, 0.25 vCPU/0.5 GiB. No Log Analytics, dedicated profiles, Firewall, NAT Gateway or AKS.'
[[ -t 0 ]] || die 'Interactive terminal required; unattended provisioning is disabled.'
read -r -p "Type PROVISION $RG to approve these changes: " answer
[[ "$answer" == "PROVISION $RG" ]] || die 'Cancelled; no resources provisioned.'

# Only create absent resources. No delete/update of application revisions on rerun.
if [[ "$RG_EXISTS" != true ]]; then
  azx group create -n "$RG" -l "$LOCATION" --tags "provisionedBy=$OWNER" project=VeriKYB -o none
fi
if [[ "$ACR_EXISTS" != true ]]; then
  azx acr create -n "$ACR_NAME" -g "$RG" -l "$LOCATION" --sku Basic --admin-enabled false --role-assignment-mode rbac --tags "provisionedBy=$OWNER" -o none
fi
if [[ "$ENV_EXISTS" != true ]]; then
  azx containerapp env create -n "$ENV" -g "$RG" -l "$LOCATION" --enable-workload-profiles true --logs-destination none --tags "provisionedBy=$OWNER" -o none
fi

# Runtime identities receive pull-only access at this registry, never RG-wide roles.
ACR_SERVER=$(azx acr show -n "$ACR_NAME" -g "$RG" --query loginServer -o tsv)
for stage in dev qa; do
  app="${PREFIX}-${stage}"
  if [[ "$(exists "$app" Microsoft.App/containerApps)" != true ]]; then
    azx containerapp create -n "$app" -g "$RG" --environment "$ENV_ID" \
      --image mcr.microsoft.com/k8se/quickstart:latest \
      --workload-profile-name Consumption --cpu 0.25 --memory 0.5Gi \
      --min-replicas 0 --max-replicas 1 --ingress internal --target-port 80 \
      --revisions-mode single --system-assigned --tags "provisionedBy=$OWNER" "stage=$stage" -o none
  fi
  principal=$(azx containerapp show -n "$app" -g "$RG" --query identity.principalId -o tsv)
  [[ -n "$principal" ]] || die "No managed identity returned for $app."
  assignments=$(azx role assignment list --scope "$ACR_ID" --query "[?principalId=='$principal' && ends_with(roleDefinitionId, '/7f951dda-4ed3-4680-a7ca-43fe172d538d')].id" -o tsv)
  if [[ -z "$assignments" ]]; then
    azx role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal --role AcrPull --scope "$ACR_ID" -o none
  fi
  # Adding a missing binding is safe; an existing incompatible binding is untouched.
  binding=$(registry_binding_state "$app" "$ACR_SERVER")
  if [[ "$binding" == absent ]]; then
    azx containerapp registry set -n "$app" -g "$RG" --server "$ACR_SERVER" --identity system -o none
  fi
done

# Non-secret output only; no variable groups/service connections are created.
printf '\nAzure DevOps common variables (copy into reviewed pipeline configuration):\n'
printf 'AZURE_SUBSCRIPTION_ID=%s\nAZURE_RESOURCE_GROUP=%s\nAZURE_LOCATION=%s\nACR_NAME=%s\nACR_LOGIN_SERVER=%s\nACR_RESOURCE_ID=%s\nCONTAINER_APP_ENVIRONMENT_ID=%s\n' "$AZURE_SUBSCRIPTION_ID" "$RG" "$LOCATION" "$ACR_NAME" "$ACR_SERVER" "$ACR_ID" "$ENV_ID"
for stage in dev qa; do
  app="${PREFIX}-${stage}"
  printf '\n%s deployment variables:\nCONTAINER_APP_NAME=%s\nCONTAINER_APP_RESOURCE_ID=%s/providers/Microsoft.App/containerApps/%s\n' "$stage" "$app" "$RG_ID" "$app"
  printf 'RUNTIME_IDENTITY_PRINCIPAL_ID=%s\n' "$(azx containerapp show -n "$app" -g "$RG" --query identity.principalId -o tsv)"
done
printf '\nConfigure WIF service connections and narrowly scoped pipeline RBAC separately; see README.md.\n'
