// ── Beavis & Butthead Mode Module ─────────────────────────────────────
// Talks to face/server.py (via hud_server.py's /api/bb/* proxy, or
// hud/demo_data.py in --demo mode), not webapp/server.py -- see
// hud_server.py's comment on the BB proxy block for why: face/server.py
// runs in-process with the live voice/text agent, so its session state
// is the real one, not a disconnected copy in a separate subprocess.

let bbState = {
    session: null,
    nice_guy: false,
    current_video: null,
    q2_is: 'butthead',
};

function initBB() {
    buildBBUI();
    loadBBSession();
}

function buildBBUI() {
    const pane = document.getElementById('pane-bb');
    if (!pane) return;

    pane.innerHTML = `
        <div style="height:100%;position:relative;display:flex;
             flex-direction:column;
             background:#0a0a0a;
             font-family:'Courier New',monospace;
             color:#e0d000;overflow:hidden">

            <!-- Header bar -->
            <div style="background:#1a0a00;
                 border-bottom:2px solid #3a2000;
                 padding:6px 12px;
                 display:flex;
                 align-items:center;
                 justify-content:space-between">
                <div style="font-size:13px;
                     font-weight:900;
                     color:#ff8c00;
                     letter-spacing:0.1em">
                    BEAVIS &amp; BUTT-HEAD
                </div>
                <div style="display:flex;gap:8px;
                     align-items:center">
                    <div style="display:flex;
                         align-items:center;gap:6px">
                        <span style="font-size:9px;
                             color:rgba(255,140,0,0.5)">
                            NICE GUY
                        </span>
                        <div class="bb-toggle"
                             id="bb-nice-toggle"
                             onclick="bbToggleNiceGuy()">
                            <div class="bb-toggle-thumb"></div>
                        </div>
                    </div>
                    <button class="bb-btn"
                            onclick="bbSwapChars()"
                            style="font-size:9px">
                        SWAP CHARS
                    </button>
                </div>
            </div>

            <!-- Main content -->
            <div style="flex:1;display:grid;
                 grid-template-columns:1fr 320px;
                 gap:0;overflow:hidden">

                <!-- TV panel -->
                <div style="display:flex;
                     flex-direction:column;
                     align-items:center;
                     justify-content:center;
                     padding:16px;
                     background:#0a0a0a;overflow-y:auto">

                    <div id="bb-tv" style="
                        position:relative;
                        width:100%;
                        max-width:480px;
                        background:#1a1000;
                        border:4px solid #3a2800;
                        border-radius:16px;
                        overflow:hidden;
                        box-shadow: 0 0 40px rgba(255,140,0,0.15),
                                    inset 0 0 20px rgba(0,0,0,0.8);
                    ">
                        <div id="bb-screen" style="
                            position:relative;
                            background:#000;
                            padding-top:56.25%;
                            border-radius:8px;
                            overflow:hidden;
                        ">
                            <div style="
                                position:absolute;inset:0;
                                background: repeating-linear-gradient(
                                    0deg, transparent, transparent 2px,
                                    rgba(0,0,0,0.15) 2px, rgba(0,0,0,0.15) 3px
                                );
                                pointer-events:none;z-index:10;
                            "></div>
                            <div style="
                                position:absolute;inset:0;
                                background: radial-gradient(
                                    ellipse at 50% 50%,
                                    rgba(255,200,0,0.03) 0%,
                                    rgba(0,0,0,0.2) 70%
                                );
                                pointer-events:none;z-index:11;
                            "></div>
                            <div id="bb-video-container"
                                 style="position:absolute;inset:0;z-index:1">
                                <div id="bb-no-video"
                                     style="position:absolute;inset:0;
                                     display:flex;align-items:center;
                                     justify-content:center;
                                     background:#000;
                                     color:rgba(255,140,0,0.3);
                                     font-size:12px;text-align:center">
                                    <div>
                                        <div style="font-size:24px;margin-bottom:8px">TV</div>
                                        SELECT VIDEOS TO BEGIN
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div style="background:#1a1000;padding:6px 12px;
                             display:flex;justify-content:space-between;
                             align-items:center;
                             border-top:1px solid #2a1800">
                            <div id="bb-now-playing"
                                 style="font-size:10px;
                                 color:rgba(255,140,0,0.6)">
                                NO SIGNAL
                            </div>
                            <div style="display:flex;gap:6px">
                                <button class="bb-btn"
                                        onclick="bbNextVideo()">NEXT &#9658;</button>
                            </div>
                        </div>
                    </div>

                    <div style="display:flex;gap:8px;
                         margin-top:12px;flex-wrap:wrap;
                         justify-content:center">
                        <button class="bb-btn bb-btn-primary"
                                onclick="bbStartSession()">
                            NEW SESSION
                        </button>
                        <button class="bb-btn"
                                onclick="bbMarkReplay(true)">
                            + REPLAY LIST
                        </button>
                        <button class="bb-btn"
                                onclick="bbMarkReplay(false)">
                            NOPE
                        </button>
                        <button class="bb-btn"
                                onclick="bbShowReplayList()">
                            REPLAY LIST
                        </button>
                    </div>
                </div>

                <!-- Commentary sidebar -->
                <div style="background:#0f0800;
                     border-left:2px solid #2a1800;
                     display:flex;flex-direction:column;
                     overflow:hidden">

                    <div style="padding:8px;
                         border-bottom:1px solid #2a1800;
                         display:flex;gap:8px">
                        <div id="bb-char-butthead"
                             style="flex:1;text-align:center;padding:6px;
                             border:1px solid rgba(255,140,0,0.2);
                             border-radius:4px;font-size:10px">
                            <div style="color:rgba(255,140,0,0.6)">BUTTHEAD</div>
                            <div id="bb-butthead-label"
                                 style="font-size:8px;
                                 color:rgba(255,140,0,0.3)">Q2</div>
                        </div>
                        <div id="bb-char-beavis"
                             style="flex:1;text-align:center;padding:6px;
                             border:1px solid rgba(255,140,0,0.2);
                             border-radius:4px;font-size:10px">
                            <div style="color:rgba(255,140,0,0.6)">BEAVIS</div>
                            <div id="bb-beavis-label"
                                 style="font-size:8px;
                                 color:rgba(255,140,0,0.3)">YOU</div>
                        </div>
                    </div>

                    <div id="bb-commentary"
                         style="flex:1;overflow-y:auto;padding:10px;
                         display:flex;flex-direction:column;gap:6px">
                        <div style="color:rgba(255,140,0,0.2);
                             font-size:10px;text-align:center;
                             padding:20px 0">
                            Session commentary appears here
                        </div>
                    </div>

                    <div style="padding:8px;border-top:1px solid #2a1800">
                        <div style="font-size:9px;
                             color:rgba(255,140,0,0.4);
                             margin-bottom:4px">
                            YOUR COMMENT (as
                            <span id="bb-user-char">BEAVIS</span>):
                        </div>
                        <div style="display:flex;gap:6px">
                            <input id="bb-user-input"
                                   placeholder="heh heh..."
                                   style="flex:1;
                                   background:rgba(255,140,0,0.05);
                                   border:1px solid rgba(255,140,0,0.2);
                                   color:#e0d000;padding:6px 8px;
                                   border-radius:3px;font-family:inherit;
                                   font-size:11px"
                                   onkeydown="if(event.key==='Enter')bbUserComment()">
                            <button class="bb-btn bb-btn-primary"
                                    onclick="bbUserComment()">
                                SAY IT
                            </button>
                        </div>

                        <div style="display:flex;gap:4px;
                             margin-top:6px;flex-wrap:wrap">
                            ${[
                                ['This rocks', 'this rocks'],
                                ['This sucks', 'this sucks'],
                                ['Heh heh heh', 'heh heh heh'],
                                ['Change it', 'change it'],
                                ['Yeah yeah', 'yeah yeah'],
                                ['Whoa', 'whoa'],
                            ].map(([label, val]) => `
                                <button class="bb-btn"
                                        style="padding:3px 6px;font-size:9px"
                                        onclick="bbQuickComment('${val}')">
                                    ${label}
                                </button>
                            `).join('')}
                        </div>
                    </div>
                </div>
            </div>

            <!-- Selection modal -->
            <div id="bb-select-modal"
                 style="display:none;position:absolute;inset:0;
                 background:rgba(0,0,0,0.92);z-index:100;
                 overflow-y:auto;padding:16px">
                <div style="max-width:600px;margin:0 auto">
                    <div style="font-size:14px;font-weight:900;
                         color:#ff8c00;margin-bottom:12px">
                        PICK 5 VIDEOS. UH HUH HUH.
                    </div>
                    <div id="bb-candidate-list"
                         style="display:grid;
                         grid-template-columns:1fr 1fr;
                         gap:6px;margin-bottom:12px">
                    </div>
                    <div style="display:flex;gap:8px">
                        <button class="bb-btn bb-btn-primary"
                                onclick="bbConfirmSelection()">
                            YEAH. THESE 5.
                        </button>
                        <button class="bb-btn"
                                onclick="bbSurpriseMe()">
                            SURPRISE ME
                        </button>
                        <button class="bb-btn"
                                onclick="hideBBModal()">
                            CANCEL
                        </button>
                    </div>
                    <div id="bb-selection-count"
                         style="margin-top:8px;font-size:10px;
                         color:rgba(255,140,0,0.5)">
                        0/5 selected
                    </div>
                </div>
            </div>
        </div>
    `;

    if (!document.getElementById('bb-styles')) {
        const style = document.createElement('style');
        style.id = 'bb-styles';
        style.textContent = `
            .bb-btn {
                padding: 5px 10px;
                background: transparent;
                border: 1px solid rgba(255,140,0,0.3);
                color: rgba(255,200,0,0.8);
                border-radius: 3px;
                cursor: pointer;
                font-family: 'Courier New', monospace;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.06em;
            }
            .bb-btn:hover {
                border-color: rgba(255,140,0,0.7);
                color: #ff8c00;
                background: rgba(255,140,0,0.06);
            }
            .bb-btn-primary {
                background: rgba(255,140,0,0.12);
                border-color: rgba(255,140,0,0.5);
                color: #ff8c00;
            }
            .bb-toggle {
                width: 32px; height: 16px;
                background: rgba(255,140,0,0.1);
                border: 1px solid rgba(255,140,0,0.3);
                border-radius: 8px;
                cursor: pointer;
                position: relative;
                transition: background 0.2s;
            }
            .bb-toggle.active {
                background: rgba(0,220,120,0.2);
                border-color: rgba(0,220,120,0.5);
            }
            .bb-toggle-thumb {
                position: absolute;
                top: 2px; left: 2px;
                width: 10px; height: 10px;
                border-radius: 50%;
                background: rgba(255,140,0,0.6);
                transition: all 0.2s;
            }
            .bb-toggle.active .bb-toggle-thumb {
                left: 18px;
                background: #00dc78;
            }
            .bb-comment {
                padding: 6px 8px;
                border-radius: 4px;
                font-size: 11px;
                line-height: 1.4;
                animation: bbFadeIn 0.3s ease-out;
            }
            .bb-comment.butthead {
                background: rgba(255,140,0,0.06);
                border-left: 2px solid #ff8c00;
                color: #e0d000;
            }
            .bb-comment.beavis {
                background: rgba(0,200,255,0.04);
                border-left: 2px solid #00c8ff;
                color: #e0f0ff;
            }
            .bb-comment.user {
                background: rgba(0,220,120,0.04);
                border-left: 2px solid #00dc78;
                color: #c8f0dc;
                font-style: italic;
            }
            .bb-comment .bb-speaker {
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.1em;
                margin-bottom: 2px;
                opacity: 0.6;
            }
            .bb-candidate-item {
                padding: 8px;
                background: rgba(255,140,0,0.03);
                border: 1px solid rgba(255,140,0,0.1);
                border-radius: 4px;
                cursor: pointer;
                transition: all 0.1s;
            }
            .bb-candidate-item:hover {
                border-color: rgba(255,140,0,0.35);
                background: rgba(255,140,0,0.07);
            }
            .bb-candidate-item.selected {
                border-color: #ff8c00;
                background: rgba(255,140,0,0.12);
            }
            @keyframes bbFadeIn {
                from { opacity: 0; transform: translateY(4px); }
                to   { opacity: 1; transform: translateY(0); }
            }
        `;
        document.head.appendChild(style);
    }
}

