@echo off
set "ANTHROPIC_BASE_URL=https://agentrouter.org"
set "ANTHROPIC_API_KEY=sk-t2oWvWH3pYxUZvBJUBPJBnsIRUWgRWcsA5rpHMNwtkpUtptj"
set "ANTHROPIC_AUTH_TOKEN=sk-t2oWvWH3pYxUZvBJUBPJBnsIRUWgRWcsA5rpHMNwtkpUtptj"
set "ANTHROPIC_MODEL=claude-opus-4-8"
set "ANTHROPIC_SMALL_FAST_MODEL=claude-opus-4-8"
set "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"

claude.cmd %*
