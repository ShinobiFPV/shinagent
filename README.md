# ShinAgent

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
