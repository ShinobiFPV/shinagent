// -- Circuit Builder Module --------------------------------------------

const CB_THEME = {
    bg: '#0a0f0a',
    panel_bg: 'rgba(0, 30, 10, 0.9)',
    border: 'rgba(0, 200, 80, 0.2)',
    accent: '#00c850',
    dim: 'rgba(0, 200, 80, 0.4)',
    text: '#c8f0dc',
    wire_colors: {
        red: '#ff3c3c',
        black: '#303030',
        yellow: '#ffcc00',
        green: '#00dc78',
        blue: '#00aaff',
        orange: '#ff8c00',
        white: '#f0f0f0',
        auto: '#ffcc00',
    },
};

// Component visual definitions for the diagram
const COMPONENT_VISUALS = {
    arduino_uno: { color: '#00979d', label: 'Arduino Uno', w: 120, h: 80, shape: 'board' },
    arduino_nano: { color: '#00979d', label: 'Arduino Nano', w: 60, h: 100, shape: 'board' },
    esp32_devkit: { color: '#e74c3c', label: 'ESP32', w: 70, h: 110, shape: 'board' },
    raspberry_pi_pico: { color: '#c51a4a', label: 'Pi Pico', w: 65, h: 130, shape: 'board' },
    dht22: { color: '#27ae60', label: 'DHT22', w: 40, h: 50, shape: 'sensor' },
    dht11: { color: '#2ecc71', label: 'DHT11', w: 40, h: 50, shape: 'sensor' },
    hc_sr04: { color: '#3498db', label: 'HC-SR04', w: 70, h: 40, shape: 'sonar' },
    pir_motion: { color: '#e67e22', label: 'PIR', w: 45, h: 45, shape: 'dome' },
    mpu6050: { color: '#8e44ad', label: 'MPU6050', w: 50, h: 35, shape: 'ic' },
    servo_motor: { color: '#e74c3c', label: 'Servo', w: 50, h: 40, shape: 'motor' },
    relay_5v: { color: '#c0392b', label: 'Relay', w: 60, h: 45, shape: 'relay' },
    ws2812b: { color: '#9b59b6', label: 'NeoPixel', w: 90, h: 25, shape: 'strip' },
    oled_128x64: { color: '#1abc9c', label: 'OLED', w: 55, h: 40, shape: 'display' },
    lcd_16x2: { color: '#27ae60', label: '16x2 LCD', w: 100, h: 40, shape: 'display' },
    led_single: { color: '#f39c12', label: 'LED', w: 20, h: 30, shape: 'led' },
    resistor: { color: '#7f8c8d', label: 'R', w: 40, h: 18, shape: 'resistor' },
    button_momentary: { color: '#95a5a6', label: 'BTN', w: 25, h: 25, shape: 'button' },
};

let cbProject = null;
let cbCanvas = null;
let cbCtx = null;
let cbScale = 1.0;
let cbOffsetX = 0;
let cbOffsetY = 0;
let cbDragging = false;
let cbDragStart = { x: 0, y: 0 };

function initCircuitBuilder() {
    buildCircuitUI();
    startCBPolling();
}

