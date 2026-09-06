use std::env;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use global_hotkey::hotkey::{Code, HotKey, Modifiers};
use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use serde_json::{json, Value};
use slint::{ComponentHandle, Model, ModelRc, VecModel, Weak};
use tokio::sync::mpsc::{self, UnboundedReceiver, UnboundedSender};
use tokio_tungstenite::{connect_async, tungstenite::Message as WsMsg};
use uuid::Uuid;

slint::include_modules!();

#[cfg(windows)]
mod windows_glass;

const DEFAULT_WS_URL: &str = "ws://127.0.0.1:8080/ws";
const DEFAULT_CHAT_URL: &str = "http://127.0.0.1:1420";

#[derive(Debug)]
enum Command {
    Text(String),
    VoiceStart,
    VoiceStop,
    Approve,
    Reject,
}

#[derive(Debug, Clone)]
enum UiEvent {
    Connection(bool),
    Status(String),
    PushMessage { is_user: bool, text: String, agent: String },
    Preview(String),
    Confirmation(String),
    Voice(bool),
    Thinking(bool),
    ActiveAgent(String),
    Attention(bool),
}

fn task_id(prefix: &str) -> String {
    format!("{}_{}", prefix, Uuid::new_v4().simple())
}

fn emit(ui: &Weak<MainWindow>, event: UiEvent) {
    let ui = ui.clone();
    let _ = slint::invoke_from_event_loop(move || {
        let Some(window) = ui.upgrade() else { return };
        match event {
            UiEvent::Connection(connected) => window.set_connected(connected),
            UiEvent::Status(status) => window.set_status(status.into()),
            UiEvent::PushMessage { is_user, text, agent } => {
                let model = window.get_messages();
                let vec_model = model
                    .as_any()
                    .downcast_ref::<VecModel<Message>>()
                    .expect("messages must be a VecModel<Message>");
                vec_model.push(Message {
                    is_user,
                    text: text.into(),
                    agent: agent.into(),
                });
                while vec_model.row_count() > 50 {
                    vec_model.remove(0);
                }
            }
            UiEvent::Preview(text) => window.set_preview(text.into()),
            UiEvent::Confirmation(text) => window.set_confirmation(text.into()),
            UiEvent::Voice(active) => window.set_voice_active(active),
            UiEvent::Thinking(thinking) => window.set_thinking(thinking),
            UiEvent::ActiveAgent(agent) => window.set_active_agent(agent.into()),
            UiEvent::Attention(expand) => {
                let _ = window.show();
                window.set_compact(!expand);
            }
        }
    });
}

