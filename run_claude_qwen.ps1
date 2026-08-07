# Run Claude CLI routed to Alibaba Cloud DashScope with Multi-Model Fallback
# Usage: .\run_claude_qwen.ps1                               # auto-select fastest
#        .\run_claude_qwen.ps1 -Model qwen3.7-flash-2026-07-15
#        .\run_claude_qwen.ps1 -Model qwen3.6-plus-2026-04-02
param(
    [switch]$Logout,
    [string]$Model = ""
)

$env:ANTHROPIC_BASE_URL = "https://dashscope-intl.aliyuncs.com/apps/anthropic"
$env:ANTHROPIC_AUTH_TOKEN = "sk-ws-H.XEXYYX.2ebM.MEUCIHvVzZqu27dFAuj2YzCAulR0Vckqmt9RYgi4iE5w4AiVAiEAsAqKZT6q5pXNGylMZ8Rtgg6CQ_KfxtvI5rf22zC9ZyQ"

if ($Logout) {
    Write-Host "Logging out of any existing Anthropic session..." -ForegroundColor Yellow
    claude.cmd /logout
    return
}

$models = @("qwen3.7-flash", "qwen3.7-flash-2026-07-15", "qwen3.6-plus-2026-04-02", "kimi-k2.7-code", "qwen-coder-plus", "qwen-plus", "qwen-max")
$selectedModel = "qwen3.7-flash"
$env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "4096"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Checking DashScope Multi-Model Fallback Pool..." -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

foreach ($m in $models) {
    try {
        $body = @{ model = $m; messages = @(@{ role = "user"; content = "hi" }); max_tokens = 5 } | ConvertTo-Json -Compress
        $res = Invoke-WebRequest -Uri "https://dashscope-intl.aliyuncs.com/apps/anthropic/v1/messages" -Method Post -Headers @{ "x-api-key" = $env:ANTHROPIC_AUTH_TOKEN; "anthropic-version" = "2023-06-01" } -Body $body -ContentType "application/json" -UseBasicParsing
        if ($res.StatusCode -eq 200) {
            $selectedModel = $m
            break
        }
    } catch {
        Write-Host " [FALLBACK] Model $m unavailable, trying next..." -ForegroundColor DarkGray
    }
}

$env:ANTHROPIC_MODEL = $selectedModel
$env:ANTHROPIC_SMALL_FAST_MODEL = $selectedModel

# Automatically sync working model into both workspace and global settings.json
foreach ($path in @("c:\code\makima\.claude\settings.json", "$env:USERPROFILE\.claude\settings.json")) {
    if (Test-Path $path) {
        try {
            $json = Get-Content $path -Raw | ConvertFrom-Json
            $json.model = $selectedModel
            if ($json.env) {
                $json.env.ANTHROPIC_MODEL = $selectedModel
                $json.env.ANTHROPIC_SMALL_FAST_MODEL = $selectedModel
            }
            $json | ConvertTo-Json -Depth 10 | Set-Content $path -Encoding UTF8
        } catch {
            # Ignore sync error if file is locked
        }
    }
}

Write-Host "Starting Claude CLI with Auto-Selected Working Model: $selectedModel..." -ForegroundColor Green
claude.cmd @args
