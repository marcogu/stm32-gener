use serde_json::Value;
use std::ffi::OsString;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use tauri::Manager;

struct PythonCandidate {
    program: OsString,
    args: Vec<OsString>,
}

fn python_candidates() -> Vec<PythonCandidate> {
    let mut candidates = Vec::new();
    let mut add = |program: OsString, args: &[&str]| {
        candidates.push(PythonCandidate {
            program,
            args: args.iter().map(OsString::from).collect(),
        })
    };

    if let Some(program) =
        std::env::var_os("STM_CMAKE_FORGE_PYTHON").filter(|value| !value.is_empty())
    {
        add(program, &[]);
    }
    if cfg!(target_os = "windows") {
        add(OsString::from("python"), &[]);
        add(OsString::from("py"), &["-3"]);
        add(OsString::from("python3"), &[]);
    } else {
        add(OsString::from("python3"), &[]);
        if cfg!(target_os = "macos") {
            for path in [
                "/opt/homebrew/bin/python3",
                "/usr/local/bin/python3",
                "/opt/local/bin/python3",
            ] {
                add(OsString::from(path), &[]);
            }
            if let Some(home) = std::env::var_os("HOME") {
                for suffix in [".pyenv/shims/python3", ".local/bin/python3"] {
                    add(PathBuf::from(&home).join(suffix).into_os_string(), &[]);
                }
            }
        }
        add(OsString::from("python"), &[]);
    }
    candidates
}

fn python_command() -> Result<Command, String> {
    let mut detected = Vec::new();
    for candidate in python_candidates() {
        let output = Command::new(&candidate.program)
            .args(&candidate.args)
            .args([
                "-X",
                "utf8",
                "-c",
                "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)",
            ])
            .output();
        let Ok(output) = output else { continue };
        let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if output.status.success() {
            let mut command = Command::new(candidate.program);
            command.args(candidate.args).args(["-X", "utf8"]);
            return Ok(command);
        }
        if !version.is_empty() {
            detected.push(version);
        }
    }
    let detail = if detected.is_empty() {
        String::new()
    } else {
        format!("; detected unsupported version(s): {}", detected.join(", "))
    };
    Err(format!("Python 3.10 or newer was not found{detail}"))
}

#[tauri::command]
fn bridge(app: tauri::AppHandle, request: Value) -> Result<Value, String> {
    let bundled_bridge = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?
        .join("bridge.py");
    let development_bridge = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("bridge.py");
    let bridge_path = if bundled_bridge.is_file() {
        bundled_bridge
    } else {
        let fallback = app
            .path()
            .resource_dir()
            .map_err(|error| error.to_string())?
            .join("_up_")
            .join("_up_")
            .join("bridge.py");
        if fallback.is_file() {
            fallback
        } else {
            development_bridge
        }
    };
    let mut command = python_command()?;
    let mut child = command
        .arg(bridge_path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("Could not start Python bridge: {error}"))?;
    child
        .stdin
        .as_mut()
        .ok_or_else(|| "Python bridge stdin is unavailable".to_string())?
        .write_all(
            serde_json::to_string(&request)
                .map_err(|error| error.to_string())?
                .as_bytes(),
        )
        .map_err(|error| error.to_string())?;
    let output = child
        .wait_with_output()
        .map_err(|error| error.to_string())?;
    let value: Value = serde_json::from_slice(&output.stdout).map_err(|error| {
        format!(
            "Python bridge returned invalid JSON: {error}; {}",
            String::from_utf8_lossy(&output.stderr)
        )
    })?;
    if !output.status.success() || value.get("ok") != Some(&Value::Bool(true)) {
        return Err(value
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("Bridge request failed")
            .to_string());
    }
    value
        .get("data")
        .cloned()
        .ok_or_else(|| "Bridge response has no data".to_string())
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

#[cfg(test)]
mod tests {
    use super::python_command;

    #[test]
    fn selects_python_310_or_newer() {
        let output = python_command()
            .expect("a supported Python interpreter should be available")
            .args([
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)",
            ])
            .output()
            .expect("the selected Python interpreter should start");
        assert!(output.status.success());
    }
}