function buildCircuitUI() {
    const pane = document.getElementById('pane-circuit');
    if (!pane) return;

    pane.style.background = CB_THEME.bg;
    pane.innerHTML = `
        <div style="height:100%;display:flex;flex-direction:column;
             font-family:'Courier New',monospace;color:${CB_THEME.text};
             overflow:hidden">

            <div style="background:${CB_THEME.panel_bg};
                 border-bottom:1px solid ${CB_THEME.border};
                 padding:6px 10px;display:flex;
                 align-items:center;gap:8px;flex-wrap:wrap">

                <div style="font-size:12px;font-weight:900;
                     color:${CB_THEME.accent};
                     letter-spacing:0.12em;margin-right:4px">
                    CIRCUIT BUILDER
                </div>

                <button class="cb-btn cb-btn-primary" onclick="cbNewProject()">+ NEW PROJECT</button>
                <button class="cb-btn" onclick="cbLoadProject()">OPEN</button>
                <button class="cb-btn" onclick="cbExportSVG()">EXPORT SVG</button>
                <button class="cb-btn" onclick="cbCopyCode()">COPY CODE</button>
                <button class="cb-btn" onclick="cbZoomFit()">FIT</button>

                <div style="flex:1;text-align:right;font-size:11px;color:${CB_THEME.dim}"
                     id="cb-project-title">
                    No project loaded
                </div>
            </div>

            <div style="flex:1;display:grid;grid-template-columns:1fr 280px;overflow:hidden">

                <div style="position:relative;overflow:hidden;background:#0a0f0a"
                     id="cb-canvas-container">

                    <canvas id="cb-diagram-canvas" style="position:absolute;inset:0;cursor:grab"></canvas>

                    <div style="position:absolute;bottom:10px;right:10px;display:flex;gap:4px">
                        <button class="cb-btn cb-btn-sm" onclick="cbZoom(1.2)">+</button>
                        <button class="cb-btn cb-btn-sm" onclick="cbZoom(0.8)">-</button>
                        <button class="cb-btn cb-btn-sm" onclick="cbZoomFit()">FIT</button>
                    </div>

                    <div id="cb-empty-state"
                         style="position:absolute;inset:0;display:flex;flex-direction:column;
                         align-items:center;justify-content:center;
                         color:rgba(0,200,80,0.2);text-align:center;pointer-events:none">
                        <div style="font-size:13px;font-weight:700;letter-spacing:0.1em">
                            CIRCUIT BUILDER
                        </div>
                        <div style="font-size:11px;margin-top:8px;max-width:250px;line-height:1.6">
                            Describe your project to Q2.<br>
                            The wiring diagram appears here.
                        </div>
                    </div>
                </div>

                <div style="background:${CB_THEME.panel_bg};
                     border-left:1px solid ${CB_THEME.border};
                     display:flex;flex-direction:column;overflow:hidden">

                    <div style="display:flex;border-bottom:1px solid ${CB_THEME.border}">
                        ${['Components', 'Wiring', 'Code', 'BOM'].map((t, i) => `
                            <div class="cb-tab ${i === 0 ? 'active' : ''}"
                                 onclick="cbSwitchTab(this, 'cb-tab-${t.toLowerCase()}')"
                                 style="flex:1;padding:6px;text-align:center;
                                 font-size:9px;letter-spacing:0.06em;cursor:pointer">
                                ${t.toUpperCase()}
                            </div>
                        `).join('')}
                    </div>

                    <div style="flex:1;overflow-y:auto;padding:10px">
                        <div id="cb-tab-components">
                            <div id="cb-component-list">
                                <div style="color:${CB_THEME.dim};font-size:10px">No project loaded</div>
                            </div>
                        </div>

                        <div id="cb-tab-wiring" style="display:none">
                            <div id="cb-wiring-list">
                                <div style="color:${CB_THEME.dim};font-size:10px">No connections defined</div>
                            </div>
                        </div>

                        <div id="cb-tab-code" style="display:none">
                            <div style="margin-bottom:6px;display:flex;justify-content:space-between;align-items:center">
                                <span style="font-size:9px;color:${CB_THEME.dim};letter-spacing:0.08em"
                                      id="cb-code-lang">ARDUINO C++</span>
                                <button class="cb-btn cb-btn-sm" onclick="cbCopyCode()">COPY</button>
                            </div>
                            <pre id="cb-code-display"
                                 style="font-size:9px;line-height:1.5;color:${CB_THEME.text};
                                 white-space:pre-wrap;word-break:break-word;margin:0"
                            ><span style="color:${CB_THEME.dim}">No code generated</span></pre>
                        </div>

                        <div id="cb-tab-bom" style="display:none">
                            <div id="cb-bom-list">
                                <div style="color:${CB_THEME.dim};font-size:10px">No BOM generated</div>
                            </div>
                        </div>
                    </div>

                    <div id="cb-warnings"
                         style="display:none;border-top:1px solid rgba(255,100,0,0.3);
                         padding:8px;background:rgba(255,80,0,0.06)"></div>

                    <div id="cb-build-steps"
                         style="display:none;border-top:1px solid ${CB_THEME.border};
                         padding:8px;max-height:120px;overflow-y:auto"></div>
                </div>
            </div>
        </div>
    `;

    if (!document.getElementById('cb-styles')) {
        const style = document.createElement('style');
        style.id = 'cb-styles';
        style.textContent = `
            .cb-btn {
                padding: 5px 10px;
                background: transparent;
                border: 1px solid rgba(0,200,80,0.3);
                color: rgba(0,220,100,0.8);
                border-radius: 3px;
                cursor: pointer;
                font-family: 'Courier New', monospace;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.06em;
            }
            .cb-btn:hover {
                border-color: rgba(0,200,80,0.7);
                color: #00c850;
                background: rgba(0,200,80,0.06);
            }
            .cb-btn-primary {
                background: rgba(0,200,80,0.1);
                border-color: rgba(0,200,80,0.5);
                color: #00c850;
            }
            .cb-btn-sm { padding: 3px 6px; font-size: 9px; }
            .cb-tab {
                color: rgba(0,200,80,0.4);
                border-bottom: 2px solid transparent;
                transition: all 0.15s;
            }
            .cb-tab.active { color: #00c850; border-bottom-color: #00c850; }
            .cb-tab:hover { color: rgba(0,200,80,0.8); }
            .cb-comp-card {
                padding: 6px 8px;
                margin-bottom: 5px;
                background: rgba(0,200,80,0.03);
                border: 1px solid rgba(0,200,80,0.1);
                border-radius: 3px;
                font-size: 10px;
            }
            .cb-conn-row {
                padding: 4px 6px;
                margin-bottom: 3px;
                font-size: 9px;
                border-left: 2px solid;
                line-height: 1.5;
            }
            .cb-bom-row {
                padding: 4px 0;
                font-size: 10px;
                border-bottom: 1px solid rgba(0,200,80,0.06);
                display: flex;
                gap: 8px;
            }
        `;
        document.head.appendChild(style);
    }

    const container = document.getElementById('cb-canvas-container');
    cbCanvas = document.getElementById('cb-diagram-canvas');
    if (cbCanvas && container) {
        resizeCBCanvas();
        cbCtx = cbCanvas.getContext('2d');

        cbCanvas.addEventListener('wheel', cbOnWheel, { passive: false });
        cbCanvas.addEventListener('mousedown', cbOnMouseDown);
        cbCanvas.addEventListener('mousemove', cbOnMouseMove);
        cbCanvas.addEventListener('mouseup', cbOnMouseUp);

        window.addEventListener('resize', resizeCBCanvas);
    }
}

