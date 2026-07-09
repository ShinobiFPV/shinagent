// Q2 Controller for Flipper Zero
// ================================
// Requires: WiFi dev board (ESP32-S2, running code.py from this same
// directory) + a firmware build with JS scripting support (Momentum or
// recent Official firmware).
// Save to: /ext/apps/Scripts/q2_controller.js
// Run from: Apps > Scripts > q2_controller
//
// UNVERIFIED AGAINST REAL HARDWARE / FIRMWARE -- I have no Flipper Zero
// to test this against. Flipper's JS scripting API (event_loop/gui/serial
// module names, subscribe() signatures, input key names) is young and
// has changed across firmware releases -- check this against your
// installed firmware's actual JS API docs before relying on it. The
// serial<->UART wiring and the JSON message shapes exchanged with
// code.py are the part that matters for interop with Q2's
// voice/controller_server.py; the Flipper-side GUI/event-loop plumbing
// below is a best-effort sketch, not a verified implementation.

let eventLoop = require("event_loop");
let gui       = require("gui");
let serial    = require("serial");
let flipper   = require("flipper");
let textbox   = require("gui/text_box");

// Q2 server settings -- edit these to match your Pi:
let Q2_HOST = "192.168.1.203";
let Q2_PORT = "8767";

// Flipper button names -> code.py's button names (currently 1:1, kept as
// a separate map so either side can be remapped independently later).
let BTN_MAP = {
    "ok":    "ok",
    "back":  "back",
    "up":    "up",
    "down":  "down",
    "left":  "left",
    "right": "right",
};

let view = textbox.makeWith({ text: "Q2 Controller\nConnecting...\n", font: "primary" });
gui.viewPort().setView(view);

serial.setup("usart", 115200);

let connected = false;
let lastPing  = Date.now();

function sendToESP32(msg) {
    // JSON line to the ESP32, which forwards it over WebSocket to Q2.
    serial.write(msg + "\n");
}

function updateDisplay(line1, line2) {
    view.set("text",
        "Q2 Controller\n" +
        (connected ? "[CONNECTED]\n" : "[NOT CONNECTED]\n") +
        line1 + "\n" + line2
    );
}

sendToESP32(JSON.stringify({ cmd: "connect", host: Q2_HOST, port: Q2_PORT, name: flipper.getName() }));
updateDisplay("Connecting...", Q2_HOST + ":" + Q2_PORT);

function onKey(key, isPress) {
    let btn = BTN_MAP[key];
    if (!btn) return;

    sendToESP32(JSON.stringify({ type: "button", btn: btn, state: isPress ? "press" : "release" }));

    if (isPress) {
        updateDisplay("BTN: " + btn.toUpperCase(), connected ? "Q2 OK" : "Not connected");
    }
}

function checkSerial() {
    let data = serial.readLine(0);  // non-blocking
    if (!data) return;
    try {
        let msg = JSON.parse(data);
        if (msg.type === "connected") {
            connected = true;
            updateDisplay("Connected!", Q2_HOST);
        } else if (msg.type === "disconnected") {
            connected = false;
            updateDisplay("Disconnected", "Retrying...");
        } else if (msg.type === "pong") {
            lastPing = Date.now();
        }
    } catch (e) {}
}

function maybePing() {
    if (Date.now() - lastPing > 10000) {
        sendToESP32(JSON.stringify({ type: "ping" }));
        lastPing = Date.now();
    }
}

let keys = ["ok", "back", "up", "down", "left", "right"];
let keySubscriptions = keys.map(key =>
    eventLoop.subscribe(eventLoop.input[key], function (_, wasPressed) {
        onKey(key, wasPressed);
    })
);

let timer = eventLoop.timer("periodic", 500);
eventLoop.subscribe(timer, function () {
    checkSerial();
    maybePing();
});

eventLoop.run();
