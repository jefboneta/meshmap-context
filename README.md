# MeshMap Context

**Local-first mind maps with LLM context for Meshtastic projects and other complex work.**

MeshMap Context turns a branching project map into useful context for an AI assistant. It gives the model the selected node, its path through the map, connected nodes, and comments, so responses stay anchored to the current problem. You remain in control of the map; the assistant suggests, but does not rewrite the tree.

The app runs locally in your browser. Choose a local GGUF model through `llama-server`, or use the DeepSeek API. Optional public discovery uses MQTT and is disabled unless you enable it.

## What it does

- Build and edit a mind map for a project, workshop, or research topic.
- Ask an LLM about a selected node using its surrounding map context.
- Add notes and comments to preserve decisions and observations.
- Optionally publish a read-only snapshot for discovery and comments.
- Check local model-server health and diagnostics in the app.

The name reflects the app's use for Meshtastic-related planning and knowledge. **This preview does not communicate with Meshtastic radios**; its optional discovery feature uses an MQTT broker over the internet.

## Quick start on Windows

Requirements: Windows 10 or newer and Python 3.10+. Anaconda is not required. The installer creates an isolated `.venv` under `%LOCALAPPDATA%\MeshMap`.

1. Download or clone this repository.
2. Choose one installer:
   - `install-deepseek.bat` for the DeepSeek API. API use requires internet access and may incur charges.
   - `install-local-gguf.bat` for a local GGUF model. You provide the model file and select it in Settings.
3. Open the MeshMap desktop shortcut. Configure the provider in **Settings**.

For local mode, the installer can copy `llama-server.exe` and its neighboring runtime files when a matching `llama-*-bin-win-*` folder is placed beside the installer. No model or large runtime binary is bundled in this repository. If you already have `llama-server.exe`, select its path in Settings instead.

The server binary is the program that loads your GGUF model and exposes the local API MeshMap uses at `127.0.0.1:8080`; a GGUF model file by itself cannot serve requests. Download a Windows build from the [official llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases), extract the complete archive, and follow the [local GGUF setup guide](MINDMAP_SETUP.md#local-gguf-model). You do not need this binary when using the DeepSeek API.

## Setup and privacy

See [MINDMAP_SETUP.md](MINDMAP_SETUP.md) for provider setup, MQTT discovery, privacy details, diagnostics, and troubleshooting.

Map data and settings are stored locally under `%LOCALAPPDATA%\MeshMap`. DeepSeek credentials and MQTT credentials are stored using Windows Credential Manager. Do not publish secrets or private map content. Public MQTT discovery is visible to other broker subscribers and does not provide per-map privacy or verified ownership.

## Project files

- `mindmap_app.py` - Flask app and local API
- `meshmap_network.py` - optional MQTT discovery and comments
- `mindmap_static/` - browser interface
- `install-deepseek.bat`, `install-local-gguf.bat` - Windows installer entry points
- `install-meshmap-common.bat` - shared installer
- `requirements-mindmap.txt` - Python dependencies

## License

MIT. See [LICENSE](LICENSE).
