#!/usr/bin/env python3
"""Local-first mind-map workspace with selectable DeepSeek or llama.cpp backend."""
import atexit
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

import keyring
import requests
import yaml
from flask import Flask, jsonify, request, send_from_directory
from meshmap_network import MeshMapNetwork, MAX_PAYLOAD_BYTES

APP_NAME = "MeshMap"
PORT = 8765
API_KEY_NAME = "deepseek_api_key"
MQTT_USER_NAME = "mqtt_username"
MQTT_PASS_NAME = "mqtt_password"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / APP_NAME
CONFIG_PATH = DATA_DIR / "settings.yaml"
GRAPH_PATH = DATA_DIR / "mindmap.json"
LOG_PATH = DATA_DIR / "llm-server.log"
DEFAULT_CONFIG = {
    "provider": "local",
    "deepseek_model": "deepseek-flash",
    "workspace_title": "Farmers' Market",
    "workspace_description": "A community map for local produce, seeds, tools, and other goods.",
    "publisher_id": "",
    "publish_enabled": False,
    "local_server_exe": "",
    "local_model_file": "",
    "local_host": "127.0.0.1",
    "local_port": 8080,
    "context_size": 4096,
    "gpu_layers": 99,
}
DEFAULT_GRAPH = {"nodes": [], "edges": [], "groups": [], "comments": []}
app = Flask(__name__, static_folder="mindmap_static", static_url_path="/static")
server_process = None
server_lock = threading.Lock()
network_client = None
network_lock = threading.RLock()
network_maps = {}
network_comments = {}
network_status = "Connecting to discovery network..."
network_connected = False


def load_config():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                config.update(loaded)
        except (OSError, yaml.YAMLError):
            pass
    changed = False
    if not config.get("publisher_id"):
        config["publisher_id"] = uuid.uuid4().hex
        changed = True
    if changed:
        save_config(config)
    return config


def save_config(config):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def load_graph():
    if GRAPH_PATH.exists():
        try:
            loaded = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return {key: loaded.get(key, []) for key in DEFAULT_GRAPH}
        except (OSError, json.JSONDecodeError):
            pass
    return {key: list(value) for key, value in DEFAULT_GRAPH.items()}


def save_graph(graph):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")


def receive_network_map(owner_id, payload):
    global network_maps
    with network_lock:
        if payload is None:
            network_maps.pop(owner_id, None)
            network_comments.pop(owner_id, None)
            return
        graph = payload.get("graph")
        if (not isinstance(graph, dict) or
            any(not isinstance(graph.get(key, []), list) for key in ("nodes", "edges", "groups"))):
            return
        groups = []
        for item in graph.get("groups", [])[:100]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            name = str(item.get("name", "")).strip()[:80]
            if name:
                groups.append({"id": item["id"][:80], "name": name})
        group_ids = {group["id"] for group in groups}
        nodes = []
        for item in graph.get("nodes", [])[:500]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            x, y = item.get("x", 30), item.get("y", 30)
            x = min(1800, max(0, x)) if isinstance(x, (int, float)) and math.isfinite(x) else 30
            y = min(1300, max(0, y)) if isinstance(y, (int, float)) and math.isfinite(y) else 30
            node = {"id": item["id"][:80], "name": str(item.get("name", "Untitled node"))[:120],
                    "description": str(item.get("description", ""))[:2000], "x": x, "y": y}
            if isinstance(item.get("parentId"), str):
                node["parentId"] = item["parentId"][:80]
            if isinstance(item.get("groupId"), str) and item["groupId"] in group_ids:
                node["groupId"] = item["groupId"][:80]
            nodes.append(node)
        valid_ids = {node["id"] for node in nodes}
        for node in nodes:
            if node.get("parentId") not in valid_ids:
                node.pop("parentId", None)
        edges = []
        for edge in graph.get("edges", [])[:1000]:
            if not isinstance(edge, dict):
                continue
            source, target = edge.get("source"), edge.get("target")
            if isinstance(source, str) and isinstance(target, str) and source in valid_ids and target in valid_ids:
                edges.append({"source": source, "target": target, "label": str(edge.get("label", ""))[:200]})
        graph["nodes"], graph["edges"], graph["groups"] = nodes, edges, groups
        payload["title"] = str(payload.get("title", "Untitled map"))[:120]
        payload["description"] = str(payload.get("description", ""))[:2000]
        payload["owner_id"] = owner_id
        if owner_id not in network_maps and len(network_maps) >= 1000:
            network_maps.pop(next(iter(network_maps)))
        network_maps[owner_id] = payload