function resizeCBCanvas() {
    const container = document.getElementById('cb-canvas-container');
    if (!cbCanvas || !container) return;
    cbCanvas.width = container.clientWidth;
    cbCanvas.height = container.clientHeight;
    drawCBDiagram();
}

// -- Diagram rendering ---------------------------------------------------

function drawCBDiagram() {
    if (!cbCtx || !cbCanvas) return;

    const W = cbCanvas.width;
    const H = cbCanvas.height;

    cbCtx.clearRect(0, 0, W, H);
    cbCtx.fillStyle = CB_THEME.bg;
    cbCtx.fillRect(0, 0, W, H);
    drawGrid();

    if (!cbProject) return;

    cbCtx.save();
    cbCtx.translate(cbOffsetX, cbOffsetY);
    cbCtx.scale(cbScale, cbScale);

    drawWires();
    drawComponents();

    cbCtx.restore();

    const empty = document.getElementById('cb-empty-state');
    if (empty) empty.style.display = 'none';
}

function drawGrid() {
    const W = cbCanvas.width;
    const H = cbCanvas.height;
    const gs = 20 * cbScale;

    cbCtx.strokeStyle = 'rgba(0,200,80,0.04)';
    cbCtx.lineWidth = 1;

    const startX = cbOffsetX % gs;
    const startY = cbOffsetY % gs;

    for (let x = startX; x < W; x += gs) {
        cbCtx.beginPath();
        cbCtx.moveTo(x, 0);
        cbCtx.lineTo(x, H);
        cbCtx.stroke();
    }
    for (let y = startY; y < H; y += gs) {
        cbCtx.beginPath();
        cbCtx.moveTo(0, y);
        cbCtx.lineTo(W, y);
        cbCtx.stroke();
    }
}

function getComponentScreenPos(instance) {
    const W = cbCanvas.width;
    const H = cbCanvas.height;
    const cx = (instance.x || 0.5) * (W - 200) + 100;
    const cy = (instance.y || 0.5) * (H - 200) + 100;
    return { x: cx, y: cy };
}

function drawComponents() {
    if (!cbProject) return;

    for (const comp of cbProject.components) {
        const vis = COMPONENT_VISUALS[comp.component_id] || {
            color: '#888', label: comp.label, w: 60, h: 40, shape: 'generic',
        };
        const pos = getComponentScreenPos(comp);
        const x = pos.x - vis.w / 2;
        const y = pos.y - vis.h / 2;

        drawComponentShape(x, y, vis.w, vis.h, vis, comp);
    }
}

