<#
.SYNOPSIS
  One-shot Teams Transcriber dev environment setup for a new Windows PC.

.DESCRIPTION
  Installs prerequisites (Python 3.11, uv, Git, GitHub CLI) via winget,
  authenticates GitHub if needed, clones the repo, sets repo-local git
  identity, restores Claude Code memory from a zip, runs `uv sync`, and
  optionally launches the app for the first-run wizard.

  Safe to re-run: each step skips work that is already done.

  Requires Windows 10 1809+ (for winget) and PowerShell 5.1 or later.
  Does NOT require admin / UAC — all installs are per-user.

.PARAMETER RepoPath
  Where to clone the repo. Default: C:\dev\teams-transcriber.
  Important: the case of this path determines the Claude Code project
  slug (memory folder name), so match the path you'll actually use.

.PARAMETER MemoryZip
  Path to the Claude memory zip exported from the source PC.
  Default: <Desktop>\teams-transcriber-claude-memory.zip, where <Desktop>
  is resolved via the Windows shell folder API so OneDrive Known Folder
  Move (Desktop redirected to OneDrive\Desktop) is handled automatically.
  If the file isn't at the default, the script also checks the literal
  USERPROFILE\Desktop and USERPROFILE\OneDrive\Desktop paths.

.PARAMETER SkipAppLaunch
  Don't launch the app at the end (skip the first-run wizard). Useful
  on a headless machine or when you'll launch later.

.EXAMPLE
  # Default everything (repo at C:\dev\teams-transcriber, zip on Desktop):
  powershell -ExecutionPolicy Bypass -File .\setup_new_machine.ps1

.EXAMPLE
  # Custom paths:
  .\setup_new_machine.ps1 -RepoPath C:\Dev\teams-transcriber -MemoryZip D:\transfer\memory.zip

.NOTES
  If PowerShell blocks the script, run via:
    powershell -ExecutionPolicy Bypass -File .\setup_new_machine.ps1
#>
[CmdletBinding()]
param(
    [string]$RepoPath = "C:\dev\teams-transcriber",
    [string]$MemoryZip = (Join-Path ([Environment]::GetFolderPath('Desktop')) "teams-transcriber-claude-memory.zip"),
    [switch]$SkipAppLaunch
)

$ErrorActionPreference = 'Stop'

function Write-Step  ($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK    ($msg) { Write-Host "    [ok]   $msg" -ForegroundColor Green }
function Write-Skip  ($msg) { Write-Host "    [skip] $msg" -ForegroundColor DarkGray }
function Write-Warn  ($msg) { Write-Host "    [warn] $msg" -ForegroundColor Yellow }

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Reload-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

# ---------------------------------------------------------------------
# Step 1: prerequisites
# ---------------------------------------------------------------------
Write-Step "Checking prerequisites"

if (-not (Test-Command "winget")) {
    throw "winget is not available. Install 'App Installer' from the Microsoft Store first, then re-run this script."
}

$packages = @(
    @{ Id = "Python.Python.3.11"; Probe = "python" }
    @{ Id = "astral-sh.uv";       Probe = "uv"     }
    @{ Id = "Git.Git";            Probe = "git"    }
    @{ Id = "GitHub.cli";         Probe = "gh"     }
)

foreach ($p in $packages) {
    if (Test-Command $p.Probe) {
        Write-Skip "$($p.Id) already installed"
        continue
    }
    Write-Host "    Installing $($p.Id) via winget..."
    winget install --id $p.Id --silent `
        --accept-source-agreements --accept-package-agreements `
        --source winget
    if ($LASTEXITCODE -ne 0) {
        throw "winget install $($p.Id) failed (exit $LASTEXITCODE)"
    }
    Reload-Path
    if (-not (Test-Command $p.Probe)) {
        Write-Warn "$($p.Probe) still not on PATH after install. You may need to open a new PowerShell window and re-run."
    } else {
        Write-OK "$($p.Id) installed"
    }
}

# ---------------------------------------------------------------------
# Step 2: GitHub auth
# ---------------------------------------------------------------------
Write-Step "Checking GitHub authentication"

$ghStatus = & gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Not authenticated. Launching device-flow login..."
    Write-Host "    Sign in to your lewis.briang@gmail.com GitHub account in the browser."
    & gh auth login --hostname github.com --git-protocol https --web --skip-ssh-key
    if ($LASTEXITCODE -ne 0) { throw "gh auth login failed" }
    Write-OK "Authenticated"
} else {
    Write-Skip "Already authenticated to GitHub"
}

