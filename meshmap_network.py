"""Public MQTT transport for MeshMap snapshots and node comments."""
import json
import os

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MESHMAP_MQTT_HOST", "mqtt.meshtastic.org")
MQTT_PORT = int(os.environ.get("MESHMAP_MQTT_PORT", "1883"))
MQTT_TOPIC = os.environ.get("MESHMAP_TOPIC", "meshmap/v1")
MAX_PAYLOAD_BYTES = 48_000


class MeshMapNetwork:
    def __init__(self, on_map, on_comment, on_status, username="", password=""):
        self.on_map = on_map
        self.on_comment = on_comment
        self.on_status = on_status
        self.host = MQTT_HOST
        self.port = MQTT_PORT
        self.connection_rejected = False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username or password:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self):
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except (OSError, RuntimeError):
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if getattr(reason_code, "value", reason_code) != 0:
            self.connection_rejected = True
            self.on_status(f"MQTT connection rejected: {reason_code}")
            return
        self.connection_rejected = False
        client.subscribe([(f"{MQTT_TOPIC}/maps/+", 0), (f"{MQTT_TOPIC}/comments/+/+", 0)])
        self.on_status(f"Connected to {self.host}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if self.connection_rejected:
            return
        if getattr(reason_code, "value", reason_code) != 0:
            self.on_status(f"MQTT disconnected: {reason_code}")
        else:
            self.on_status("MQTT disconnected")

    def _on_message(self, client, userdata, message):
        topic = message.topic.split("/")
        if len(message.payload) > MAX_PAYLOAD_BYTES:
            return
        prefix = MQTT_TOPIC.split("/")
        if not message.payload and topic[:len(prefix)] == prefix:
            tail = topic[len(prefix):]
            if len(tail) == 2 and tail[0] == "maps":
                self.on_map(tail[1], None)
                return
            if len(tail) == 3 and tail[0] == "comments":
                self.on_comment(tail[1], tail[2], None)
                return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if len(topic) == 4 and topic[:3] == [*MQTT_TOPIC.split("/"), "maps"]:
            self.on_map(topic[3], payload)
        elif len(topic) == 5 and topic[:3] == [*MQTT_TOPIC.split("/"), "comments"]:
            self.on_comment(topic[3], topic[4], payload)

    def _publish(self, topic, payload, retain):
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"Published data exceeds {MAX_PAYLOAD_BYTES} bytes.")
        if not self.client.is_connected():
            raise RuntimeError("MeshMap is not connected to the discovery network yet.")
        info = self.client.publish(topic, encoded, qos=0, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with code {info.rc}.")

    def publish_map(self, owner_id, payload):
        self._publish(f"{MQTT_TOPIC}/maps/{owner_id}", payload, retain=True)

    def unpublish_map(self, owner_id):
        if self.client.is_connected():
            self.client.publish(f"{MQTT_TOPIC}/maps/{owner_id}", b"", qos=0, retain=True)

    def unpublish_comment(self, owner_id, comment_id):
        if self.client.is_connected():
            self.client.publish(f"{MQTT_TOPIC}/comments/{owner_id}/{comment_id}", b"", qos=0, retain=True)

    def publish_comment(self, owner_id, comment_id, payload):
        self._publish(f"{MQTT_TOPIC}/comments/{owner_id}/{comment_id}", payload, retain=True)
