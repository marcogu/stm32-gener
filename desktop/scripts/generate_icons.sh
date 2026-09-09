#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
npm --prefix "$ROOT" run tauri icon -- "$ROOT/public/icon.svg" -o "$ROOT/src-tauri/icons"