def receive_network_comment(owner_id, comment_id, payload):
    if payload is None:
        with network_lock:
            network_comments.get(owner_id, {}).pop(comment_id, None)
        return
    text = payload.get("text")
    node_id = payload.get("node_id")
    if not isinstance(text, str) or not text.strip() or not isinstance(node_id, str):
        return
    comment = {
        "id": comment_id,
        "nodeId": node_id,
        "text": text[:1000],
        "createdAt": str(payload.get("created_at", "")),
        "author": str(payload.get("author", "Network user"))[:80],
    }
    with network_lock:
        comments = network_comments.setdefault(owner_id, {})
        if comment_id not in comments and len(comments) >= 5000:
            comments.pop(next(iter(comments)))
        comments[comment_id] = comment


def set_network_status(status):
    global network_status, network_connected
    network_status = status
    network_connected = status.startswith("Connected to ") or status.startswith("Connected;")
    if status.startswith("Connected to "):
        try:
            config = load_config()
            if config.get("publish_enabled"):
                publish_public_map()
            elif network_client is not None:
                unpublish_public_map(config["publisher_id"])
        except (RuntimeError, ValueError) as error:
            network_status = f"Connected; map publish issue: {error}"


def start_network():
    global network_client
    try:
        if network_client is not None:
            network_client.stop()
        username = keyring.get_password(APP_NAME, MQTT_USER_NAME) or os.environ.get("MESHBOOK_MQTT_USER", "")
        password = keyring.get_password(APP_NAME, MQTT_PASS_NAME) or os.environ.get("MESHBOOK_MQTT_PASS", "")
        network_client = MeshMapNetwork(receive_network_map, receive_network_comment,
                                        set_network_status, username, password)
        network_client.start()
    except (OSError, ValueError, RuntimeError, keyring.errors.KeyringError) as error:
        set_network_status(f"Network unavailable: {error}")


def stop_network():
    stop = getattr(network_client, "stop", None)
    if stop is not None:
        stop()


def unpublish_public_map(owner_id):
    network_client.unpublish_map(owner_id)
    comment_ids = {str(comment.get("id", "")) for comment in load_graph()["comments"]}
    with network_lock:
        comment_ids.update(network_comments.get(owner_id, {}).keys())
        network_maps.pop(owner_id, None)
        network_comments.pop(owner_id, None)
    for comment_id in comment_ids:
        if comment_id:
            network_client.unpublish_comment(owner_id, comment_id)


def publish_public_map():
    config = load_config()
    owner_id = config["publisher_id"]
    if network_client is None:
        raise RuntimeError("Discovery network has not started yet.")
    if not config.get("publish_enabled"):
        unpublish_public_map(owner_id)
        return
    graph = load_graph()
    public_map = {
        "schema_version": 1,
        "owner_id": owner_id,
        "title": str(config.get("workspace_title", "My workshop"))[:120],
        "description": str(config.get("workspace_description", ""))[:2000],
        "updated_at": time.time(),
        "graph": {"nodes": graph["nodes"], "edges": graph["edges"], "groups": graph["groups"]},
    }
    encoded = json.dumps(public_map, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"This map is {len(encoded)} bytes; public maps must be under {MAX_PAYLOAD_BYTES} bytes.")
    network_client.publish_map(owner_id, public_map)
    with network_lock:
        network_maps[owner_id] = public_map
    for comment in graph["comments"]:
        comment_id = str(comment.get("id") or uuid.uuid4().hex)
        network_client.publish_comment(owner_id, comment_id, {
            "node_id": comment.get("nodeId", ""),
            "text": comment.get("text", ""),
            "created_at": comment.get("createdAt", ""),
            "author": comment.get("author", "Workshop owner"),
        })


atexit.register(stop_network)


def provider_url(config):
    if config["provider"] == "deepseek":
        return "https://api.deepseek.com/chat/completions"
    host = config.get("local_host", "127.0.0.1")
    port = int(config.get("local_port", 8080))
    return f"http://{host}:{port}/v1/chat/completions"


