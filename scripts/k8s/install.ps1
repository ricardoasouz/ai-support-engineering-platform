[CmdletBinding()]
param(
    [string]$ReleaseName = "ai-support",
    [string]$Namespace = "ai-support-phase8",
    [string]$ImageTag = "dev",
    [switch]$UseFakeProviders,
    [switch]$DisableObservability
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$chart = Join-Path $root "deploy\helm\ai-support-platform"
$baseValues = Join-Path $chart "values-dev.yaml"
$ciValues = Join-Path $chart "values-ci.yaml"
$secretName = "ai-support-platform-secrets"

$randomBytes = New-Object byte[] 24
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $random.GetBytes($randomBytes) } finally { $random.Dispose() }
$password = (($randomBytes | ForEach-Object { $_.ToString("x2") }) -join "")
$databaseUrl = "postgresql+psycopg://support_user:$password@$ReleaseName-postgres:5432/support_platform"

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null
kubectl label namespace $Namespace `
    pod-security.kubernetes.io/enforce=restricted `
    pod-security.kubernetes.io/audit=restricted `
    pod-security.kubernetes.io/warn=restricted `
    --overwrite | Out-Null
kubectl -n $Namespace create secret generic $secretName `
    --from-literal="database-url=$databaseUrl" `
    --from-literal="postgres-password=$password" `
    --from-literal="grafana-admin-password=$password" `
    --dry-run=client -o yaml | kubectl apply -f - | Out-Null

$helmArgs = @(
    "upgrade", "--install", $ReleaseName, $chart,
    "--namespace", $Namespace,
    "--values", $baseValues,
    "--set-string", "fullnameOverride=$ReleaseName",
    "--set-string", "image.tag=$ImageTag",
    "--wait", "--wait-for-jobs", "--timeout", "30m"
)
if ($UseFakeProviders) {
    $helmArgs += @("--values", $ciValues, "--set-string", "image.tag=$ImageTag")
}
if ($DisableObservability) {
    $helmArgs += @("--set", "observability.enabled=false", "--set", "config.telemetry.tracesExporter=none", "--set", "config.telemetry.metricsExporter=none")
}

helm @helmArgs
if ($LASTEXITCODE -ne 0) { throw "Helm installation failed." }
kubectl wait --namespace $Namespace --for=condition=Available deployment/$ReleaseName-api --timeout=300s
kubectl wait --namespace $Namespace --for=condition=Available deployment/$ReleaseName-worker --timeout=300s
kubectl get pods,pvc --namespace $Namespace -l "app.kubernetes.io/instance=$ReleaseName"
