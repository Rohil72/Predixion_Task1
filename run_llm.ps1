param(
    [switch]$Local,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Prompt
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "call_llm.py"
$launcherScript = Join-Path $scriptDir "run_llm.ps1"

if (-not (Test-Path $pythonScript)) {
    Write-Error "Could not find call_llm.py in $scriptDir"
    exit 1
}

function Invoke-LlmLocally {
    Set-Location -LiteralPath $scriptDir
    & python $pythonScript @Prompt
    exit $LASTEXITCODE
}

if ($Local) {
    Invoke-LlmLocally
}

$launchErrors = @()

foreach ($shellExe in @("powershell.exe", "pwsh.exe")) {
    try {
        $argumentList = @(
            "-NoExit",
            "-ExecutionPolicy", "Bypass",
            "-File", $launcherScript,
            "--local"
        )
        $argumentList += $Prompt

        $process = Start-Process -FilePath $shellExe -ArgumentList $argumentList -WorkingDirectory $scriptDir -PassThru -ErrorAction Stop

        if ($process) {
            exit 0
        }

        throw "Launcher process was not created."
    } catch {
        $launchErrors += "${shellExe}: $($_.Exception.Message)"
    }
}

Write-Warning "Could not open a separate PowerShell window. Running in the current terminal instead."
if ($launchErrors.Count -gt 0) {
    Write-Warning ($launchErrors -join " | ")
}

Invoke-LlmLocally
