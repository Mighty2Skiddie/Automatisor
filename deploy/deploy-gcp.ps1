<#
.SYNOPSIS
    Deploy the Sector Analyst backend (API + MCP) to Google Cloud Run as "mindtraqk".

.DESCRIPTION
    Idempotent: safe to re-run. Creates the project only if it does not exist, enables
    the APIs it needs, builds the container with Cloud Build, and deploys a Cloud Run
    service. Secrets are passed as environment variables at deploy time and never baked
    into the image.

    Cloud Run is the right host for this workload for one specific reason: a query takes
    60-100 seconds, and Cloud Run allows request timeouts up to 60 minutes. Most free
    serverless tiers cap out well below that.

.PARAMETER ProjectId
    GCP project id. Defaults to "mindtraqk".

.PARAMETER Region
    Cloud Run region. Defaults to asia-south1 (Mumbai).

.PARAMETER MinInstances
    Keep this many instances warm. 0 is free but means a ~30s cold start on the first
    request, which lands on top of the 60-100s query. Use 1 for a live demo.

.EXAMPLE
    gcloud auth login
    .\deploy\deploy-gcp.ps1
    .\deploy\deploy-gcp.ps1 -MinInstances 1
#>

[CmdletBinding()]
param(
    [string]$ProjectId = "mindtraqk",
    [string]$Region = "asia-south1",
    [string]$ServiceName = "mindtraqk",
    [int]$MinInstances = 0,
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

# --- preflight -------------------------------------------------------------------

Write-Step "Checking gcloud authentication"
$account = (gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null)
if (-not $account) {
    throw "Not authenticated. Run: gcloud auth login"
}
Write-Host "    authenticated as $account"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path $EnvFile)) {
    throw "$EnvFile not found. Copy .env.example to .env and add GOOGLE_API_KEY."
}

# Read the keys the service needs. They are passed to Cloud Run as env vars, never
# committed and never copied into an image layer.
$envMap = @{}
foreach ($line in Get-Content $EnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
    $parts = $trimmed.Split("=", 2)
    $envMap[$parts[0].Trim()] = $parts[1].Trim()
}

if (-not $envMap["GOOGLE_API_KEY"]) {
    throw "GOOGLE_API_KEY is empty in $EnvFile. The service cannot answer without it."
}

# --- project ---------------------------------------------------------------------

Write-Step "Ensuring project '$ProjectId' exists"
$existing = (gcloud projects describe $ProjectId --format="value(projectId)" 2>$null)
if ($existing -eq $ProjectId) {
    Write-Host "    project already exists"
} else {
    Write-Host "    creating project $ProjectId"
    gcloud projects create $ProjectId --name="mindtraqk" | Out-Null
    Write-Host "    NOTE: a project needs a billing account linked before Cloud Run and" -ForegroundColor Yellow
    Write-Host "    Cloud Build will work, even to stay inside the always-free tier." -ForegroundColor Yellow
    Write-Host "    Link one at: https://console.cloud.google.com/billing/linkedaccount?project=$ProjectId" -ForegroundColor Yellow
}

gcloud config set project $ProjectId | Out-Null

Write-Step "Enabling required APIs (idempotent, may take a minute)"
gcloud services enable `
    run.googleapis.com `
    cloudbuild.googleapis.com `
    artifactregistry.googleapis.com `
    --project $ProjectId | Out-Null
Write-Host "    run, cloudbuild, artifactregistry enabled"

# --- build -----------------------------------------------------------------------

$image = "gcr.io/$ProjectId/${ServiceName}:latest"

Write-Step "Building container with Cloud Build -> $image"
Write-Host "    this takes ~5-10 minutes on a cold cache (installing ~100 pinned packages)"
# --config, not --tag: the two are mutually exclusive, and the Cloud Run image is
# built from deploy/Dockerfile.cloudrun rather than the root Dockerfile.
gcloud builds submit `
    --project $ProjectId `
    --config deploy/cloudbuild.yaml `
    --substitutions "_SERVICE=$ServiceName" `
    --ignore-file deploy/.gcloudignore `
    .

# --- deploy ----------------------------------------------------------------------

# Written as a YAML file rather than --set-env-vars, because gcloud splits that flag
# on commas and CORS_ORIGINS is itself a comma-separated list of origins. Passing it
# inline silently truncates the allowed origins to the first one and the deployed
# frontend then fails CORS with no error on the server side.
$envYaml = [System.IO.Path]::GetTempFileName()
$envLines = @()
function Add-EnvLine($name, $value) {
    if ($value) { $script:envLines += "${name}: `"$value`"" }
}
Add-EnvLine "LLM_MODEL"           $envMap["LLM_MODEL"]
Add-EnvLine "GOOGLE_API_KEY"      $envMap["GOOGLE_API_KEY"]
Add-EnvLine "GROQ_API_KEY"        $envMap["GROQ_API_KEY"]
Add-EnvLine "GROQ_MODEL"          $envMap["GROQ_MODEL"]
Add-EnvLine "LANGFUSE_PUBLIC_KEY" $envMap["LANGFUSE_PUBLIC_KEY"]
Add-EnvLine "LANGFUSE_SECRET_KEY" $envMap["LANGFUSE_SECRET_KEY"]
Add-EnvLine "LANGFUSE_HOST"       $envMap["LANGFUSE_HOST"]
# The Vercel frontend calls this service from the browser, so its origin must be
# allowed. "*" is not usable here because the API sends credentials.
Add-EnvLine "CORS_ORIGINS"        $envMap["CORS_ORIGINS"]
Set-Content -Path $envYaml -Value ($envLines -join "`n") -Encoding utf8

Write-Step "Deploying Cloud Run service '$ServiceName' in $Region"
gcloud run deploy $ServiceName `
    --image $image `
    --project $ProjectId `
    --region $Region `
    --platform managed `
    --allow-unauthenticated `
    --port 8080 `
    --memory 1Gi `
    --cpu 1 `
    --concurrency 4 `
    --timeout 900 `
    --min-instances $MinInstances `
    --max-instances 3 `
    --env-vars-file $envYaml

Remove-Item $envYaml -Force -ErrorAction SilentlyContinue

$url = (gcloud run services describe $ServiceName --project $ProjectId --region $Region --format="value(status.url)")

Write-Step "Deployed"
Write-Host "    service : $ServiceName"
Write-Host "    url     : $url"
Write-Host "    health  : $url/healthz"
Write-Host "    docs    : $url/docs"
Write-Host ""
Write-Host "Next: point the frontend at it and deploy to Vercel:" -ForegroundColor Cyan
Write-Host "    cd web"
Write-Host "    vercel --prod -e NEXT_PUBLIC_API_URL=$url"
Write-Host ""
Write-Host "Then add the Vercel origin to CORS_ORIGINS and re-run this script." -ForegroundColor Yellow