// ── Session management ─────────────────────────────────────────────

async function loadBBSession() {
    try {
        const r = await fetch(`${BASE}/api/bb/session`).then(r => r.json());
        bbState.session = r;
        if (r.q2_is) bbState.q2_is = r.q2_is;
        if (r.current_video) {
            bbState.current_video = r.current_video;
            updateNowPlaying(r.current_video);
        }
    } catch (e) {}
}

async function bbStartSession() {
    const r = await fetch(`${BASE}/api/bb/candidates`, { method: 'POST' }).then(r => r.json());
    bbState.candidates = r.candidates || [];
    showBBModal();
}

function showBBModal() {
    const modal = document.getElementById('bb-select-modal');
    const list = document.getElementById('bb-candidate-list');
    if (!modal || !list) return;

    list.innerHTML = bbState.candidates.map((v, i) => `
        <div class="bb-candidate-item"
             id="bb-cand-${i}"
             onclick="toggleBBCandidate(${i})"
             data-idx="${i}">
            <div style="font-size:10px;font-weight:700;
                 color:#e0d000">${i+1}. ${v.artist}</div>
            <div style="font-size:9px;
                 color:rgba(255,200,0,0.5)">${v.title}</div>
        </div>
    `).join('');

    modal.style.display = 'block';
    window._bbSelected = new Set();
    updateBBSelectionCount();
}

