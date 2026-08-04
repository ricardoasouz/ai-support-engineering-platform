[CmdletBinding()]
param(
    [string]$ClusterName = "ai-support-phase8"
)

$ErrorActionPreference = "Stop"
$kind = Get-Command kind -ErrorAction Stop
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$config = Join-Path $root "deploy\kind\cluster.yaml"
$nodeImage = "kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f"

$clusters = @(& $kind.Source get clusters)
if ($clusters -contains $ClusterName) {
    Write-Output "Kind cluster '$ClusterName' already exists."
    exit 0
}

& $kind.Source create cluster --name $ClusterName --image $nodeImage --config $config --wait 180s
if ($LASTEXITCODE -ne 0) { throw "Kind cluster creation failed." }
kubectl cluster-info --context "kind-$ClusterName"
