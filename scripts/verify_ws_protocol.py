from pathlib import Path

path = Path("apps/brain/ws_protocol.py")
content = path.read_text(encoding="utf-8")

# Verify builder functions
builder_functions = [
    "def build_ghost_alert(source: str, severity: str, title: str, message: str) -> WSMessage:",
    "def build_ghost_health_check(status: dict) -> WSMessage:",
    "def build_ghost_webhook_received(source: str, severity: str) -> WSMessage:"
]

results = {
    func: func in content for func in builder_functions
}

# Print results
with open('C:/code/makima/_verify_ws_protocol.txt', 'w', encoding='utf-8') as out:
    for func, verified in results.items():
        status_str = 'OK' if verified else 'FAIL'
        out.write(f"{func}: {status_str}\n")
    out.write('Total lines: ' + str(len(content.split('\n'))) + '\n')
    out.write('Last 15 lines:\n')
    for i, line in enumerate(content.split('\n')[-15:], start=max(1, len(content.split('\n'))-14)):
        out.write(f'{i}: {line.rstrip()[:100]}\n')
print('Verification done')
