param(
    [string]$Src = "tcad_simulator.py",
    [string]$Out = "tcad_simulator_split"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tool = Join-Path $Root "tools\split_tcad.py"
$Py = $env:PYTHON

if ($Py) {
    & $Py $Tool --src $Src --out $Out --clean --dedupe conservative
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Py $Tool --out $Out --verify
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        & py -3 $Tool --src $Src --out $Out --clean --dedupe conservative
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & py -3 $Tool --out $Out --verify
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        & python $Tool --src $Src --out $Out --clean --dedupe conservative
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & python $Tool --out $Out --verify
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

Write-Host "Split complete: $Out"
Write-Host "Docs: $Out/docs"
Write-Host "Docs HTML: $Out/docs_html/index.html"
Write-Host "Report: $Out/SPLIT_REPORT.json"
