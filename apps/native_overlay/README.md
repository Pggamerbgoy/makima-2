# Makima Native Overlay

This is the native Rust + Slint quick-command overlay. The full Makima chat
continues to run in Chrome; this process has no WebView or browser dependency.

## Run

```powershell
cargo run --manifest-path apps/native_overlay/Cargo.toml
```

The overlay connects to `ws://127.0.0.1:8080/ws` by default. Override it with
`MAKIMA_WS_URL` and the Chrome handoff URL with `MAKIMA_CHAT_URL`.
