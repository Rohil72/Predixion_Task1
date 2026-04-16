param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Prompt
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "call_llm.py"
$researchAgentScript = Join-Path $scriptDir "research_agent.py"
$orchestratorScript = Join-Path $scriptDir "agent_cli.py"
$formatterScript = Join-Path $scriptDir "formatter_agent.py"
$rendererScript = Join-Path $scriptDir "renderer\render_report.py"
$serverScript = Join-Path $scriptDir "renderer\serve_reports.py"
$reportUrl = "http://127.0.0.1:8123/latest_report.html"

if (-not (Test-Path $pythonScript)) {
    Write-Error "Could not find call_llm.py in $scriptDir"
    exit 1
}

if (-not (Test-Path $formatterScript)) {
    Write-Error "Could not find formatter_agent.py in $scriptDir"
    exit 1
}

if (-not (Test-Path $researchAgentScript)) {
    Write-Error "Could not find research_agent.py in $scriptDir"
    exit 1
}

if (-not (Test-Path $orchestratorScript)) {
    Write-Error "Could not find agent_cli.py in $scriptDir"
    exit 1
}

if (-not (Test-Path $rendererScript)) {
    Write-Error "Could not find renderer\\render_report.py in $scriptDir"
    exit 1
}

if (-not (Test-Path $serverScript)) {
    Write-Error "Could not find renderer\\serve_reports.py in $scriptDir"
    exit 1
}

function Test-PortOpen {
    param(
        [string]$HostName,
        [int]$Port
    )

    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(500)
        if (-not $connected) {
            $client.Close()
            return $false
        }
        $client.EndConnect($async)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Ensure-ReportServer {
    if (Test-PortOpen -HostName "127.0.0.1" -Port 8123) {
        return
    }

    Start-Process -FilePath "python" -ArgumentList @($serverScript) -WorkingDirectory $scriptDir -WindowStyle Hidden | Out-Null
    Start-Sleep -Milliseconds 1200
}

Set-Location -LiteralPath $scriptDir

if ($Prompt.Count -eq 0) {
    & python $pythonScript
    exit $LASTEXITCODE
}

$question = $Prompt -join " "
$rawOutput = & python $orchestratorScript @Prompt
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$formattedOutput = $rawOutput | & python $formatterScript --question $question
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$renderOutput = $formattedOutput | & python $rendererScript
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Ensure-ReportServer
Start-Process $reportUrl | Out-Null

Write-Output $reportUrl
