
## [2026-07-30] Deterministic Fast-Path Intent Pre-Routing in CommandRouter
- **Problem**: LLM classifier (_classify_intent) on fallback/fast models intermittently returned 'media' intent for system commands ('open chrome') and research queries ('do a research on apple company'), causing MediaAgent to hallucinate browser and research responses without proper OS/research tool execution.
- **Solution**: Added _check_deterministic_intent(< 1ms keyword/regex engine) in CommandRouter.route_command before LLM classification.
- **Result**: Exactly 100% deterministic routing for SYSTEM_CONTROL ('open chrome'), RESEARCH ('do a research'), MEDIA ('play song'), and BROWSER intents.
