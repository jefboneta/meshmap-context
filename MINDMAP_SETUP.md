# MeshMap Context Setup

MeshMap Context is a local-first mind-map workspace for asking an LLM about a selected node and its surrounding project context. The app runs on your PC and opens in a browser.

## Requirements

- Windows 10 or newer
- Python 3.10 or newer
- Internet access to install Python dependencies

The installer creates a private Python environment at `%LOCALAPPDATA%\MeshMap\.venv`; Anaconda is not required. If Python is missing and `winget` is available, the installer offers to install Python 3.12 for the current user.

## Choose an AI provider

### DeepSeek API

1. Run `install-deepseek.bat`.
2. Open the MeshMap desktop shortcut.
3. Open **Settings**, enter your DeepSeek API key, and save.

API requests require internet access and may incur charges. The key is stored in Windows Credential Manager.

### Local GGUF model

1. Run `install-local-gguf.bat`.
2. Open the MeshMap desktop shortcut and go to **Settings**.
3. Choose an existing `.gguf` model and start the local server.

The local server binary is required because it loads the GGUF model and provides the HTTP API MeshMap uses. The GGUF file contains model weights; it does not run the model on its own. MeshMap connects to the server at `127.0.0.1:8080` by default.

Download the Windows llama.cpp server build from the [official llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases) and extract the complete archive. Keep `llama-server.exe` together with the DLLs and other runtime files from that archive. To have the installer copy the runtime automatically, place the extracted `llama-*-bin-win-*` folder beside the installer. Otherwise, select `llama-server.exe` in **Settings** and leave its neighboring runtime files in place.

The model file and server binary are not included in this repository. You do not need llama.cpp when using the DeepSeek API installer.

## Map context

Create a workshop or project map and select a node before asking the assistant a question. The prompt can include the selected node's path, related nodes, and comments. The model's response is a suggestion, not a verified correctness check, and the assistant does not directly edit the map structure.

## Optional public discovery

Public discovery is off by default. To share a map snapshot, enable **Publish this map to public discovery** in **Settings** and save. Other MeshMap users can search published titles, descriptions, and node text, open a read-only snapshot, and add comments.

Discovery uses the `mqtt.meshtastic.org` broker on the `meshmap/v1` topic and requires valid MQTT credentials. Credentials are stored in Windows Credential Manager. `MESHBOOK_MQTT_USER` and `MESHBOOK_MQTT_PASS` environment variables are also supported.

**Publishing is public, not private sharing.** Other broker subscribers may read published maps and comments. MQTT login does not create per-map access control, and author claims are not verified. Only publish content you are comfortable making public. Disable publishing to remove retained map and comment entries when connected.

This preview does not use Meshtastic radio transport, provide encrypted/private maps, or verify map ownership. Discovery availability depends on the broker and is not a hosted service guarantee.

## Local server troubleshooting

Open **Diagnostics** in the app to check the server executable, model file, port, health endpoint, and recent log output.

- Confirm the model path points to an existing `.gguf` file.
- If CUDA startup fails, set **GPU layers** to `0` to test CPU mode.
- If Windows reports missing DLLs, install the Microsoft Visual C++ x64 Redistributable and restart the app.
- If the model does not fit in memory, try a smaller GGUF or reduce context size and GPU layers.
- Check that another process is not already using port `8080`.

Settings, map data, and the local server log are stored under `%LOCALAPPDATA%\MeshMap`.
