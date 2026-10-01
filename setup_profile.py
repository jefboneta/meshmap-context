#!/usr/bin/env python3
"""Write the selected first-run provider profile without storing secrets."""
import argparse
import os
import uuid
from pathlib import Path

import yaml

APP_NAME = "MeshMap"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / APP_NAME
CONFIG_PATH = DATA_DIR / "settings.yaml"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("deepseek", "local"), required=True)
    parser.add_argument("--server-exe", default="")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings = {}
    if CONFIG_PATH.exists():
        try:
            existing = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                settings.update(existing)
        except (OSError, yaml.YAMLError):
            pass
    settings.setdefault("deepseek_model", "deepseek-flash")
    settings.setdefault("workspace_title", "Phone repair workshop")
    settings.setdefault("workspace_description", "A place to document phone repairs, parts, and service workflows.")
    settings.setdefault("publisher_id", uuid.uuid4().hex)
    settings.setdefault("publish_enabled", False)
    settings.setdefault("local_model_file", "")
    settings.setdefault("local_host", "127.0.0.1")
    settings.setdefault("local_port", 8080)
    settings.setdefault("context_size", 4096)
    settings.setdefault("gpu_layers", 99)
    settings["provider"] = args.provider
    if args.server_exe:
        settings["local_server_exe"] = args.server_exe
    else:
        settings.setdefault("local_server_exe", "")
    CONFIG_PATH.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    print(f"Configured {APP_NAME} for {args.provider} at {CONFIG_PATH}")


if __name__ == "__main__":
    main()
