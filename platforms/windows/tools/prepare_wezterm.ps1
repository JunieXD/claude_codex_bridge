<# Create an isolated WezTerm copy with the signed ConPTY pair shipped by Herdr.
   Original installation is never overwritten. Close its windows before switching
   shortcuts to the new copy; changing files cannot update an existing console. #>
param(
    [Parameter(Mandatory)][string]$WezTermRoot,
    [Parameter(Mandatory)][string]$HerdrConptyRoot,
    [Parameter(Mandatory)][string]$Destination
)
$ErrorActionPreference = 'Stop'
$original = (Resolve-Path -LiteralPath $WezTermRoot).Path.TrimEnd('\')
$conpty = (Resolve-Path -LiteralPath $HerdrConptyRoot).Path.TrimEnd('\')
$target = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
if (Test-Path -LiteralPath $target) { throw "Destination already exists: $target" }
foreach ($sourceRoot in @($original, $conpty)) {
    if ($target.Equals($sourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $target.StartsWith($sourceRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Destination must be outside the source directories.'
    }
}
foreach ($name in @('wezterm.exe', 'wezterm-gui.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $original $name) -PathType Leaf)) {
        throw "Missing WezTerm binary: $name"
    }
}
$dll = Join-Path $conpty 'conpty.dll'
$hostExe = Join-Path $conpty 'x64\OpenConsole.exe'
foreach ($path in @($dll, $hostExe)) {
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw "Expected a valid Microsoft-signed ConPTY component: $path"
    }
}
$version = (Get-Item -LiteralPath $dll).VersionInfo.FileVersion
if ($version -ne (Get-Item -LiteralPath $hostExe).VersionInfo.FileVersion) {
    throw 'ConPTY DLL and OpenConsole must be a matching package pair.'
}
Copy-Item -LiteralPath $original -Destination $target -Recurse
Copy-Item -LiteralPath $dll -Destination (Join-Path $target 'conpty.dll') -Force
# WezTerm's 2024 build locates OpenConsole beside its own EXE.
Copy-Item -LiteralPath $hostExe -Destination (Join-Path $target 'OpenConsole.exe') -Force
# Preserve the package layout as well for newer DLL host discovery.
Copy-Item -LiteralPath (Join-Path $conpty 'x64') -Destination $target -Recurse -Force
$files = foreach ($name in @('wezterm.exe', 'wezterm-gui.exe', 'conpty.dll', 'OpenConsole.exe')) {
    @{ path = $name; sha256 = (Get-FileHash -LiteralPath (Join-Path $target $name) -Algorithm SHA256).Hash }
}
@{ original = $original; conpty_source = $conpty; conpty_version = $version; files = @($files) } |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $target 'CCB-CONPTY.json') -Encoding UTF8
Write-Host "Prepared isolated native Windows terminal: $target (ConPTY $version)"
