# Run Claude CLI routed to AgentRouter (agentrouter.org)
# Usage: .\run_claude_agentrouter.ps1
#        .\run_claude_agentrouter.ps1 -Model "claude-opus-4-8"
param(
    [switch]$Logout,
    [string]$Model = "claude-opus-4-8"
)

$env:ANTHROPIC_BASE_URL = "https://agentrouter.org"
$env:ANTHROPIC_API_KEY = "sk-t2oWvWH3pYxUZvBJUBPJBnsIRUWgRWcsA5rpHMNwtkpUtptj"
$env:ANTHROPIC_AUTH_TOKEN = "sk-t2oWvWH3pYxUZvBJUBPJBnsIRUWgRWcsA5rpHMNwtkpUtptj"
$env:ANTHROPIC_MODEL = $Model
$env:ANTHROPIC_SMALL_FAST_MODEL = $Model
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"

if ($Logout) {
    Write-Host "Logging out of any existing Anthropic session..." -ForegroundColor Yellow
    claude.cmd /logout
    return
}

# Automatically sync working model into settings.json
foreach ($path in @("c:\code\makima\.claude\settings.json", "$env:USERPROFILE\.claude\settings.json")) {
    if (Test-Path $path) {
        try {
            $json = Get-Content $path -Raw | ConvertFrom-Json
            $json.model = $Model
            if ($json.env) {
                $json.env.ANTHROPIC_MODEL = $Model
                $json.env.ANTHROPIC_SMALL_FAST_MODEL = $Model
                $json.env.ANTHROPIC_BASE_URL = "https://agentrouter.org"
                $json.env.ANTHROPIC_API_KEY = "sk-t2oWvWH3pYxUZvBJUBPJBnsIRUWgRWcsA5rpHMNwtkpUtptj"
                $json.env.ANTHROPIC_AUTH_TOKEN = "sk-t2oWvWH3pYxUZvBJUBPJBnsIRUWgRWcsA5rpHMNwtkpUtptj"
            }
            $json | ConvertTo-Json -Depth 10 | Set-Content $path -Encoding UTF8
        } catch {
        }
    }
}

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Starting Claude CLI via AgentRouter ($Model)..." -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

claude.cmd @args
