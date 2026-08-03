[CmdletBinding()]
param(
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Destination) { $Destination = Join-Path $root ".tools\k8s" }
$downloads = Join-Path $Destination "downloads"
New-Item -ItemType Directory -Force $Destination, $downloads | Out-Null

function Assert-Sha256 {
    param([string]$Path, [string]$Expected)
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA-256 verification failed for $Path"
    }
}

$helmVersion = "4.2.0"
$helmArchive = "helm-v$helmVersion-windows-amd64.zip"
$helmZip = Join-Path $downloads $helmArchive
$helmChecksum = Join-Path $downloads "$helmArchive.sha256sum"
Invoke-WebRequest -UseBasicParsing -Uri "https://get.helm.sh/$helmArchive" -OutFile $helmZip
Invoke-WebRequest -UseBasicParsing -Uri "https://get.helm.sh/$helmArchive.sha256sum" -OutFile $helmChecksum
$helmExpected = ((Get-Content $helmChecksum -Raw).Trim() -split '\s+')[0]
Assert-Sha256 $helmZip $helmExpected
$helmExtract = Join-Path $downloads "helm"
Expand-Archive -LiteralPath $helmZip -DestinationPath $helmExtract -Force
Copy-Item -LiteralPath (Join-Path $helmExtract "windows-amd64\helm.exe") -Destination (Join-Path $Destination "helm.exe") -Force

$kindVersion = "0.31.0"
$kindBinary = Join-Path $Destination "kind.exe"
$kindChecksum = Join-Path $downloads "kind.sha256sum"
Invoke-WebRequest -UseBasicParsing -Uri "https://kind.sigs.k8s.io/dl/v$kindVersion/kind-windows-amd64" -OutFile $kindBinary
Invoke-WebRequest -UseBasicParsing -Uri "https://kind.sigs.k8s.io/dl/v$kindVersion/kind-windows-amd64.sha256sum" -OutFile $kindChecksum
$kindExpected = ((Get-Content $kindChecksum -Raw).Trim() -split '\s+')[0]
Assert-Sha256 $kindBinary $kindExpected

$kubeconformVersion = "0.7.0"
$kubeconformArchive = "kubeconform-windows-amd64.zip"
$kubeconformZip = Join-Path $downloads $kubeconformArchive
$kubeconformChecksums = Join-Path $downloads "kubeconform-CHECKSUMS"
$kubeconformBase = "https://github.com/yannh/kubeconform/releases/download/v$kubeconformVersion"
Invoke-WebRequest -UseBasicParsing -Uri "$kubeconformBase/$kubeconformArchive" -OutFile $kubeconformZip
Invoke-WebRequest -UseBasicParsing -Uri "$kubeconformBase/CHECKSUMS" -OutFile $kubeconformChecksums
$checksumLine = Get-Content $kubeconformChecksums | Where-Object { $_ -match "\s$([regex]::Escape($kubeconformArchive))$" } | Select-Object -First 1
if (-not $checksumLine) { throw "Kubeconform checksum entry was not found." }
$kubeconformExpected = ($checksumLine -split '\s+')[0]
Assert-Sha256 $kubeconformZip $kubeconformExpected
Expand-Archive -LiteralPath $kubeconformZip -DestinationPath $Destination -Force

if (($env:Path -split ';') -notcontains $Destination) {
    $env:Path = "$Destination;$env:Path"
}

& (Join-Path $Destination "helm.exe") version --short
& (Join-Path $Destination "kind.exe") version
& (Join-Path $Destination "kubeconform.exe") -v
Write-Output "Pinned Kubernetes tools are installed under the ignored path $Destination."
