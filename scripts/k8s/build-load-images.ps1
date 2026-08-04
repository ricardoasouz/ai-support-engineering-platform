[CmdletBinding()]
param(
    [string]$ClusterName = "ai-support-phase8",
    [string]$ImageTag = "dev"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$version = (Get-Content (Join-Path $root "VERSION") -Raw).Trim()
$gitSha = (git -C $root rev-parse HEAD).Trim()
$buildTime = (git -C $root show -s --format=%cI HEAD).Trim()
$apiImage = "ai-support-platform-api:$ImageTag"
$workerImage = "ai-support-platform-worker:$ImageTag"

docker build --build-arg "APP_VERSION=$version" --build-arg "GIT_SHA=$gitSha" --build-arg "BUILD_TIME=$buildTime" --tag $apiImage $root
if ($LASTEXITCODE -ne 0) { throw "API image build failed." }
docker tag $apiImage $workerImage
kind load docker-image --name $ClusterName $apiImage $workerImage
if ($LASTEXITCODE -ne 0) { throw "Loading local images into Kind failed." }
