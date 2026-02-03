use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, PhysicalPosition};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

mod selection;

const HYW_SERVER_URL: &str = "http://127.0.0.1:5140/hyw";

// Track if a query is in progress
static QUERY_IN_PROGRESS: AtomicBool = AtomicBool::new(false);

// Pending query from shortcut (frontend will fetch this)
static PENDING_QUERY: Mutex<Option<String>> = Mutex::new(None);

#[tauri::command]
async fn run_query(query: String, app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    // Prevent duplicate queries
    if QUERY_IN_PROGRESS.swap(true, Ordering::SeqCst) {
        return Err("Query already in progress".into());
    }

    let client = reqwest::Client::new();
    let result = async {
        let response = client
            .post(format!("{}/query", HYW_SERVER_URL))
            .json(&serde_json::json!({ "query": query }))
            .send()
            .await
            .map_err(|e| format!("Failed to connect to HYW server: {}", e))?;

        let result: serde_json::Value = response
            .json()
            .await
            .map_err(|e| format!("Failed to parse response: {}", e))?;

        Ok(result)
    }
    .await;

    // Reset query state
    QUERY_IN_PROGRESS.store(false, Ordering::SeqCst);

    // Emit completion event for notification
    if result.is_ok() {
        let _ = app.emit("query-complete", ());
    }

    result
}

#[tauri::command]
fn get_pending_query() -> Option<String> {
    PENDING_QUERY.lock().unwrap().take()
}

#[tauri::command]
async fn check_server_status() -> Result<bool, String> {
    let client = reqwest::Client::new();
    match client
        .get(format!("{}/health", HYW_SERVER_URL))
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .await
    {
        Ok(resp) => Ok(resp.status().is_success()),
        Err(_) => Ok(false),
    }
}

#[tauri::command]
async fn get_server_config() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/config", HYW_SERVER_URL))
        .send()
        .await
        .map_err(|e| format!("Failed to get config: {}", e))?;

    let result: serde_json::Value = response
        .json()
        .await
        .map_err(|e| format!("Failed to parse config: {}", e))?;

    Ok(result)
}

