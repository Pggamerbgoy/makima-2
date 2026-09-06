"""Comprehensive verification of GhostWatchdog wiring."""
import os
os.chdir("C:/code/makima")

results = []

# 1. Module imports
try:
    from apps.brain.ghost_watchdog import GhostWatchdog
    results.append("ghost_watchdog_import=OK")
except Exception as e:
    results.append(f"ghost_watchdog_import=FAIL: {e}")

try:
    from apps.brain.ws_protocol import ServerMessageType, build_ghost_alert, build_ghost_health_check
    results.append(f"ghost_alert_type={ServerMessageType.GHOST_ALERT.value}")
    results.append("ghost_builders=OK")
except Exception as e:
    results.append(f"ghost_protocol=FAIL: {e}")

# 2. Config parsing
try:
    import yaml
    cfg = yaml.safe_load(open("configs/default.yaml", encoding="utf-8"))
    gw = cfg.get("ghost_watchdog", {})
    results.append(f"config_enabled={gw.get('enabled')}")
    results.append(f"config_interval={gw.get('cron_interval_s')}s")
    results.append(f"config_endpoints={len(gw.get('cron_endpoints', []))}")
    results.append(f"config_webhook={gw.get('webhook', {}).get('enabled')}")
except Exception as e:
    results.append(f"config=FAIL: {e}")

# 3. Instantiation (no WS broadcast)
try:
    gw_inst = GhostWatchdog({"ghost_watchdog": {"enabled": True, "cron_interval_s": 60, "cron_endpoints": [], "webhook": {"enabled": True}}})
    results.append(f"instantiation=OK endpoints={len(gw_inst.endpoints)} webhook={gw_inst.webhook_enabled}")
except Exception as e:
    results.append(f"instantiation=FAIL: {e}")

# 4. Main.py wiring
try:
    content = open("apps/brain/main.py", encoding="utf-8").read()
    checks = {
        "ghost_import": "from .ghost_watchdog import GhostWatchdog" in content,
        "ghost_init": "ghost_watchdog = GhostWatchdog(CONFIG" in content,
        "ghost_notif": "ghost_watchdog.notification_hub = notifications" in content,
        "ghost_modules": '"ghost_watchdog": ghost_watchdog' in content,
        "ghost_start": "await ghost_watchdog.start()" in content,
        "ghost_stop": "await ghost_watchdog.stop()" in content,
        "ghost_status": "/ghost/status" in content,
        "ghost_webhook": "/ghost/webhook" in content,
        "ghost_check": "/ghost/check" in content,
    }
    for k, v in checks.items():
        results.append(f"main_{k}={'OK' if v else 'FAIL'}")
except Exception as e:
    results.append(f"main_check=FAIL: {e}")

# 5. Security: HMAC verification
try:
    gw_inst = GhostWatchdog({
        "ghost_watchdog": {
            "enabled": True,
            "cron_endpoints": [],
            "webhook": {"enabled": True, "secret": "test_secret"}
        }
    })
    results.append(f"hmac_secret_configured={bool(gw_inst.webhook_secret)}")
    results.append(f"hmac_secret_matches={gw_inst.webhook_secret == 'test_secret'}")
except Exception as e:
    results.append(f"hmac_check=FAIL: {e}")

output = "\n".join(results)
print(output)