# ---------------------------------------------------------------------
# Step 3: clone repo
# ---------------------------------------------------------------------
Write-Step "Cloning repo to $RepoPath"

if (Test-Path (Join-Path $RepoPath ".git")) {
    Write-Skip "Repo already present at $RepoPath"
} else {
    $parent = Split-Path $RepoPath -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force $parent | Out-Null }
    & gh repo clone therealbiglou/teams-transcriber $RepoPath
    if ($LASTEXITCODE -ne 0) { throw "gh repo clone failed" }
    Write-OK "Cloned"
}

# ---------------------------------------------------------------------
# Step 4: git identity
# ---------------------------------------------------------------------
Write-Step "Setting repo-local git identity"

Push-Location $RepoPath
try {
    & git config user.email "lewis.briang@gmail.com"
    & git config user.name  "Brian Lewis"
    Write-OK "user.email=lewis.briang@gmail.com  user.name=Brian Lewis"
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------
# Step 5: restore Claude memory
# ---------------------------------------------------------------------
Write-Step "Restoring Claude Code memory"

# If the explicit path doesn't have the zip, search common Desktop locations
# (handles OneDrive Known Folder Move where Desktop is C:\Users\<u>\OneDrive\Desktop).
$zipPath = $MemoryZip
if (-not (Test-Path $zipPath)) {
    $candidates = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) "teams-transcriber-claude-memory.zip"),
        (Join-Path $env:USERPROFILE "Desktop\teams-transcriber-claude-memory.zip"),
        (Join-Path $env:USERPROFILE "OneDrive\Desktop\teams-transcriber-claude-memory.zip")
    ) | Select-Object -Unique
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) {
        Write-Host "    Found zip at fallback location: $found"
        $zipPath = $found
    }
}

if (-not (Test-Path $zipPath)) {
    Write-Warn "Memory zip not found. Searched:"
    Write-Warn "  - $MemoryZip"
    Write-Warn "  - $(Join-Path ([Environment]::GetFolderPath('Desktop')) 'teams-transcriber-claude-memory.zip')"
    Write-Warn "  - $env:USERPROFILE\Desktop\teams-transcriber-claude-memory.zip"
    Write-Warn "  - $env:USERPROFILE\OneDrive\Desktop\teams-transcriber-claude-memory.zip"
    Write-Warn "Skipping. Place the zip at one of those paths (or pass -MemoryZip <path>) and re-run."
} else {
    # Derive Claude Code project slug from the literal repo path.
    # e.g. C:\dev\teams-transcriber  ->  C--dev-teams-transcriber
    $slug = $RepoPath -replace ":\\", "--" -replace "\\", "-"
    $memDest = Join-Path $env:USERPROFILE ".claude\projects\$slug\memory"
    New-Item -ItemType Directory -Force $memDest | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $memDest -Force
    $count = (Get-ChildItem $memDest -File).Count
    Write-OK "Restored $count memory files to $memDest"
}

# ---------------------------------------------------------------------
# Step 6: uv sync
# ---------------------------------------------------------------------
Write-Step "Installing Python dependencies via uv (CUDA wheels ~3 GB; this takes a few minutes)"

Push-Location $RepoPath
try {
    & uv sync --all-extras
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
    Write-OK "Dependencies installed"
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------
# Step 7: launch app for first-run wizard
# ---------------------------------------------------------------------
if ($SkipAppLaunch) {
    Write-Step "Skipping app launch (-SkipAppLaunch)"
    Write-Host "    To launch later: cd $RepoPath; uv run python -m teams_transcriber"
} else {
    Write-Step "Launching app for first-run wizard"
    Write-Host "    Paste your Anthropic API key when the wizard prompts."
    Write-Host "    (Get one at https://console.anthropic.com/settings/keys)"
    Push-Location $RepoPath
    try {
        & uv run python -m teams_transcriber
    } finally {
        Pop-Location
    }
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Open Claude Code in $RepoPath to continue development with full context loaded." -ForegroundColor Green
