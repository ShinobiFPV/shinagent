# Q2 Controller Bridge -- ESP32-S2 on Flipper Zero's WiFi Dev Board
# ====================================================================
# Bridges UART (from the Flipper Zero) <-> WebSocket (to Q2's
# voice/controller_server.py, port 8767 by default). Flash CircuitPython
# to the ESP32-S2 first, then copy this file to the board's CIRCUITPY
# drive as code.py.
#
# UNVERIFIED AGAINST REAL HARDWARE -- I have no Flipper/ESP32-S2 devboard
# to test this against. What IS verified: the WebSocket frame-masking
# logic below (ws_send()) was checked against a live instance of the
# exact `websockets` Python server voice/controller_server.py uses --
# unmasked client frames (as an earlier draft of this file had) are
# rejected outright with close code 1002 "incorrect masking". This
# version masks every client->server frame per RFC 6455 5.1/5.3.
# Everything else here (exact `wifi`/`socketpool`/`busio.UART` CircuitPython
# API calls) should be checked against whatever CircuitPython version is
# actually flashed before relying on it.

import wifi
import socketpool
import json
import time
import os
import busio
import board

# WiFi credentials -- set these, or better, put them in settings.toml
# (CIRCUITPY_WIFI_SSID / CIRCUITPY_WIFI_PASSWORD) and read via os.getenv()
# instead of hardcoding here.
WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID", "YourWiFi")
WIFI_PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD", "YourPassword")

# UART to the Flipper Zero
uart = busio.UART(board.TX, board.RX, baudrate=115200)

ws_socket = None
ws_connected = False
q2_host = "192.168.1.100"
q2_port = 8767
device_name = "Flipper Zero"


def connect_wifi():
    print("Connecting to WiFi...")
    wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
    print(f"Connected: {wifi.radio.ipv4_address}")


def send_to_flipper(data: dict):
    """Send JSON response back to the Flipper via UART."""
    try:
        uart.write((json.dumps(data) + "\n").encode())
    except Exception as e:
        print(f"UART write error: {e}")


def connect_websocket(host: str, port: int, name: str) -> bool:
    """Open a WebSocket connection to Q2 (voice/controller_server.py)."""
    global ws_socket, ws_connected, q2_host, q2_port, device_name
    q2_host, q2_port, device_name = host, port, name

    try:
        pool = socketpool.SocketPool(wifi.radio)
        ws_socket = pool.socket()
        ws_socket.settimeout(5)
        ws_socket.connect((host, port))

        key = "dGhlIHNhbXBsZSBub25jZQ=="  # static base64 nonce -- fine for
        # a fixed, single-client server we control; a real client would
        # generate this randomly per RFC 6455 4.1.
        handshake = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        ws_socket.send(handshake.encode())

        buf = bytearray(1024)
        ws_socket.recv_into(buf)  # discard the handshake response

        ws_connected = True
        print(f"WebSocket connected to {host}:{port}")

        ws_send({"type": "identify", "device": "flipper", "name": name})
        send_to_flipper({"type": "connected"})
        return True

    except Exception as e:
        print(f"WS connect error: {e}")
        ws_connected = False
        send_to_flipper({"type": "disconnected", "error": str(e)})
        return False


def ws_send(data: dict):
    """Send a MASKED WebSocket text frame -- RFC 6455 5.1 requires every
    client->server frame to be masked; the reference server this talks to
    (Python's `websockets` library) rejects unmasked frames outright."""
    global ws_socket, ws_connected
    if not ws_connected or not ws_socket:
        return
    try:
        payload = json.dumps(data).encode()
        length = len(payload)
        mask_key = os.urandom(4)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if length < 126:
            header = bytes([0x81, 0x80 | length])
        else:
            header = bytes([0x81, 0x80 | 126, (length >> 8) & 0xFF, length & 0xFF])

        ws_socket.send(header + mask_key + masked)
    except Exception as e:
        print(f"WS send error: {e}")
        ws_connected = False
        send_to_flipper({"type": "disconnected"})


def ws_recv():
    """Non-blocking WebSocket frame read. Server->client frames are NOT
    masked (RFC 6455 5.1 -- masking is client-to-server only)."""
    global ws_socket, ws_connected
    if not ws_connected or not ws_socket:
        return None
    try:
        ws_socket.settimeout(0)
        header = bytearray(2)
        n = ws_socket.recv_into(header)
        if not n:
            return None
        length = header[1] & 0x7F
        if length == 126:
            ext = bytearray(2)
            ws_socket.recv_into(ext)
            length = (ext[0] << 8) | ext[1]
        payload = bytearray(length)
        ws_socket.recv_into(payload)
        return json.loads(payload.decode())
    except OSError:
        return None
    except Exception as e:
        print(f"WS recv error: {e}")
        ws_connected = False
        return None


# ── Main loop ─────────────────────────────────────────────

connect_wifi()

uart_buf = ""
last_ping = time.monotonic()

while True:
    if uart.in_waiting:
        data = uart.read(uart.in_waiting)
        if data:
            uart_buf += data.decode("utf-8", "ignore")
            while "\n" in uart_buf:
                line, uart_buf = uart_buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    cmd = msg.get("cmd") or msg.get("type", "")

                    if cmd == "connect":
                        connect_websocket(
                            msg.get("host", q2_host),
                            int(msg.get("port", q2_port)),
                            msg.get("name", "Flipper Zero"),
                        )
                    elif cmd == "button":
                        ws_send({"type": "button", "btn": msg.get("btn", ""), "state": msg.get("state", "press")})
                    elif cmd == "ping":
                        ws_send({"type": "ping"})
                    else:
                        ws_send(msg)  # forward as-is

                except Exception as e:
                    print(f"Parse error: {e}")

    if ws_connected:
        msg = ws_recv()
        if msg:
            send_to_flipper(msg)

    now = time.monotonic()
    if now - last_ping > 15:
        if ws_connected:
            ws_send({"type": "ping"})
        last_ping = now

    if not ws_connected:
        time.sleep(5)
        connect_websocket(q2_host, q2_port, device_name)

    time.sleep(0.01)