def chat_completion(messages, timeout=180, response_format=None):
    config = load_config()
    headers = {"Content-Type": "application/json"}
    model = config.get("deepseek_model", "deepseek-flash")
    if config["provider"] == "deepseek":
        api_key = os.environ.get("MINDMAP_DEEPSEEK_API_KEY") or keyring.get_password(APP_NAME, API_KEY_NAME)
        if not api_key:
            raise RuntimeError("Add your DeepSeek API key in Settings. It is stored in Windows Credential Manager.")
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        model = "local-model"
    payload = {"model": model, "messages": messages, "temperature": 0.4}
    if response_format:
        payload["response_format"] = response_format
    response = requests.post(
        provider_url(config),
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def server_status():
    config = load_config()
    url = f"http://{config.get('local_host', '127.0.0.1')}:{int(config.get('local_port', 8080))}/health"
    try:
        response = requests.get(url, timeout=2)
        return response.ok, f"Server replied with HTTP {response.status_code}."
    except requests.RequestException as error:
        return False, str(error)


def diagnose_server():
    config = load_config()
    checks = []
    exe = Path(config.get("local_server_exe", ""))
    model = Path(config.get("local_model_file", ""))
    checks.append({"name": "llama-server.exe", "ok": exe.is_file(),
                   "detail": str(exe) if exe.is_file() else "Choose the llama-server executable from your llama.cpp folder."})
    checks.append({"name": "GGUF model", "ok": model.is_file() and model.suffix.lower() == ".gguf",
                   "detail": str(model) if model.is_file() else "Choose an existing .gguf model file; MeshMap does not download or copy it."})
    host = config.get("local_host", "127.0.0.1")
    port = int(config.get("local_port", 8080))
    try:
        with socket.create_connection((host, port), timeout=1):
            port_available = False
            port_detail = f"Port {port} is already occupied; it may be another server or an unrelated program."
    except OSError:
        port_available = True
        port_detail = f"Port {port} is available."
    healthy, health_detail = server_status()
    checks.append({"name": "Server port", "ok": healthy or port_available,
                   "detail": health_detail if healthy else port_detail})
    checks.append({"name": "Server health", "ok": healthy, "detail": health_detail})
    log_tail = ""
    if LOG_PATH.exists():
        try:
            log_tail = "\n".join(LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
        except OSError as error:
            log_tail = f"Could not read log: {error}"
    hints = []
    lowered = log_tail.lower()
    if "cudart" in lowered or "cuda" in lowered:
        hints.append("CUDA/GPU error: try setting GPU layers to 0 to test CPU mode, or use a llama.cpp build matching your CUDA version.")
    if "dll" in lowered or "vcruntime" in lowered or "side-by-side" in lowered:
        hints.append("Missing Windows runtime: install the Microsoft Visual C++ x64 Redistributable, then restart MeshMap.")
    if "out of memory" in lowered or "alloc" in lowered:
        hints.append("Memory allocation failed: choose a smaller GGUF, lower context size, or reduce GPU layers.")
    if not hints and not healthy:
        hints.append("Review the server log below. Confirm the GGUF matches the model format and lower context size or GPU layers if startup fails.")
    return {"checks": checks, "hints": hints, "log": log_tail}


def stop_server():
    global server_process
    with server_lock:
        process = server_process
        server_process = None
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


atexit.register(stop_server)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/graph")
def get_graph():
    graph = load_graph()
    owner_id = load_config()["publisher_id"]
    with network_lock:
        received_comments = list(network_comments.get(owner_id, {}).values())
    known_ids = {comment.get("id") for comment in graph["comments"]}
    graph["comments"].extend(comment for comment in received_comments if comment.get("id") not in known_ids)
    return jsonify(graph)


@app.put("/api/graph")
def put_graph():
    graph = request.get_json(silent=True)
    if not isinstance(graph, dict) or any(not isinstance(graph.get(key, []), list) for key in DEFAULT_GRAPH):
        return jsonify(error="Graph must contain node, edge, and comment lists."), 400
    save_graph({key: graph.get(key, []) for key in DEFAULT_GRAPH})
    result = {"ok": True, "published": False}
    if load_config().get("publish_enabled"):
        try:
            publish_public_map()
            result["published"] = True
        except (RuntimeError, ValueError) as error:
            result["network_error"] = str(error)
    return jsonify(result)


@app.get("/api/settings")
def get_settings():
    config = load_config()
    return jsonify({key: value for key, value in config.items() if key != "deepseek_api_key"})


@app.put("/api/settings")
def put_settings():
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify(error="Settings must be a JSON object."), 400
    config = load_config()
    if "provider" in incoming and incoming["provider"] not in ("local", "deepseek"):
        return jsonify(error="Provider must be local or deepseek."), 400
    for key in DEFAULT_CONFIG:
        if key in incoming:
            config[key] = incoming[key]
    try:
        config["local_port"] = int(config["local_port"])
        config["context_size"] = int(config["context_size"])
        config["gpu_layers"] = int(config["gpu_layers"])
        if not 1 <= config["local_port"] <= 65535:
            raise ValueError("Local port must be between 1 and 65535.")
        if config["context_size"] < 512 or config["gpu_layers"] < 0:
            raise ValueError("Context size must be at least 512 and GPU layers cannot be negative.")
        if not isinstance(config["publish_enabled"], bool):
            raise ValueError("Public publishing must be enabled or disabled.")
        config["workspace_title"] = str(config["workspace_title"]).strip()[:120]
        config["workspace_description"] = str(config["workspace_description"]).strip()[:2000]
        if not config["workspace_title"]:
            raise ValueError("Give your workshop a title.")
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400
    save_config(config)
    result = {"ok": True}
    try:
        publish_public_map()
    except (RuntimeError, ValueError) as error:
        result["network_error"] = str(error)
    return jsonify(result)


def build_node_context(graph, node_id):
    nodes = {node.get("id"): node for node in graph["nodes"] if isinstance(node, dict)}
    selected = nodes.get(node_id)
    if selected is None:
        return None
    incoming = {}
    outgoing = {}
    for edge in graph["edges"]:
        source, target = edge.get("source"), edge.get("target")
        if source in nodes and target in nodes:
            incoming.setdefault(target, []).append(edge)
            outgoing.setdefault(source, []).append(edge)

    roots = [key for key in nodes if key not in incoming]
    queue = [(root, [root]) for root in roots]
    seen = set()
    path = None
    while queue:
        current, current_path = queue.pop(0)
        if current == node_id:
            path = current_path
            break
        if current in seen:
            continue
        seen.add(current)
        for edge in outgoing.get(current, []):
            queue.append((edge["target"], current_path + [edge["target"]]))
    if path is None:
        path = [node_id]

    path_entries = []
    for index, path_id in enumerate(path):
        entry = {"node": nodes[path_id]}
        if index + 1 < len(path):
            next_id = path[index + 1]
            edge = next((item for item in outgoing.get(path_id, []) if item.get("target") == next_id), {})
            entry["relationship_to_next"] = edge.get("label", "")
        path_entries.append(entry)
    path_ids = set(path)
    comments = [comment for comment in graph["comments"] if comment.get("nodeId") in path_ids]
    return {
        "selected_node": selected,
        "path": path_entries,
        "outgoing_relationships": [
            {"label": edge.get("label", ""), "node": nodes[edge["target"]]}
            for edge in outgoing.get(node_id, [])
        ],
        "comments_on_path": comments,
    }


@app.put("/api/deepseek-key")
def put_deepseek_key():
    data = request.get_json(silent=True) or {}
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return jsonify(error="Enter an API key."), 400
    try:
        keyring.set_password(APP_NAME, API_KEY_NAME, api_key)
    except keyring.errors.KeyringError as error:
        return jsonify(error=f"Could not store key in the operating-system credential store: {error}"), 500
    return jsonify(ok=True)


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify(error="Enter a message."), 400
    try:
        config = load_config()
        owner_id = str(data.get("map_owner_id", ""))
        if owner_id:
            with network_lock:
                public_map = network_maps.get(owner_id)
                public_comments = list(network_comments.get(owner_id, {}).values())
            if not public_map:
                return jsonify(error="That public map is no longer available."), 404
            graph = {**public_map.get("graph", {}), "comments": public_comments}
            concept = {"title": public_map.get("title", "Shared workshop"),
                       "description": public_map.get("description", "")}
        else:
            graph = load_graph()
            concept = {"title": config["workspace_title"],
                       "description": config["workspace_description"]}
        selected_id = data.get("selected_node_id")
        selected_context = build_node_context(graph, selected_id) if selected_id else None
        context = {
            "workspace_concept": concept,
            "selected_node_context": selected_context,
            "comments": (selected_context["comments_on_path"] if selected_context
                         else graph["comments"][-100:]),
            "map_structure": {"nodes": graph["nodes"], "edges": graph["edges"],
                              "groups": graph.get("groups", [])},
        }
        map_context = json.dumps(context, ensure_ascii=False)[:24000]
        answer = chat_completion([
            {"role": "system", "content": "You are an assistant for the user's editable workshop or project concept. Use its description, the selected node's path, relationships, and comments as context. When asked to improve a path, suggest specific missing, ambiguous, or misordered nodes and explain why. Never edit the map or claim a semantic score proves truth; the owner decides changes."},
            {"role": "user", "content": f"Current map context:\n{map_context}\n\nQuestion:\n{message}"},
        ])
        return jsonify(answer=answer)
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as error:
        return jsonify(error=str(error)), 502


@app.post("/api/semantic")
def semantic_check():
    data = request.get_json(silent=True) or {}
    source = data.get("source", {})
    target = data.get("target", {})
    relationship = data.get("relationship", "")
    try:
        config = load_config()
        result = chat_completion([
            {"role": "system", "content": f"Assess whether the relationship is supported within the project concept '{config['workspace_title']}: {config['workspace_description']}'. Return only a JSON object with confidence_percent (integer 0-100) and explanation (one short paragraph). The score is model judgment, not verified truth."},
            {"role": "user", "content": json.dumps({"source": source, "relationship": relationship, "target": target}, ensure_ascii=False)},
        ], response_format={"type": "json_object"})
        parsed = json.loads(result)
        score = int(parsed.get("confidence_percent", 0))
        return jsonify(confidence_percent=max(0, min(100, score)), explanation=str(parsed.get("explanation", "No explanation returned.")))
    except (requests.RequestException, RuntimeError, KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
        return jsonify(error=str(error)), 502


@app.post("/api/local/start")
def start_local_server():
    global server_process
    config = load_config()
    exe = Path(config.get("local_server_exe", ""))
    model = Path(config.get("local_model_file", ""))
    if not exe.is_file():
        return jsonify(error="Choose the llama-server executable first.", diagnostics=diagnose_server()), 400
    if not model.is_file() or model.suffix.lower() != ".gguf":
        return jsonify(error="Choose an existing .gguf model file first.", diagnostics=diagnose_server()), 400
    healthy, detail = server_status()
    if healthy:
        return jsonify(ok=True, message="A local server is already responding.")
    command = [str(exe), "--model", str(model), "--host", config.get("local_host", "127.0.0.1"),
               "--port", str(config["local_port"]), "--ctx-size", str(config["context_size"]),
               "--n-gpu-layers", str(config["gpu_layers"])]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        log_file = LOG_PATH.open("a", encoding="utf-8", errors="replace")
        with server_lock:
            if server_process and server_process.poll() is None:
                return jsonify(error="MeshMap's local server is already running."), 409
            server_process = subprocess.Popen(command, cwd=str(exe.parent), stdout=log_file,
                                              stderr=subprocess.STDOUT,
                                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if server_process.poll() is not None:
                break
            healthy, detail = server_status()
            if healthy:
                return jsonify(ok=True, message="Local model server is ready.")
            time.sleep(0.5)
        return jsonify(error=f"Local server did not become ready: {detail}", diagnostics=diagnose_server()), 502
    except OSError as error:
        return jsonify(error=f"Could not start llama-server: {error}", diagnostics=diagnose_server()), 500


@app.post("/api/local/stop")
def stop_local_server():
    stop_server()
    return jsonify(ok=True)


@app.post("/api/local/browse")
def browse_local_file():
    data = request.get_json(silent=True) or {}
    file_kind = data.get("kind")
    if file_kind not in ("server", "model"):
        return jsonify(error="Choose either server or model."), 400
    dialog_script = (
        "import sys, tkinter as tk; from tkinter import filedialog; "
        "root=tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
        "kind=sys.argv[1]; "
        "filters=[('llama-server.exe','llama-server.exe'),('Executable','*.exe'),('All files','*.*')] if kind=='server' else [('GGUF models','*.gguf'),('All files','*.*')]; "
        "print(filedialog.askopenfilename(parent=root, filetypes=filters)); root.destroy()"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", dialog_script, file_kind], capture_output=True,
            text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or "The file dialog could not be opened."
            return jsonify(error=detail), 500
        return jsonify(path=result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired) as error:
        return jsonify(error=f"Could not open the file picker: {error}"), 500


@app.get("/api/local/diagnostics")
def local_diagnostics():
    return jsonify(diagnose_server())


@app.get("/api/network/status")
def get_network_status():
    config = load_config()
    with network_lock:
        map_count = len(network_maps)
    try:
        credentials_configured = bool(
            (keyring.get_password(APP_NAME, MQTT_USER_NAME) or os.environ.get("MESHBOOK_MQTT_USER")) and
            (keyring.get_password(APP_NAME, MQTT_PASS_NAME) or os.environ.get("MESHBOOK_MQTT_PASS"))
        )
    except keyring.errors.KeyringError:
        credentials_configured = bool(os.environ.get("MESHBOOK_MQTT_USER") and os.environ.get("MESHBOOK_MQTT_PASS"))
    return jsonify(connected=network_connected, status=network_status,
                   publish_enabled=config.get("publish_enabled", False), map_count=map_count,
                   credentials_configured=credentials_configured)


@app.put("/api/network/credentials")
def put_network_credentials():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not username or not password:
        return jsonify(error="Enter both the MQTT username and password."), 400
    try:
        keyring.set_password(APP_NAME, MQTT_USER_NAME, username)
        keyring.set_password(APP_NAME, MQTT_PASS_NAME, password)
    except keyring.errors.KeyringError as error:
        return jsonify(error=f"Could not save MQTT credentials in the operating-system credential store: {error}"), 500
    start_network()
    return jsonify(ok=True, message="Credentials saved. Reconnecting to the discovery network.")


@app.get("/api/network/search")
def search_network_maps():
    query = request.args.get("q", "").strip().casefold()[:120]
    with network_lock:
        maps = list(network_maps.values())
    results = []
    for public_map in maps:
        graph = public_map.get("graph", {})
        nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
        searchable = " ".join([
            str(public_map.get("title", "")), str(public_map.get("description", "")),
            *(str(node.get("name", "")) + " " + str(node.get("description", ""))
              for node in nodes if isinstance(node, dict)),
        ]).casefold()
        if query and query not in searchable:
            continue
        results.append({
            "owner_id": public_map.get("owner_id", ""),
            "title": public_map.get("title", "Untitled map"),
            "description": public_map.get("description", ""),
            "node_count": len(nodes),
            "updated_at": public_map.get("updated_at", 0),
        })
    results.sort(key=lambda item: item["title"].casefold())
    return jsonify(connected=network_connected, status=network_status,
                   results=results[:50])


@app.get("/api/network/maps/<owner_id>")
def get_network_map(owner_id):
    with network_lock:
        public_map = network_maps.get(owner_id)
        comments = list(network_comments.get(owner_id, {}).values())
    if not public_map:
        config = load_config()
        if owner_id != config["publisher_id"] or not config.get("publish_enabled"):
            return jsonify(error="That map is not currently available on the network."), 404
        graph = load_graph()
        public_map = {
            "schema_version": 1,
            "owner_id": owner_id,
            "title": config["workspace_title"],
            "description": config["workspace_description"],
            "graph": {"nodes": graph["nodes"], "edges": graph["edges"], "groups": graph["groups"]},
        }
    result = dict(public_map)
    result["comments"] = comments
    return jsonify(result)


@app.post("/api/network/comments")
def post_network_comment():
    data = request.get_json(silent=True) or {}
    owner_id = str(data.get("owner_id", ""))
    node_id = str(data.get("node_id", ""))
    text = str(data.get("text", "")).strip()
    if not owner_id or not node_id or not text or len(text) > 1000:
        return jsonify(error="Choose a map node and enter a comment of up to 1000 characters."), 400
    with network_lock:
        public_map = network_maps.get(owner_id)
    if not public_map:
        return jsonify(error="That map is not available on the network."), 404
    nodes = public_map.get("graph", {}).get("nodes", [])
    if not any(node.get("id") == node_id for node in nodes if isinstance(node, dict)):
        return jsonify(error="That node is not part of the published map."), 404
    if network_client is None:
        return jsonify(error="The discovery network is not available."), 503
    comment_id = uuid.uuid4().hex
    config = load_config()
    payload = {
        "node_id": node_id,
        "text": text,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "author": f"MeshMap user {config['publisher_id'][:6]}",
    }
    try:
        network_client.publish_comment(owner_id, comment_id, payload)
        receive_network_comment(owner_id, comment_id, payload)
    except (RuntimeError, ValueError) as error:
        return jsonify(error=str(error)), 503
    return jsonify(ok=True, comment={"id": comment_id, "nodeId": node_id,
                                     "text": text, "createdAt": payload["created_at"],
                                     "author": payload["author"]})


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{APP_NAME} is running at http://127.0.0.1:{PORT}")
    start_network()
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