function toggleBBCandidate(idx) {
    if (!window._bbSelected) window._bbSelected = new Set();
    const el = document.getElementById(`bb-cand-${idx}`);

    if (window._bbSelected.has(idx)) {
        window._bbSelected.delete(idx);
        el?.classList.remove('selected');
    } else if (window._bbSelected.size < 5) {
        window._bbSelected.add(idx);
        el?.classList.add('selected');
    }
    updateBBSelectionCount();
}

function updateBBSelectionCount() {
    const el = document.getElementById('bb-selection-count');
    if (el) {
        const n = window._bbSelected?.size || 0;
        el.textContent = `${n}/5 selected`;
        el.style.color = n === 5 ? '#00dc78' : 'rgba(255,140,0,0.5)';
    }
}

async function bbConfirmSelection() {
    const indices = Array.from(window._bbSelected || []);
    if (indices.length < 1) return;
    const selection = indices.map(i => i + 1).join(', ');

    const r = await fetch(`${BASE}/api/bb/select`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selection }),
    }).then(r => r.json());

    hideBBModal();
    addBBComment(bbState.q2_is, r.response || "Okay. Let's watch.");
    await loadBBSession();
    await bbPlayCurrent();
}

async function bbSurpriseMe() {
    const r = await fetch(`${BASE}/api/bb/select`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selection: 'surprise me' }),
    }).then(r => r.json());

    hideBBModal();
    addBBComment(bbState.q2_is, r.response || "Uh okay. Surprise.");
    await loadBBSession();
    await bbPlayCurrent();
}

