use serde_json::Value;
use std::ffi::OsString;
use std::io::Write;
use std::path::{Path, PathBuf};
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

fn select_bridge_path(resource_dir: &Path, development_bridge: PathBuf, is_dev: bool) -> PathBuf {
    // Development must read current sources, even when an older build copied resources.
    if is_dev && development_bridge.is_file() {
        return development_bridge;
    }
    let bundled_bridge = resource_dir.join("bridge.py");
    if bundled_bridge.is_file() {
        bundled_bridge
    } else {
        let fallback = resource_dir.join("_up_").join("_up_").join("bridge.py");
        if fallback.is_file() {
            fallback
        } else {
            development_bridge
        }
    }
}

#[tauri::command]
fn bridge(app: tauri::AppHandle, request: Value) -> Result<Value, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?;
    let development_bridge = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("bridge.py");
    let bridge_path = select_bridge_path(&resource_dir, development_bridge, tauri::is_dev());
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
    use super::{python_command, select_bridge_path};
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct BridgeFiles(PathBuf);

    impl BridgeFiles {
        fn new() -> Self {
            static NEXT_ID: AtomicUsize = AtomicUsize::new(0);
            let directory = std::env::temp_dir().join(format!(
                "stm-cmake-forge-bridge-{}-{}",
                std::process::id(),
                NEXT_ID.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&directory).unwrap();
            Self(directory)
        }

        fn write(&self, path: &str) -> PathBuf {
            let path = self.0.join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, "# bridge fixture").unwrap();
            path
        }
    }

    impl Drop for BridgeFiles {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn development_prefers_source_over_stale_bundled_resources() {
        let files = BridgeFiles::new();
        let source = files.write("source/bridge.py");
        let bundled = files.write("resources/bridge.py");
        files.write("resources/_up_/_up_/bridge.py");
        assert_eq!(
            select_bridge_path(bundled.parent().unwrap(), source.clone(), true),
            source
        );
        fs::remove_file(&source).unwrap();
        assert_eq!(
            select_bridge_path(bundled.parent().unwrap(), source, true),
            bundled
        );
    }

    #[test]
    fn packaged_app_prefers_its_resources_even_when_source_exists() {
        let files = BridgeFiles::new();
        let source = files.write("source/bridge.py");
        let bundled = files.write("resources/bridge.py");
        let legacy = files.write("resources/_up_/_up_/bridge.py");
        let resource_dir = bundled.parent().unwrap();
        assert_eq!(
            select_bridge_path(resource_dir, source.clone(), false),
            bundled
        );
        fs::remove_file(&bundled).unwrap();
        assert_eq!(select_bridge_path(resource_dir, source, false), legacy);
    }

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
