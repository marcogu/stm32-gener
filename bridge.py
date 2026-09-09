"""JSON stdin/stdout adapter for the desktop shell; no shell execution."""
import json
import sys
from pathlib import Path

from config_model import ConfigError, normalize_config
from generator import check_destinations, preview, render, write_files
from scanner import scan_environment, scan_project, scan_workspace


def handle(request):
    action = request["action"]
    if action == "scan":
        scan = {"environment": scan_environment, "project": scan_project, "workspace": scan_workspace}[request["kind"]]
        return scan(Path(request["path"]))
    output = Path(request["output"]).expanduser().resolve()
    base = Path(request["configDir"]).expanduser().resolve()
    config = normalize_config(request["config"], base, output)
    files = render(config, base, output)
    check_destinations(files, output, config["project"]["createDirs"])
    if action == "generate":
        write_files(files, output, policy=config["generation"]["conflictPolicy"], directories=config["project"]["createDirs"])
    elif action != "preview":
        raise ConfigError("unknown action")
    return {"files": {str(p): text for p, text in files.items()}, "diff": preview(files, output), "output": str(output)}


if __name__ == "__main__":
    try:
        if sys.version_info < (3, 10):
            raise ValueError("Python 3.10 or newer is required")
        print(json.dumps({"ok": True, "data": handle(json.load(sys.stdin))}, ensure_ascii=False))
    except Exception as exc:
        # Keep the bridge protocol intact for unexpected generator/runtime
        # failures as well as validation and file I/O errors, so the UI can
        # always display the failure instead of receiving only a traceback.
        print(json.dumps({"ok": False, "error": str(exc) or type(exc).__name__}, ensure_ascii=False))
        sys.exit(2)