function hideBBModal() {
    const modal = document.getElementById('bb-select-modal');
    if (modal) modal.style.display = 'none';
}

// ── Video playback ──────────────────────────────────────────────────

async function bbPlayCurrent() {
    const r = await fetch(`${BASE}/api/bb/start_video`, { method: 'POST' }).then(r => r.json());
    _applyVideoResult(r);
}

function _applyVideoResult(r) {
    if (r.video) {
        bbState.current_video = r.video;
        if (r.q2_is) bbState.q2_is = r.q2_is;
        updateNowPlaying(r.video);

        const container = document.getElementById('bb-video-container');
        const noVideo = document.getElementById('bb-no-video');
        if (container && noVideo) {
            noVideo.style.display = 'none';
            loadYouTubeVideo(r.video, container);
        }
        if (r.commentary) addBBComment(r.q2_is || bbState.q2_is, r.commentary);
        startBBCommentary();
    }
}

function loadYouTubeVideo(video, container) {
    // video.video_id is a real YouTube video ID resolved server-side via
    // the YouTube Data API (integrations/beavis_butthead.py's
    // resolve_video_id(), reusing integrations/youtube_music.py's
    // Music-category search) -- NOT a "?listType=search&list=..." embed,
    // which YouTube deprecated for public embeds years ago and no longer
    // reliably returns results. If the lookup failed (no OAuth token
    // configured, quota, etc.), fall back to a real search-results link
    // the user opens themselves, same pattern as MasterChef's
    // get_technique_video()/Radio DJ's playback.
    if (video.video_id) {
        container.innerHTML = `
            <iframe
                src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(video.video_id)}?autoplay=1"
                style="position:absolute;inset:0;width:100%;height:100%;border:none"
                allow="autoplay;encrypted-media"
                allowfullscreen>
            </iframe>
        `;
    } else {
        const url = `https://www.youtube.com/results?search_query=${encodeURIComponent(video.query || '')}`;
        container.innerHTML = `
            <div style="position:absolute;inset:0;display:flex;
                 align-items:center;justify-content:center;
                 background:#000;color:rgba(255,140,0,0.5);
                 font-size:11px;text-align:center;padding:16px">
                <div>
                    Couldn't resolve a video automatically.<br>
                    <a href="${url}" target="_blank" rel="noopener"
                       style="color:#00c8ff">Search YouTube for "${video.artist} -- ${video.title}"</a>
                </div>
            </div>
        `;
    }
}