fn handle_server_message(
    value: &Value,
    ui: &Weak<MainWindow>,
    pending_confirmation: &mut Option<(String, String)>,
) {
    let message_type = value
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let payload = value.get("payload").cloned().unwrap_or_default();
    match message_type {
        "ai_chunk" => {
            if let Some(text) = payload.get("text").and_then(Value::as_str) {
                let agent = payload
                    .get("agent")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                emit(ui, UiEvent::Thinking(false));
                emit(ui, UiEvent::ActiveAgent(String::new()));
                emit(ui, UiEvent::PushMessage {
                    is_user: false,
                    text: text.to_string(),
                    agent,
                });
                let preview = text.lines().last().unwrap_or(text).to_string();
                emit(ui, UiEvent::Preview(preview));
                emit(ui, UiEvent::Attention(false));
            }
        }
        "ai_error" | "voice_error" | "agent_error" => {
            let error = payload
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("Request failed");
            emit(ui, UiEvent::PushMessage {
                is_user: false,
                text: format!("Error: {}", error),
                agent: String::new(),
            });
            emit(ui, UiEvent::Status("Needs attention".into()));
            emit(ui, UiEvent::Thinking(false));
            emit(ui, UiEvent::ActiveAgent(String::new()));
        }
        "agent_started" | "agent_progress" | "thinking_status" => {
            let status = payload
                .get("status")
                .or_else(|| payload.get("message"))
                .and_then(Value::as_str)
                .unwrap_or("Makima is working...");
            let agent = payload
                .get("agent")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            emit(ui, UiEvent::Status(status.to_string()));
            emit(ui, UiEvent::Thinking(true));
            if !agent.is_empty() {
                emit(ui, UiEvent::ActiveAgent(agent));
            }
        }
        "action_confirm_request" => {
            let description = payload
                .get("description")
                .or_else(|| payload.get("action"))
                .and_then(Value::as_str)
                .unwrap_or("Makima needs approval for an action");
            let tid = value
                .get("task_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let action = payload
                .get("action")
                .and_then(Value::as_str)
                .unwrap_or("destructive")
                .to_string();
            *pending_confirmation = Some((tid, action));
            emit(ui, UiEvent::Confirmation(description.to_string()));
            emit(ui, UiEvent::Attention(true));
        }
        "voice_session_state" | "voice_tts_started" => {
            emit(ui, UiEvent::Voice(true));
            if let Some(state) = payload.get("state").and_then(Value::as_str) {
                emit(ui, UiEvent::Status(format!("Voice: {}", state)));
            }
        }
        "voice_tts_stopped" => {
            emit(ui, UiEvent::Voice(false));
        }
        "voice_transcript_final" => {
            if let Some(text) = payload.get("transcript").and_then(Value::as_str) {
                emit(ui, UiEvent::PushMessage {
                    is_user: true,
                    text: text.to_string(),
                    agent: String::new(),
                });
                emit(ui, UiEvent::Preview(text.to_string()));
            }
        }
        _ => {}
    }
}

async fn websocket_worker(
    ws_url: String,
    mut commands: UnboundedReceiver<Command>,
    ui: Weak<MainWindow>,
) {
    let conversation_id = format!("native-overlay-{}", Uuid::new_v4().simple());
    let mut voice_session_id: Option<String> = None;
    let mut pending_confirmation: Option<(String, String)> = None;

    loop {
        emit(&ui, UiEvent::Status("Connecting to Makima...".into()));
        match connect_async(&ws_url).await {
            Ok((mut socket, _)) => {
                emit(&ui, UiEvent::Connection(true));
                emit(&ui, UiEvent::Status("Ready".into()));
                loop {
                    tokio::select! {
                        command = commands.recv() => {
                            let Some(command) = command else { return };
                            let message = match command {
                                Command::Text(text) => {
                                    let id = task_id("task");
                                    emit(&ui, UiEvent::PushMessage {
                                        is_user: true,
                                        text: text.clone(),
                                        agent: String::new(),
                                    });
                                    emit(&ui, UiEvent::Preview(text.clone()));
                                    emit(&ui, UiEvent::Thinking(true));
                                    json!({"v": 1, "type": "user_message", "task_id": id,
                                        "payload": {"text": text, "conversation_id": conversation_id, "source": "native_overlay"}})
                                }
                                Command::VoiceStart => {
                                    let id = task_id("voice");
                                    voice_session_id = Some(id.clone());
                                    emit(&ui, UiEvent::Voice(true));
                                    json!({"v": 1, "type": "voice_session_start", "task_id": id,
                                        "payload": {"voice_session_id": id, "conversation_id": conversation_id,
                                            "settings": {"auto_read_aloud": true, "language": "auto"}, "source": "native_overlay"}})
                                }
                                Command::VoiceStop => {
                                    let id = voice_session_id.take().unwrap_or_else(|| task_id("voice"));
                                    emit(&ui, UiEvent::Voice(false));
                                    json!({"v": 1, "type": "voice_session_stop", "task_id": id,
                                        "payload": {"voice_session_id": id}})
                                }
                                Command::Approve | Command::Reject => {
                                    let Some((approval_task_id, action)) = pending_confirmation.take() else {
                                        continue;
                                    };
                                    let message_type = if matches!(command, Command::Approve) {
                                        "approve_action"
                                    } else {
                                        "reject_action"
                                    };
                                    emit(&ui, UiEvent::Confirmation(String::new()));
                                    json!({"v": 1, "type": message_type, "task_id": approval_task_id,
                                        "payload": {"action": action}})
                                }
                            };
                            if socket.send(WsMsg::Text(message.to_string().into())).await.is_err() { break; }
                        }
                        incoming = socket.next() => {
                            match incoming {
                                Some(Ok(WsMsg::Text(text))) => {
                                    if let Ok(value) = serde_json::from_str::<Value>(&text) {
                                        handle_server_message(&value, &ui, &mut pending_confirmation);
                                    }
                                }
                                Some(Ok(WsMsg::Ping(payload))) => {
                                    let _ = socket.send(WsMsg::Pong(payload)).await;
                                }
                                Some(Ok(WsMsg::Close(_))) | None | Some(Err(_)) => break,
                                _ => {}
                            }
                        }
                    }
                }
                emit(&ui, UiEvent::Connection(false));
                emit(&ui, UiEvent::Status("Makima disconnected; retrying...".into()));
            }
            Err(_) => {
                emit(&ui, UiEvent::Connection(false));
                emit(&ui, UiEvent::Status("Makima is offline; retrying...".into()));
            }
        }
        tokio::time::sleep(Duration::from_secs(3)).await;
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let ws_url = env::var("MAKIMA_WS_URL").unwrap_or_else(|_| DEFAULT_WS_URL.to_string());
    let chat_url = env::var("MAKIMA_CHAT_URL").unwrap_or_else(|_| DEFAULT_CHAT_URL.to_string());
    let window = MainWindow::new()?;
    let tray = TrayIcon::new()?;
    let (command_tx, command_rx) = mpsc::unbounded_channel();

    // Initialise messages as an empty VecModel — must be set before UI renders
    let messages_model: ModelRc<Message> = ModelRc::new(VecModel::default());
    window.set_messages(messages_model);

    let window_weak = window.as_weak();
    std::thread::Builder::new()
        .name("makima-ws".into())
        .spawn(move || {
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build();
            match runtime {
                Ok(runtime) => runtime.block_on(websocket_worker(ws_url, command_rx, window_weak)),
                Err(error) => eprintln!("Makima WS runtime failed: {error}"),
            }
        })?;

    wire_window_callbacks(&window, &tray, command_tx.clone(), chat_url);
    window.show()?;
    tray.show()?;

    #[cfg(windows)]
    {
        let glass_window = window.as_weak();
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(window) = glass_window.upgrade() {
                windows_glass::apply(&window.window());
            }
        });
    }

    let hotkey_manager = GlobalHotKeyManager::new()?;
    let primary = HotKey::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyO);
    let fallback = HotKey::new(Some(Modifiers::ALT), Code::Space);
    hotkey_manager.register(primary)?;
    hotkey_manager.register(fallback)?;
    let window_for_hotkey = window.as_weak();
    std::thread::spawn(move || loop {
        if let Ok(event) = GlobalHotKeyEvent::receiver().try_recv() {
            if event.state() == HotKeyState::Pressed {
                let ui = window_for_hotkey.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(window) = ui.upgrade() {
                        if !window.window().is_visible() {
                            window.show().ok();
                            window.set_compact(true);
                        } else if window.get_compact() {
                            window.set_compact(false);
                        } else {
                            window.hide().ok();
                        }
                    }
                });
            }
        }
        std::thread::sleep(Duration::from_millis(25));
    });

    slint::run_event_loop()?;
    Ok(())
}

