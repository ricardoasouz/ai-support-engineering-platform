[CmdletBinding()]
param(
    [string]$ReleaseName = "ai-support",
    [string]$Namespace = "ai-support-phase8",
    [int]$LocalPort = 18080,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$portForward = Start-Process kubectl -ArgumentList @(
    "port-forward", "--namespace", $Namespace,
    "service/$ReleaseName-api", "${LocalPort}:8000"
) -WindowStyle Hidden -PassThru

try {
    $baseUrl = "http://127.0.0.1:$LocalPort"
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 3
            if ($health.status -eq "healthy") { break }
        } catch {
            Start-Sleep -Seconds 1
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($health.status -ne "healthy") { throw "API did not become healthy through port-forward." }

    $build = Invoke-RestMethod -Uri "$baseUrl/build" -TimeoutSec 5
    if (-not $build.version) { throw "Build metadata is missing." }

    $service = "k8s-smoke-$([Guid]::NewGuid().ToString('N').Substring(0,10))"
    $payload = @{
        service = $service
        error = "PostgreSQL connection pool exhausted"
        log = "pool timeout while waiting for an available database connection"
        severity = "critical"
    } | ConvertTo-Json
    $analysis = Invoke-RestMethod -Uri "$baseUrl/api/v1/incidents" -Method Post -ContentType "application/json" -Body $payload
    if ($analysis.classification -ne "database_connection_error") { throw "Unexpected incident classification." }

    $incidents = @(Invoke-RestMethod -Uri "$baseUrl/api/v1/incidents?service=$service")
    $incident = $incidents[0]
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $execution = Invoke-RestMethod -Uri "$baseUrl/api/v1/incidents/$($incident.id)/agent-execution" -TimeoutSec 5
            $resolution = Invoke-RestMethod -Uri "$baseUrl/api/v1/incidents/$($incident.id)/resolution" -TimeoutSec 5
            if ($execution.status -eq "awaiting_review") { break }
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    if ($execution.status -ne "awaiting_review") { throw "Agent execution did not reach awaiting_review." }
    if ($resolution.status -ne "completed" -or @($resolution.cited_sources).Count -eq 0) { throw "Grounded resolution validation failed." }

    $review = @{reviewer="phase8-smoke"; rating=5; comment="Kubernetes smoke validation"} | ConvertTo-Json
    $approval = Invoke-RestMethod -Uri "$baseUrl/api/v1/incidents/$($incident.id)/resolution/approve" -Method Post -ContentType "application/json" -Body $review
    if ($approval.outcome -ne "approved") { throw "Human review endpoint validation failed." }
    Write-Output "Kubernetes smoke passed for incident $($incident.id); version $($build.version)."
} finally {
    if ($portForward -and -not $portForward.HasExited) {
        Stop-Process -Id $portForward.Id -Force
    }
}