function drawComponentShape(x, y, w, h, vis, comp) {
    const ctx = cbCtx;
    const color = vis.color || '#888';

    ctx.shadowColor = color + '40';
    ctx.shadowBlur = 12;

    ctx.fillStyle = color + '22';
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (ctx.roundRect) {
        ctx.roundRect(x, y, w, h, 4);
    } else {
        ctx.rect(x, y, w, h);
    }
    ctx.fill();
    ctx.stroke();

    ctx.shadowBlur = 0;

    ctx.fillStyle = color;
    ctx.font = 'bold 9px Courier New';
    ctx.textAlign = 'left';
    ctx.fillText(comp.instance_id, x + 4, y + 12);

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 10px Courier New';
    ctx.textAlign = 'center';
    ctx.fillText(vis.label, x + w / 2, y + h / 2 + 3);

    if (vis.shape === 'board') {
        ctx.fillStyle = color + '88';
        const dotCount = Math.max(2, Math.min(8, Math.floor(h / 10)));
        for (let i = 0; i < dotCount; i++) {
            const dy = y + 8 + i * (h - 16) / (dotCount - 1);
            ctx.beginPath();
            ctx.arc(x + 3, dy, 2, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.arc(x + w - 3, dy, 2, 0, Math.PI * 2);
            ctx.fill();
        }
    } else if (vis.shape === 'led') {
        ctx.fillStyle = color + 'cc';
        ctx.beginPath();
        ctx.moveTo(x + w / 2, y + 4);
        ctx.lineTo(x + w / 2 + 6, y + h - 4);
        ctx.lineTo(x + w / 2 - 6, y + h - 4);
        ctx.closePath();
        ctx.fill();
    } else if (vis.shape === 'resistor') {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(x + 4, y + h / 2);
        const steps = 5;
        const sw = (w - 8) / steps;
        for (let i = 0; i <= steps; i++) {
            ctx.lineTo(x + 4 + i * sw, y + h / 2 + (i % 2 === 0 ? -5 : 5));
        }
        ctx.stroke();
    }

    ctx.textAlign = 'left';
}

function drawWires() {
    if (!cbProject) return;

    const posMap = {};
    for (const comp of cbProject.components) {
        posMap[comp.instance_id] = getComponentScreenPos(comp);
    }

    for (const conn of cbProject.connections) {
        const fromPos = posMap[conn.from_instance];
        const toPos = posMap[conn.to_instance];
        if (!fromPos || !toPos) continue;

        const color = CB_THEME.wire_colors[conn.wire_color] || CB_THEME.wire_colors.auto;

        cbCtx.strokeStyle = color;
        cbCtx.lineWidth = 1.5;
        cbCtx.setLineDash([]);

        cbCtx.beginPath();
        cbCtx.moveTo(fromPos.x, fromPos.y);

        const cp1x = fromPos.x + (toPos.x - fromPos.x) * 0.5;
        const cp1y = fromPos.y;
        const cp2x = fromPos.x + (toPos.x - fromPos.x) * 0.5;
        const cp2y = toPos.y;

        cbCtx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, toPos.x, toPos.y);
        cbCtx.stroke();

        if (conn.note || conn.label) {
            const mx = (fromPos.x + toPos.x) / 2;
            const my = (fromPos.y + toPos.y) / 2;
            cbCtx.fillStyle = color + 'cc';
            cbCtx.font = '8px Courier New';
            cbCtx.textAlign = 'center';
            cbCtx.fillText(conn.note || conn.label, mx, my - 4);
            cbCtx.textAlign = 'left';
        }
    }
}

// -- Pan/Zoom -------------------------------------------------------------

function cbOnWheel(e) {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    cbScale = Math.max(0.2, Math.min(5, cbScale * factor));
    drawCBDiagram();
}

function cbOnMouseDown(e) {
    cbDragging = true;
    cbDragStart = { x: e.clientX - cbOffsetX, y: e.clientY - cbOffsetY };
    cbCanvas.style.cursor = 'grabbing';
}

function cbOnMouseMove(e) {
    if (!cbDragging) return;
    cbOffsetX = e.clientX - cbDragStart.x;
    cbOffsetY = e.clientY - cbDragStart.y;
    drawCBDiagram();
}

function cbOnMouseUp() {
    cbDragging = false;
    if (cbCanvas) cbCanvas.style.cursor = 'grab';
}

function cbZoom(factor) {
    cbScale = Math.max(0.2, Math.min(5, cbScale * factor));
    drawCBDiagram();
}