fn wire_window_callbacks(
    window: &MainWindow,
    tray: &TrayIcon,
    commands: UnboundedSender<Command>,
    chat_url: String,
) {
    let send = commands.clone();
    window.on_send_message(move |text| {
        if !text.trim().is_empty() {
            let _ = send.send(Command::Text(text.to_string()));
        }
    });

    let ui = window.as_weak();
    window.on_toggle_compact(move || {
        if let Some(window) = ui.upgrade() {
            window.set_compact(!window.get_compact());
        }
    });

    let ui = window.as_weak();
    window.on_toggle_pin(move || {
        if let Some(window) = ui.upgrade() {
            window.set_pinned(!window.get_pinned());
        }
    });

    let chat = chat_url.clone();
    window.on_open_chat(move || { let _ = open::that(&chat); });
    let chat = chat_url.clone();
    tray.on_open_chat(move || { let _ = open::that(&chat); });

    let start = commands.clone();
    window.on_start_voice(move || { let _ = start.send(Command::VoiceStart); });
    let start = commands.clone();
    tray.on_start_voice(move || { let _ = start.send(Command::VoiceStart); });
    let stop = commands.clone();
    window.on_stop_voice(move || { let _ = stop.send(Command::VoiceStop); });
    let stop = commands.clone();
    tray.on_stop_voice(move || { let _ = stop.send(Command::VoiceStop); });

    let approve = commands.clone();
    window.on_approve_action(move || { let _ = approve.send(Command::Approve); });
    let reject = commands;
    window.on_reject_action(move || { let _ = reject.send(Command::Reject); });

    let ui = window.as_weak();
    window.on_hide_overlay(move || {
        if let Some(window) = ui.upgrade() { window.hide().ok(); }
    });
    let ui = window.as_weak();
    tray.on_show_overlay(move || {
        if let Some(window) = ui.upgrade() {
            window.show().ok();
            window.set_compact(false);
        }
    });
    tray.on_quit_overlay(move || { let _ = slint::quit_event_loop(); });

    // Drag-to-move: record grab origin on drag_start, apply delta on drag_move
    let drag_origin: std::sync::Arc<std::sync::Mutex<Option<(f32, f32)>>> =
        std::sync::Arc::new(std::sync::Mutex::new(None));

    let origin_start = drag_origin.clone();
    let ui_drag = window.as_weak();
    window.on_drag_start(move |mx, my| {
        let mut origin = origin_start.lock().unwrap();
        if let Some(w) = ui_drag.upgrade() {
            let pos = w.window().position();
            *origin = Some((pos.x as f32 - mx, pos.y as f32 - my));
        }
    });

    let origin_move = drag_origin.clone();
    let ui_drag2 = window.as_weak();
    window.on_drag_move(move |mx, my| {
        let origin = origin_move.lock().unwrap();
        if let (Some((ox, oy)), Some(w)) = (*origin, ui_drag2.upgrade()) {
            w.window().set_position(slint::PhysicalPosition::new(
                (ox + mx) as i32,
                (oy + my) as i32,
            ));
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn task_ids_include_the_requested_prefix() {
        let id = task_id("overlay");
        assert!(id.starts_with("overlay_"));
    }
}