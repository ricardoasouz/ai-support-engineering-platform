[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ClusterName = "ai-support-phase8"
)

$ErrorActionPreference = "Stop"
if ($ClusterName -notmatch '^ai-support-phase8(?:-[a-z0-9-]+)?$') {
    throw "Refusing to delete a cluster outside the dedicated ai-support-phase8 naming scope."
}
if ($PSCmdlet.ShouldProcess($ClusterName, "Delete isolated Kind cluster")) {
    kind delete cluster --name $ClusterName
    if ($LASTEXITCODE -ne 0) { throw "Kind cluster deletion failed." }
}