function updateNowPlaying(video) {
    const el = document.getElementById('bb-now-playing');
    if (el && video) el.textContent = `${video.artist} -- ${video.title}`;
}

let _commentaryInterval = null;

function startBBCommentary() {
    if (_commentaryInterval) clearInterval(_commentaryInterval);
    // Fires every 25-45s (randomized) -- enough presence without being
    // annoying, per the design note.
    _commentaryInterval = setInterval(async () => {
        if (!bbState.current_video) return;
        const r = await fetch(`${BASE}/api/bb/react`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ moment: '' }),
        }).then(r => r.json());
        if (r.commentary) addBBComment(bbState.q2_is, r.commentary);
    }, 25000 + Math.random() * 20000);
}

async function bbNextVideo() {
    if (_commentaryInterval) clearInterval(_commentaryInterval);

    const endR = await fetch(`${BASE}/api/bb/video_end`, { method: 'POST' }).then(r => r.json());
    if (endR.commentary) addBBComment(bbState.q2_is, endR.commentary);

    await new Promise(r => setTimeout(r, 1500));

    // Actually advances the session's current video (unlike calling
    // start_video again, which would just re-announce the same one).
    const r = await fetch(`${BASE}/api/bb/next_video`, { method: 'POST' }).then(r => r.json());
    _applyVideoResult(r);
}

// ── Commentary ──────────────────────────────────────────────────────