function cbZoomFit() {
    cbScale = 1.0;
    cbOffsetX = 0;
    cbOffsetY = 0;
    drawCBDiagram();
}

// -- Project loading -------------------------------------------------------

let cbPollInterval = null;

function startCBPolling() {
    if (cbPollInterval) clearInterval(cbPollInterval);
    cbPollInterval = setInterval(async () => {
        try {
            const r = await fetch(`${BASE}/api/circuit/active`).then(r => r.json());
            if (r.project && r.project.project_id !== cbProject?.project_id) {
                cbProject = r.project;
                renderCBProject();
            }
        } catch (e) {}
    }, 2000);
}

function renderCBProject() {
    if (!cbProject) return;

    const title = document.getElementById('cb-project-title');
    if (title) title.textContent = cbProject.title;

    drawCBDiagram();
    renderCBComponents();
    renderCBWiring();
    renderCBCode();
    renderCBBOM();
    renderCBWarnings();
    renderCBBuildSteps();
}

function renderCBComponents() {
    const el = document.getElementById('cb-component-list');
    if (!el || !cbProject) return;

    el.innerHTML = cbProject.components.map(comp => `
        <div class="cb-comp-card">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                    <span style="color:${CB_THEME.accent};font-weight:700;font-size:11px">${comp.instance_id}</span>
                    <span style="color:${CB_THEME.text};margin-left:6px;font-size:11px">${comp.label}</span>
                </div>
                <span style="font-size:9px;color:${CB_THEME.dim}">${comp.component_id}</span>
            </div>
            ${comp.notes ? `<div style="font-size:9px;color:${CB_THEME.dim};margin-top:3px">${comp.notes}</div>` : ''}
        </div>
    `).join('');
}

function renderCBWiring() {
    const el = document.getElementById('cb-wiring-list');
    if (!el || !cbProject) return;

    el.innerHTML = cbProject.connections.map(conn => {
        const color = CB_THEME.wire_colors[conn.wire_color] || CB_THEME.wire_colors.auto;
        return `
            <div class="cb-conn-row" style="border-left-color:${color}">
                <span style="color:${CB_THEME.accent}">${conn.from_instance}.${conn.from_pin}</span>
                <span style="color:${CB_THEME.dim}"> -&gt; </span>
                <span style="color:${CB_THEME.accent}">${conn.to_instance}.${conn.to_pin}</span>
                ${conn.note ? `<span style="color:${CB_THEME.dim}"> (${conn.note})</span>` : ''}
            </div>
        `;
    }).join('');
}

function renderCBCode() {
    const el = document.getElementById('cb-code-display');
    const lang = document.getElementById('cb-code-lang');
    if (!el || !cbProject) return;

    if (lang) {
        const labels = { arduino_cpp: 'ARDUINO C++', micropython: 'MICROPYTHON', circuitpython: 'CIRCUITPYTHON' };
        lang.textContent = labels[cbProject.code_language] || 'CODE';
    }

    if (cbProject.code) {
        el.textContent = cbProject.code;
    } else {
        el.innerHTML = `<span style="color:${CB_THEME.dim}">No code generated</span>`;
    }
}

function renderCBBOM() {
    const el = document.getElementById('cb-bom-list');
    if (!el || !cbProject) return;

    if (!cbProject.bom || !cbProject.bom.length) {
        el.innerHTML = `<div style="color:${CB_THEME.dim};font-size:10px">No BOM generated</div>`;
        return;
    }

    el.innerHTML = cbProject.bom.map(item => `
        <div class="cb-bom-row">
            <span style="color:${CB_THEME.accent};font-weight:700;width:24px;flex-shrink:0">${item.qty}x</span>
            <span style="color:${CB_THEME.text};flex:1">${item.part}</span>
            ${item.notes ? `<span style="color:${CB_THEME.dim};font-size:9px">${item.notes}</span>` : ''}
        </div>
    `).join('');
}

function renderCBWarnings() {
    const el = document.getElementById('cb-warnings');
    if (!el || !cbProject) return;

    if (!cbProject.warnings || !cbProject.warnings.length) {
        el.style.display = 'none';
        return;
    }

    el.style.display = 'block';
    el.innerHTML = cbProject.warnings.map(w => `
        <div style="font-size:9px;color:#ff8c00;margin-bottom:3px">! ${w}</div>
    `).join('');
}

