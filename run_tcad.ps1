param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsList
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Root "tcad_simulator.py"
$Py = $env:PYTHON

if ($Py) {
    & $Py $Script @ArgsList
    exit $LASTEXITCODE
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
    & py -3 $Script @ArgsList
    exit $LASTEXITCODE
}

& python $Script @ArgsList
exit $LASTEXITCODE