#[tauri::command]
fn is_query_in_progress() -> bool {
    QUERY_IN_PROGRESS.load(Ordering::SeqCst)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--hidden"]),
        ))
        .setup(move |app| {
            // Setup Logging
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Pre-position window to top-right before first show
            if let Some(window) = app.get_webview_window("main") {
                if let Some(monitor) = window.primary_monitor().ok().flatten() {
                    let monitor_pos = monitor.position();
                    let monitor_size = monitor.size();
                    let window_size = window.outer_size().unwrap_or_default();
                    let x = monitor_pos.x + monitor_size.width as i32 - window_size.width as i32 - 20;
                    let y = monitor_pos.y + 40;
                    let _ = window.set_position(PhysicalPosition::new(x, y));
                }
            }

            // Setup System Tray
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let show = MenuItem::with_id(app, "show", "Show Window", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &settings, &quit])?;

            let _tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .menu_on_left_click(false)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "quit" => {
                        app.exit(0);
                    }
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            // Position to top-right BEFORE showing
                            if let Some(monitor) = window.current_monitor().ok().flatten() {
                                let monitor_pos = monitor.position();
                                let monitor_size = monitor.size();
                                let window_size = window.outer_size().unwrap_or_default();
                                let x = monitor_pos.x + monitor_size.width as i32 - window_size.width as i32 - 20;
                                let y = monitor_pos.y + 40;
                                let _ = window.set_position(PhysicalPosition::new(x, y));
                            }
                            let _ = window.set_always_on_top(true);
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "settings" => {
                        // Show window first, then open settings
                        if let Some(window) = app.get_webview_window("main") {
                            if let Some(monitor) = window.current_monitor().ok().flatten() {
                                let monitor_pos = monitor.position();
                                let monitor_size = monitor.size();
                                let window_size = window.outer_size().unwrap_or_default();
                                let x = monitor_pos.x + monitor_size.width as i32 - window_size.width as i32 - 20;
                                let y = monitor_pos.y + 40;
                                let _ = window.set_position(PhysicalPosition::new(x, y));
                            }
                            let _ = window.set_always_on_top(true);
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                        let _ = app.emit("open-settings", ());
                    }
                    _ => {}
                })
                .on_tray_icon_event(move |tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            if window.is_visible().unwrap_or(false) {
                                let _ = window.hide();
                            } else {
                                // Position to top-right of current monitor
                                if let Some(monitor) = window.current_monitor().ok().flatten() {
                                    let monitor_pos = monitor.position();
                                    let monitor_size = monitor.size();
                                    let window_size = window.outer_size().unwrap_or_default();
                                    let x = monitor_pos.x + monitor_size.width as i32 - window_size.width as i32 - 20;
                                    let y = monitor_pos.y + 40;
                                    let _ = window.set_position(PhysicalPosition::new(x, y));
                                }
                                let _ = window.set_always_on_top(true);
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                    }
                })
                .build(app)?;

            // Register Hotkey: Cmd+G
            // - If window visible: hide it
            // - If window hidden: try to get selected text, show window, query if has selection or focus input
            let shortcut = Shortcut::new(Some(Modifiers::META), Code::KeyG);
            let app_handle_hotkey = app.handle().clone();

            app.handle().plugin(
                tauri_plugin_global_shortcut::Builder::new()
                    .with_handler(move |_app, _shortcut, event| {
                        if event.state != ShortcutState::Pressed {
                            return;
                        }

                        log::info!("[Shortcut] Cmd+G pressed");

                        if let Some(window) = app_handle_hotkey.get_webview_window("main") {
                            // Toggle: hide if visible
                            if window.is_visible().unwrap_or(false) {
                                log::info!("[Shortcut] Window visible, hiding");
                                let _ = window.hide();
                                return;
                            }

                            // Try to get selected text BEFORE showing window
                            log::info!("[Shortcut] Getting selected text...");
                            let selected_text = selection::get_selected_text();
                            log::info!("[Shortcut] Result: {:?}", selected_text);

                            // If we have valid selection, clear UI state BEFORE showing window to avoid flash
                            if let Some(ref text) = selected_text {
                                if !text.trim().is_empty() {
                                    // Pass text to frontend to check if it matches current result
                                    // Serialize string to safe JS literal
                                    if let Ok(json_text) = serde_json::to_string(text) {
                                        let js = format!("window.__prepareForNewQuery && window.__prepareForNewQuery({})", json_text);
                                        let _ = window.eval(&js);
                                    } else {
                                        // Fallback if serialization fails
                                        let _ = window.eval("window.__prepareForNewQuery && window.__prepareForNewQuery()");
                                    }
                                }
                            }

                            // Position and show window
                            if let Some(monitor) = window.current_monitor().ok().flatten() {
                                let monitor_pos = monitor.position();
                                let monitor_size = monitor.size();
                                let window_size = window.outer_size().unwrap_or_default();
                                let x = monitor_pos.x + monitor_size.width as i32 - window_size.width as i32 - 20;
                                let y = monitor_pos.y + 40;
                                let _ = window.set_position(PhysicalPosition::new(x, y));
                            }
                            let _ = window.set_always_on_top(true);
                            let _ = window.show();
                            let _ = window.set_focus();

                            // Handle selection
                            match selected_text {
                                Some(text) if !text.trim().is_empty() => {
                                    log::info!("[Shortcut] Has selection, setting pending query");
                                    let query_text = text.trim().to_string();

                                    // Store pending query for frontend to fetch
                                    *PENDING_QUERY.lock().unwrap() = Some(query_text);

                                    // Force frontend to check pending query immediately via JS eval
                                    // This is more reliable than events for window focus timing
                                    let _ = window.eval("window.__checkPendingQuery && window.__checkPendingQuery()");

                                    log::info!("[Shortcut] Pending query set, force checked frontend");
                                }
                                _ => {
                                    log::info!("[Shortcut] No selection, focusing input");
                                    let _ = app_handle_hotkey.emit("focus-input", ());
                                }
                            }
                        }
                    })
                    .build(),
            )?;

            app.global_shortcut().register(shortcut)?;

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            run_query,
            get_pending_query,
            check_server_status,
            get_server_config,
            is_query_in_progress
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Focused(focused) = event {
                // Hide the main window when it loses focus
                if !focused && window.label() == "main" {
                    if !is_query_in_progress() {
                        let _ = window.hide();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
