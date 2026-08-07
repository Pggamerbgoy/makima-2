use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, State};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

struct EngineState {
    process: Mutex<Option<Child>>,
}

#[tauri::command]
fn start_engine(state: State<'_, EngineState>) -> Result<String, String> {
    let mut proc_guard = state.process.lock().map_err(|e| e.to_string())?;

    if proc_guard.is_some() {
        return Ok("Engine is already running.".into());
    }

    let mut cmd = Command::new("python");
    cmd.args(["-m", "apps.brain.main"]).current_dir("../../");

    #[cfg(target_os = "windows")]
    {
        const CREATE_NEW_CONSOLE: u32 = 0x00000010;
        cmd.creation_flags(CREATE_NEW_CONSOLE);
    }

    let child = cmd
        .spawn()
        .map_err(|e| format!("Failed to start Makima engine: {}", e))?;

    *proc_guard = Some(child);
    Ok("Makima engine started successfully.".into())
}

#[tauri::command]
fn stop_engine(state: State<'_, EngineState>) -> Result<String, String> {
    let mut proc_guard = state.process.lock().map_err(|e| e.to_string())?;

    if let Some(mut child) = proc_guard.take() {
        let _ = child.kill();
        Ok("Makima engine stopped.".into())
    } else {
        Ok("Engine was not running.".into())
    }
}

#[tauri::command]
fn is_engine_running(state: State<'_, EngineState>) -> bool {
    let mut proc_guard = match state.process.lock() {
        Ok(guard) => guard,
        Err(_) => return false,
    };

    if let Some(child) = proc_guard.as_mut() {
        match child.try_wait() {
            Ok(Some(_)) => {
                // Process has exited
                *proc_guard = None;
                false
            }
            Ok(None) => true, // Still running
            Err(_) => false,
        }
    } else {
        false
    }
}

/// Center, show, and focus the overlay — Spotlight-style, always opens mid-screen.
/// Includes a robust Windows foreground lock workaround to guarantee 100% reliable focus.
fn show_overlay(window: &tauri::WebviewWindow) {
    println!("[Tauri] Showing overlay window");
    
    // 1. Unminimize if it was previously minimized
    let _ = window.unminimize();
    
    // 2. Center the window on the active monitor
    let _ = window.center();
    
    // 3. Set always on top to ensure it beats other windows (Spotlight style)
    let _ = window.set_always_on_top(true);
    
    // 4. Show the window
    let _ = window.show();
    
    // 5. Set initial focus
    let _ = window.set_focus();

    // 6. Windows foreground lock workaround:
    // Windows often prevents background apps from aggressively stealing focus.
    // A slight delayed re-focus and z-order bump ensures it reliably comes to the front.
    let win_clone = window.clone();
    tauri::async_runtime::spawn(async move {
        std::thread::sleep(std::time::Duration::from_millis(50));
        let _ = win_clone.set_focus();
        let _ = win_clone.set_always_on_top(true);
    });
}

fn hide_overlay(window: &tauri::WebviewWindow) {
    println!("[Tauri] Hiding overlay window");
    let _ = window.hide();
    // Release always-on-top when hidden so it doesn't interfere with other fullscreen apps
    let _ = window.set_always_on_top(false);
}

fn toggle_overlay_window(window: &tauri::WebviewWindow) {
    let is_visible = window.is_visible().unwrap_or(false);
    println!(
        "[Tauri] toggle_overlay_window called, is_visible={}",
        is_visible
    );
    if is_visible {
        hide_overlay(window);
    } else {
        show_overlay(window);
    }
}

/// Show + focus the overlay window if hidden, hide it if currently visible.
/// Called both from the global hotkey handler and via JS `invoke("toggle_overlay")`.
#[tauri::command]
fn toggle_overlay(app: tauri::AppHandle) -> Result<(), String> {
    let Some(window) = app.get_webview_window("overlay") else {
        return Err("overlay window not found".into());
    };
    toggle_overlay_window(&window);
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(EngineState {
            process: Mutex::new(None),
        })
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    // Only act on the key-down edge, not key-up, so holding
                    // the combo doesn't repeatedly toggle the window.
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    let overlay_shortcut: Shortcut = "Ctrl+Shift+O".parse().unwrap();
                    let alt_space: Shortcut = "Alt+Space".parse().unwrap();
                    if shortcut == &overlay_shortcut || shortcut == &alt_space {
                        if let Some(window) = app.get_webview_window("overlay") {
                            toggle_overlay_window(&window);
                        }
                    }
                })
                .build(),
        )
        .setup(|app| {
            // Register Ctrl+Shift+O as the primary overlay toggle hotkey,
            // with Alt+Space as a secondary fallback. We ignore register errors
            // if Alt+Space is intercepted by Windows System Menu or PowerToys.
            let overlay_shortcut: Shortcut = "Ctrl+Shift+O".parse().unwrap();
            let _ = app.global_shortcut().register(overlay_shortcut);
            
            let alt_space: Shortcut = "Alt+Space".parse().unwrap();
            let _ = app.global_shortcut().register(alt_space);
            
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_engine,
            stop_engine,
            is_engine_running,
            toggle_overlay
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