function addBBComment(speaker, text, isUser = false) {
    const feed = document.getElementById('bb-commentary');
    if (!feed) return;

    const placeholder = feed.querySelector('div');
    if (placeholder && placeholder.textContent.includes('Session commentary')) placeholder.remove();

    let speakerLabel = (speaker || 'butthead').toUpperCase();
    let cls = (speaker || 'butthead').toLowerCase();
    if (isUser) {
        cls = 'user';
        speakerLabel = (bbState.q2_is === 'butthead' ? 'BEAVIS' : 'BUTTHEAD') + ' (YOU)';
    }

    const div = document.createElement('div');
    div.className = `bb-comment ${cls}`;
    if (bbState.nice_guy && !isUser) {
        div.style.borderLeftColor = '#ffd700';
        div.style.background = 'rgba(255,215,0,0.04)';
    }
    div.innerHTML = `<div class="bb-speaker">${speakerLabel}</div>${text}`;

    feed.appendChild(div);
    feed.scrollTop = feed.scrollHeight;
}

async function bbUserComment() {
    const input = document.getElementById('bb-user-input');
    const text = input?.value.trim();
    if (!text) return;

    addBBComment('user', text, true);
    if (input) input.value = '';

    const r = await fetch(`${BASE}/api/bb/user_comment`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ comment: text }),
    }).then(r => r.json());

    if (r.reaction) {
        setTimeout(() => addBBComment(bbState.q2_is, r.reaction), 500);
    }
}

function bbQuickComment(text) {
    const input = document.getElementById('bb-user-input');
    if (input) {
        input.value = text;
        bbUserComment();
    }
}

// ── Controls ────────────────────────────────────────────────────────

async function bbToggleNiceGuy() {
    const toggle = document.getElementById('bb-nice-toggle');
    const r = await fetch(`${BASE}/api/bb/toggle_nice_guy`, { method: 'POST' }).then(r => r.json());

    bbState.nice_guy = r.nice_guy;
    toggle?.classList.toggle('active', r.nice_guy);
    if (r.commentary) addBBComment(bbState.q2_is, r.commentary);
}

async function bbSwapChars() {
    const r = await fetch(`${BASE}/api/bb/swap_chars`, { method: 'POST' }).then(r => r.json());
    bbState.q2_is = r.q2_is;

    const bhLabel = document.getElementById('bb-butthead-label');
    const bvLabel = document.getElementById('bb-beavis-label');
    const ucLabel = document.getElementById('bb-user-char');

    if (r.q2_is === 'beavis') {
        if (bhLabel) bhLabel.textContent = 'YOU';
        if (bvLabel) bvLabel.textContent = 'Q2';
        if (ucLabel) ucLabel.textContent = 'BUTTHEAD';
    } else {
        if (bhLabel) bhLabel.textContent = 'Q2';
        if (bvLabel) bvLabel.textContent = 'YOU';
        if (ucLabel) ucLabel.textContent = 'BEAVIS';
    }

    addBBComment(r.q2_is, r.commentary || "Uh... okay. I'm different now.");
}

async function bbMarkReplay(allowed) {
    const r = await fetch(`${BASE}/api/bb/replay`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ allowed }),
    }).then(r => r.json());
    if (r.commentary) addBBComment(bbState.q2_is, r.commentary);
}

async function bbShowReplayList() {
    const r = await fetch(`${BASE}/api/bb/replay_list`).then(r => r.json());
    const feed = document.getElementById('bb-commentary');
    if (feed && r.list) {
        const div = document.createElement('div');
        div.style.cssText =
            'padding:8px;background:rgba(255,140,0,0.04);' +
            'border:1px solid rgba(255,140,0,0.15);' +
            'border-radius:4px;font-size:10px;' +
            'color:rgba(255,200,0,0.7);margin-top:4px';
        div.innerHTML = '<div style="font-weight:700;margin-bottom:4px">REPLAY LIST:</div>' +
            r.list.map(v => `${v.artist} -- ${v.title} (${v.play_count}x)`).join('<br>');
        feed.appendChild(div);
        feed.scrollTop = feed.scrollHeight;
    }
}
