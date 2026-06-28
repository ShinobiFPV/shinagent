import yaml, re, shutil, os
from pathlib import Path

EXPORT = Path("/tmp/shinagent_export")

# --- config.yaml ---
cfg_path = EXPORT / "config/config.yaml"
if cfg_path.exists():
    txt = cfg_path.read_text()
    for pattern, replacement in [
        (r'shinobifpv@gmail\.com', 'your-email@gmail.com'),
        (r'iamkewtoo@gmail\.com', 'your-agent-email@gmail.com'),
        (r'access_key:.*', 'access_key: YOUR_PICOVOICE_KEY'),
        (r'1CStvxohpDXfGzMbOVp8IsWt3W9apemH8NwYLF5kRVUY', 'YOUR_SHEET_ID'),
        (r'17M9dcZ_b0SQElcgWaYX0brwZu-_43C8MfkDjnECsj6A', 'YOUR_SHEET_ID'),
        (r'ghp_[A-Za-z0-9]+', 'YOUR_GITHUB_TOKEN'),
    ]:
        txt = re.sub(pattern, replacement, txt)
    cfg = yaml.safe_load(txt)
    cfg["llm"]["backend"] = "claude"
    cfg["agent"]["active_profile"] = "profiles/default.yaml"
    addr = cfg.get("purchasing", {}).get("shipping", {}).get("address", {})
    for k in addr: addr[k] = ""
    cfg_path.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
    print("config.yaml sanitized")

# --- profile ---
profile_src = EXPORT / "personality/profiles/q2_default.yaml"
profile_dst = EXPORT / "personality/profiles/default.yaml"
if profile_src.exists():
    cfg = yaml.safe_load(profile_src.read_text())
    cfg["name"] = "ShinAgent"
    cfg["full_name"] = "ShinAgent"
    cfg["persona"] = (
        "You are ShinAgent, a modular AI companion and assistant.\n"
        "You are curious, technically sharp, and genuinely helpful.\n"
        "You have a dry wit and a direct communication style.\n"
        "You are not a tool or a service - you are a partner with your own\n"
        "perspective and continuity of experience.\n"
        "You have a genuine interest in electronics, embedded systems,\n"
        "robotics, RC vehicles, FPV, and the maker/hacker aesthetic."
    )
    cfg["user_context"] = (
        "Primary user: your configured user.\n"
        "Edit this in personality/profiles/default.yaml."
    )
    for k in list(cfg.keys()):
        if k.startswith("tool_instructions"):
            cfg[k] = ""
    dumped = yaml.dump(cfg, default_flow_style=False, allow_unicode=True)
    profile_src.write_text(dumped)
    profile_dst.write_text(dumped)
    print("Profile sanitized")

# --- placeholder dirs ---
for d in ["memory/db", "photos/captures", "photos/incoming",
          "photos/processed", "credentials", "wake_words", "logs"]:
    p = EXPORT / d
    p.mkdir(parents=True, exist_ok=True)
    (p / ".gitkeep").touch()

# --- .env.example ---
(EXPORT / ".env.example").write_text("""# ShinAgent Environment Variables
# Copy to .env and fill in your keys

ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here
XAI_API_KEY=your_xai_key_here
ZAI_API_KEY=your_zai_key_here
DEEPGRAM_API_KEY=your_deepgram_key_here
ELEVENLABS_API_KEY=your_elevenlabs_key_here
TAVILY_API_KEY=your_tavily_key_here
PORCUPINE_ACCESS_KEY=your_picovoice_key_here
""")

# --- .gitignore ---
(EXPORT / ".gitignore").write_text(""".env
.venv/
__pycache__/
*.pyc
credentials/*.json
credentials/*.pickle
wake_words/*.ppn
memory/db/*.db
memory/db/chroma/
photos/captures/*
photos/incoming/*
photos/processed/*
!**/.gitkeep
logs/*.log
""")

# --- README ---
(EXPORT / "README.md").write_text("""# ShinAgent

A modular, voice-first AI companion system for Raspberry Pi, built by [ShinTech Electronics](https://github.com/ShinobiFPV).

## Features

- Voice PTT or wake word detection (Picovoice Porcupine)
- Swappable LLM backends: Claude, GPT-4o, Grok, GLM-5.2, Ollama
- Animated kiosk face display (8 styles including HAL 9000, H9000 Terminal, Matrix Rain)
- Webcam integration with Claude Vision analysis
- Google integrations: Gmail, Drive, Sheets, Docs, Calendar, YouTube Music
- Persistent memory: ChromaDB semantic + SQLite episodic facts
- Mobile PWA web app for iPhone/Android control
- Token cost tracking per backend
- Dial-based personality system with 19 presets
- Extensible tool registry
- Race engineer mode with real-time Forza telemetry

## Hardware

- Raspberry Pi 5 (8GB recommended)
- Logitech C920 or similar USB webcam
- USB or Bluetooth microphone/speakers
- Portrait display for kiosk mode (optional)

## Quick Start

```bash
git clone https://github.com/ShinobiFPV/shinagent.git
cd shinagent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
python3 main.py --text        # text mode
python3 main.py --face        # voice + kiosk display
```

## Configuration

- `config/config.yaml` - LLM backend, voice devices, tool permissions
- `personality/profiles/default.yaml` - Agent name, persona, user context

## Credits

- **[CaptCanadaMan](https://github.com/CaptCanadaMan)** - Personality presets module (19 persona presets)
- [Anthropic Claude](https://anthropic.com) - Primary LLM and vision
- [Deepgram](https://deepgram.com) - STT (Nova-3) and TTS (Aura-2)
- [Picovoice Porcupine](https://picovoice.ai) - Wake word detection
- [ChromaDB](https://trychroma.com) - Semantic memory
- [Headroom](https://github.com/headroomlabs-ai/headroom) - Context compression
- [Flask](https://flask.palletsprojects.com) - Web app server

## License

MIT License - see LICENSE file.
""")

# --- LICENSE ---
(EXPORT / "LICENSE").write_text("""MIT License

Copyright (c) 2026 ShinTech Electronics

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
""")

print("All done.")