function renderCBBuildSteps() {
    const el = document.getElementById('cb-build-steps');
    if (!el || !cbProject) return;

    if (!cbProject.build_steps || !cbProject.build_steps.length) {
        el.style.display = 'none';
        return;
    }

    el.style.display = 'block';
    el.innerHTML = `
        <div style="font-size:9px;color:${CB_THEME.dim};font-weight:700;
             letter-spacing:0.08em;margin-bottom:5px">BUILD STEPS</div>
        ${cbProject.build_steps.map((step, i) => `
            <div style="font-size:9px;color:${CB_THEME.text};padding:2px 0">
                <span style="color:${CB_THEME.accent};margin-right:6px">${i + 1}.</span>${step}
            </div>
        `).join('')}
    `;
}

// -- Tab switching ---------------------------------------------------------

function cbSwitchTab(btn, tabId) {
    document.querySelectorAll('.cb-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');

    ['components', 'wiring', 'code', 'bom'].forEach(name => {
        const el = document.getElementById(`cb-tab-${name}`);
        if (el) el.style.display = `cb-tab-${name}` === tabId ? 'block' : 'none';
    });
}

// -- Actions ----------------------------------------------------------------

function cbNewProject() {
    // There's no in-HUD project wizard -- Circuit Builder is designed
    // entirely through conversation with Q2 (voice or text), which then
    // calls create_project_from_json() and this tab picks it up via its
    // /api/circuit/active poll. Surface that instead of silently doing
    // nothing (the codebase has no global "send a chat message from the
    // HUD" hook to wire this button to).
    const title = document.getElementById('cb-project-title');
    if (title) {
        title.textContent = 'Tell Q2 what you want to build (voice or text) -- the diagram appears here automatically.';
        setTimeout(() => { if (!cbProject) title.textContent = 'No project loaded'; }, 6000);
    }
}

async function cbLoadProject() {
    const pid = prompt('Enter project ID:');
    if (!pid) return;

    const r = await fetch(`${BASE}/api/circuit/load/${encodeURIComponent(pid)}`, { method: 'POST' }).then(r => r.json());
    if (r.project) {
        cbProject = r.project;
        renderCBProject();
    }
}

function cbCopyCode() {
    if (cbProject?.code) {
        navigator.clipboard.writeText(cbProject.code);
    }
}

function cbExportSVG() {
    if (!cbProject) return;

    const svgData = canvasToSVG();
    const blob = new Blob([svgData], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${cbProject.title.replace(/\s+/g, '_')}.svg`;
    a.click();
    URL.revokeObjectURL(url);
}

function canvasToSVG() {
    if (!cbProject) return '<svg></svg>';

    const W = 800;
    const H = 600;
    let svg = `<svg width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg" style="background:#0a0f0a">`;

    for (const comp of cbProject.components) {
        const vis = COMPONENT_VISUALS[comp.component_id] || { color: '#888', label: comp.label, w: 60, h: 40 };
        const x = comp.x * (W - 100) + 50 - vis.w / 2;
        const y = comp.y * (H - 100) + 50 - vis.h / 2;
        const col = vis.color || '#888';

        svg += `<rect x="${x}" y="${y}" width="${vis.w}" height="${vis.h}" rx="4" ` +
               `fill="${col}22" stroke="${col}" stroke-width="1.5"/>`;
        svg += `<text x="${x + vis.w / 2}" y="${y + vis.h / 2 + 4}" fill="white" font-size="10" ` +
               `font-family="Courier New" text-anchor="middle">${vis.label}</text>`;
        svg += `<text x="${x + 3}" y="${y + 12}" fill="${col}" font-size="8" ` +
               `font-family="Courier New">${comp.instance_id}</text>`;
    }

    const posMap = {};
    for (const comp of cbProject.components) {
        posMap[comp.instance_id] = { x: comp.x * (W - 100) + 50, y: comp.y * (H - 100) + 50 };
    }

    for (const conn of cbProject.connections) {
        const from = posMap[conn.from_instance];
        const to = posMap[conn.to_instance];
        if (!from || !to) continue;

        const col = CB_THEME.wire_colors[conn.wire_color] || CB_THEME.wire_colors.auto;
        const cp1x = from.x + (to.x - from.x) * 0.5;

        svg += `<path d="M${from.x},${from.y} C${cp1x},${from.y} ${cp1x},${to.y} ${to.x},${to.y}" ` +
               `fill="none" stroke="${col}" stroke-width="1.5"/>`;
    }

    svg += `</svg>`;
    return svg;
}
