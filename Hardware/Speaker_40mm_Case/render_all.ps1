<#
    Batch-export every output of speaker_case_40mm.scad.

    Requires the OpenSCAD CLI. Either put it on PATH, or pass the path:
        .\render_all.ps1 -OpenSCAD "C:\Program Files\OpenSCAD\openscad.com"

    The per-part selection is done with tiny wrapper files that set PART and
    include the model.  They are written WITHOUT a UTF-8 BOM on purpose —
    OpenSCAD's parser rejects a file that starts with a BOM.
#>
param(
    [string]$OpenSCAD = "openscad",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutputDir) { $OutputDir = Join-Path $here "export" }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$targets = [ordered]@{
    front       = "part_A_front_bezel.stl"    # print: front bezel
    back        = "part_B_back_cup.stl"       # print: back cup, print orientation
    all         = "print_plate.stl"           # both parts, one plate
    assembly    = "assembly.stl"              # reference only
    exploded    = "exploded.stl"              # reference only
    section     = "section_case.stl"          # half-section, reference only
    cavity      = "cavity.stl"                # net trapped air volume
    driver      = "driver_ghost.stl"          # driver keep-out, half
    driver_full = "driver_full.stl"           # driver keep-out, full
    sec2d       = "section_2d_case.dxf"       # 2D cross-section (case)
    sec2d_drv   = "section_2d_driver.dxf"     # 2D cross-section (driver)
}

$enc = New-Object System.Text.UTF8Encoding($false)
$failed = 0

foreach ($key in $targets.Keys) {
    $wrapper = Join-Path $here "build_$key.scad"
    [System.IO.File]::WriteAllText(
        $wrapper,
        "PART = `"$key`";`r`ninclude <speaker_case_40mm.scad>`r`n",
        $enc)
    $dest = Join-Path $OutputDir $targets[$key]
    Write-Host ("{0,-12} -> {1}" -f $key, $targets[$key])
    & $OpenSCAD -o $dest $wrapper
    if ($LASTEXITCODE -ne 0) { $failed++ }
    Remove-Item $wrapper -Force
}

if ($failed) {
    Write-Warning "$failed export(s) failed."
    exit 1
}
Write-Host "`nAll exports written to $OutputDir"
