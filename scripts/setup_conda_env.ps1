<#
.SYNOPSIS
Creates the local-product-search Conda environment for Windows and validates CUDA.

.DESCRIPTION
Installs a Python 3.11 Conda environment, CUDA 12.8 PyTorch wheels, and the
libraries used by the FastAPI + LangGraph + Ollama product-search application.

The script does not install Ollama automatically. Ollama is a Windows program
outside the Conda environment. Use -PullModel after Ollama is installed and
running to download the selected model.

.EXAMPLE
.\scripts\setup_conda_env.ps1

.EXAMPLE
.\scripts\setup_conda_env.ps1 -PullModel

.EXAMPLE
.\scripts\setup_conda_env.ps1 -EnvironmentName my-product-search -ModelName llama3.2:3b -PullModel
#>
[CmdletBinding()]
param(
    [string]$EnvironmentName = "local-product-search",
    [string]$PythonVersion = "3.11",
    # Llama 3.2 has 1B and 3B variants rather than an exact 2B variant.
    [string]$ModelName = "llama3.2:3b",
    [switch]$PullModel,
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

function Invoke-Conda {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $script:CondaCommand @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Conda command failed: conda $($Arguments -join ' ')"
    }
}

function Invoke-InEnvironment {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $condaRunArguments = @("run", "--no-capture-output", "-n", $EnvironmentName) + $Arguments
    Invoke-Conda -Arguments $condaRunArguments
}

$conda = Get-Command conda -ErrorAction SilentlyContinue
if ($null -eq $conda) {
    throw @"
Conda was not found in PATH.
Install Miniconda or Anaconda, then open a new PowerShell window and run this script again.
"@
}
$script:CondaCommand = $conda.Source

$environmentExists = $false
$environmentList = & $script:CondaCommand env list --json | ConvertFrom-Json
foreach ($prefix in $environmentList.envs) {
    if ((Split-Path -Leaf $prefix) -eq $EnvironmentName) {
        $environmentExists = $true
        break
    }
}

if ($environmentExists -and $Recreate) {
    Write-Host "Removing existing Conda environment '$EnvironmentName'..."
    Invoke-Conda -Arguments @("env", "remove", "--yes", "-n", $EnvironmentName)
    $environmentExists = $false
}

if (-not $environmentExists) {
    Write-Host "Creating Conda environment '$EnvironmentName' with Python $PythonVersion..."
    Invoke-Conda -Arguments @("create", "--yes", "-n", $EnvironmentName, "python=$PythonVersion", "pip")
}
else {
    Write-Host "Using existing Conda environment '$EnvironmentName'."
}

Write-Host "Upgrading pip tooling..."
Invoke-InEnvironment -Arguments @("python", "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")

Write-Host "Installing PyTorch with CUDA 12.8 support..."
# These versions are the matched PyTorch CUDA 12.8 wheel set for Windows/Linux.
Invoke-InEnvironment -Arguments @(
    "python", "-m", "pip", "install",
    "torch==2.10.0", "torchvision==0.25.0", "torchaudio==2.10.0",
    "--index-url", "https://download.pytorch.org/whl/cu128"
)

$requirementsPath = Join-Path $PSScriptRoot "..\requirements.txt"
Write-Host "Installing application libraries from $requirementsPath..."
Invoke-InEnvironment -Arguments @("python", "-m", "pip", "install", "-r", $requirementsPath)

Write-Host "`nVerifying Python, PyTorch, and CUDA..."
$verificationCode = @'
import sys
import torch

print(f"Python: {sys.version.split()[0]}")
print(f"PyTorch: {torch.__version__}")
print(f"PyTorch CUDA build: {torch.version.cuda}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"GPU memory: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
else:
    print("CUDA was not detected. Update the NVIDIA driver, then run this test again.")
'@
Invoke-InEnvironment -Arguments @("python", "-c", $verificationCode)

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($PullModel) {
    if ($null -eq $ollama) {
        throw "-PullModel was requested, but Ollama is not installed or is not in PATH. Install Ollama, restart PowerShell, and rerun with -PullModel."
    }

    Write-Host "`nDownloading local model '$ModelName' with Ollama..."
    & $ollama.Source pull $ModelName
    if ($LASTEXITCODE -ne 0) {
        throw "Ollama could not download '$ModelName'. Make sure the Ollama service is running."
    }
}
elseif ($null -eq $ollama) {
    Write-Host @"

Ollama is not installed yet. Install it from https://ollama.com/download/windows.
After installation, open a new PowerShell window and run:
  ollama pull $ModelName
"@
}
else {
    Write-Host @"

Ollama was found. To download the default model, run:
  ollama pull $ModelName
or rerun this script with -PullModel.
"@
}

Write-Host @"

Setup complete.

Activate the environment with:
  conda activate $EnvironmentName
"@
