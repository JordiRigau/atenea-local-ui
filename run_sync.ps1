param(
    [switch]$GitPush
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

python sync_atenea.py
$syncExit = $LASTEXITCODE

python build_index.py

if ($GitPush) {
    git add -A
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        $count = (git diff --cached --name-only | Measure-Object -Line).Lines
        git commit -q -m "Sync Atenea $(Get-Date -Format 'yyyy-MM-dd') ($count fitxers)"
        git push -q
        if ($LASTEXITCODE -ne 0) {
            Add-Content sync.log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ERROR: git push ha fallat"
        }
    }
}

exit $syncExit
