# Copy a few music files of each format into a fresh scratch folder for smoke testing.
#
# Usage: scripts\dev\make_test_copies.ps1 -Source <music folder> [-PerFormat 3]
#   -Source     A music folder. The script only reads it and never changes a file there.
#   -PerFormat  Copies per format, 3 by default.
#
# The copies land in scratch\test-library\<UTC time>\, which .gitignore excludes. The script
# prints the destination on its last line.
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [ValidateRange(1, 1000)][int]$PerFormat = 3
)

if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    Write-Output "Pass an existing music folder with -Source."
    exit 2
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$dest = Join-Path $repoRoot "scratch\test-library\$stamp"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$copied = 0
foreach ($ext in @("mp3", "wav", "aif", "aiff", "flac", "m4a", "ogg")) {
    $files = Get-ChildItem -LiteralPath $Source -Recurse -File -Filter "*.$ext" |
        Sort-Object FullName | Select-Object -First $PerFormat
    $count = 0
    foreach ($file in $files) {
        $target = Join-Path $dest $file.Name
        if (-not (Test-Path -LiteralPath $target)) {
            Copy-Item -LiteralPath $file.FullName -Destination $target
            $count++
        }
    }
    Write-Output "${ext}: $count"
    $copied += $count
}
Write-Output "Copied $copied files to:"
Write-Output $dest
