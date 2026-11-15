#!/usr/bin/env bash
# ==============================================================================
# publish_cloud_run.sh - Publish gensampledata2 workbench to Google Cloud Run
# ==============================================================================
# Designed for GCP project: ai-testbed-512518 (Region: us-central1)
# Enforces strict cost ceiling (<$20/mo) via scale-to-zero (min=0, max=2)
# Configures IAM & IAP invoker permissions for Identity-Aware Proxy security.
# ==============================================================================

set -euo pipefail

# Configuration defaults
PROJECT_ID="${PROJECT_ID:-ai-testbed-512518}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-gensampledata2}"
MIN_INSTANCES=0
MAX_INSTANCES=2
MEMORY="1Gi"
CPU="1"
INGRESS="${INGRESS:-all}"
ENABLE_IAP="${ENABLE_IAP:-true}"
ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "=================================================================="
echo " Cloud Run Deployment: ${SERVICE_NAME}"
echo " Project:       ${PROJECT_ID}"
echo " Region:        ${REGION}"
echo " Ingress:       ${INGRESS}"
echo " Cost Controls: min-instances=${MIN_INSTANCES}, max-instances=${MAX_INSTANCES}"
echo " Security:      IAP-compatible (no-allow-unauthenticated by default)"
echo " Working Dir:   ${SCRIPT_DIR}"
echo "=================================================================="

# Check gcloud tool
if ! command -v gcloud &>/dev/null; then
  echo "Error: 'gcloud' CLI is required but not found in PATH." >&2
  exit 1
fi

CURRENT_ACCOUNT="$(gcloud config get-value account 2>/dev/null || echo "")"
if [[ -z "${CURRENT_ACCOUNT}" ]]; then
  echo "Error: No active gcloud account. Run 'gcloud auth login' first." >&2
  exit 1
fi
echo "Active gcloud account: ${CURRENT_ACCOUNT}"

# Verify project access and get project number
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)" 2>/dev/null)" || {
  echo "Error: Cannot describe project ${PROJECT_ID}. Check permissions." >&2
  exit 1
}
echo "Project number:        ${PROJECT_NUMBER}"

# Ensure required APIs are enabled
REQUIRED_SERVICES=("run.googleapis.com" "cloudbuild.googleapis.com" "artifactregistry.googleapis.com" "iap.googleapis.com")
for svc in "${REQUIRED_SERVICES[@]}"; do
  if ! gcloud services list --project "${PROJECT_ID}" --enabled --filter="name:${svc}" --format="value(name)" | grep -q "${svc}"; then
    echo "Enabling service ${svc}..."
    gcloud services enable "${svc}" --project "${PROJECT_ID}"
  fi
done

# Authentication & IAP flags
AUTH_FLAG="--no-allow-unauthenticated"
if [[ "${ALLOW_UNAUTHENTICATED}" == "true" ]]; then
  AUTH_FLAG="--allow-unauthenticated"
fi

IAP_FLAG="--iap"
if [[ "${ENABLE_IAP}" == "false" ]]; then
  IAP_FLAG="--no-iap"
fi

echo ""
echo "Building container and deploying to Cloud Run via Cloud Build..."
echo "------------------------------------------------------------------"

gcloud run deploy "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source . \
  --min-instances "${MIN_INSTANCES}" \
  --max-instances "${MAX_INSTANCES}" \
  --memory "${MEMORY}" \
  --cpu "${CPU}" \
  --concurrency 80 \
  --timeout 300s \
  --ingress "${INGRESS}" \
  ${AUTH_FLAG} \
  ${IAP_FLAG} \
  --set-env-vars "ALLOWED_HOSTS=*,HOST=0.0.0.0,DATA_DIR=/app/artifacts/workbench-data"

echo ""
echo "Configuring IAM invoker permissions for IAP & Admin..."
echo "------------------------------------------------------------------"

# Grant IAP service account permission to invoke Cloud Run
IAP_SA="service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com"
echo "Granting roles/run.invoker to IAP service account (${IAP_SA})..."
gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --member "serviceAccount:${IAP_SA}" \
  --role "roles/run.invoker" 2>/dev/null || echo "Note: IAP service account binding skipped or already present."

# Grant current deployer permission to test invoke
if [[ -n "${CURRENT_ACCOUNT}" ]]; then
  echo "Granting roles/run.invoker to deployer (user:${CURRENT_ACCOUNT})..."
  gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --member "user:${CURRENT_ACCOUNT}" \
    --role "roles/run.invoker" 2>/dev/null || true
fi

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --project "${PROJECT_ID}" --region "${REGION}" --format="value(status.url)")"

echo ""
echo "=================================================================="
echo " Deployment Complete!"
echo " Service URL: ${SERVICE_URL}"
echo ""
echo " How to test with IAM authentication:"
echo "   curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" ${SERVICE_URL}/api/meta"
echo ""
echo " How to open locally with authenticated proxy:"
echo "   gcloud run services proxy ${SERVICE_NAME} --project ${PROJECT_ID} --region ${REGION} --port 8767"
echo "   then visit: http://127.0.0.1:8767"
echo ""
echo " Direct IAP Access:"
echo "   Visit: ${SERVICE_URL} in your browser."
echo "   IAP intercepts requests directly on *.run.app and authenticates via Google OAuth."
echo "=================================================================="
