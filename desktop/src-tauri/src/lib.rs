use serde_json::Value;
use std::io::Write;
use std::process::{Command, Stdio};
use tauri::Manager;

fn python_command() -> Command {
    if cfg!(target_os = "windows") { Command::new("python") } else { Command::new("python3") }
}

#[tauri::command]
fn bridge(app: tauri::AppHandle, request: Value) -> Result<Value, String> {
    let bundled_bridge = app.path().resource_dir().map_err(|error| error.to_string())?.join("bridge.py");
    let development_bridge = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("bridge.py");
    let bridge_path = if bundled_bridge.is_file() { bundled_bridge } else {
        let fallback = app.path().resource_dir().map_err(|error| error.to_string())?.join("_up_").join("_up_").join("bridge.py");
        if fallback.is_file() { fallback } else { development_bridge }
    };
    let mut child = python_command().arg(bridge_path).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(|error| format!("Could not start Python bridge: {error}"))?;
    child.stdin.as_mut().ok_or_else(|| "Python bridge stdin is unavailable".to_string())?.write_all(serde_json::to_string(&request).map_err(|error| error.to_string())?.as_bytes()).map_err(|error| error.to_string())?;
    let output = child.wait_with_output().map_err(|error| error.to_string())?;
    let value: Value = serde_json::from_slice(&output.stdout).map_err(|error| format!("Python bridge returned invalid JSON: {error}; {}", String::from_utf8_lossy(&output.stderr)))?;
    if !output.status.success() || value.get("ok") != Some(&Value::Bool(true)) {
        return Err(value.get("error").and_then(Value::as_str).unwrap_or("Bridge request failed").to_string());
    }
    value.get("data").cloned().ok_or_else(|| "Bridge response has no data".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![bridge])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
