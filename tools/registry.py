"""
IMQ2 Tool Registry
Manages available tools, their permission states, and their JSON Schema
definitions for native LLM tool-use (Claude/OpenAI function calling).
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from config.loader import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base Tool
# ---------------------------------------------------------------------------

class BaseTool(ABC):
    name: str = ""
    description: str = ""
    # JSON Schema for this tool's input, e.g.:
    # {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    input_schema: dict = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    def run(self, **kwargs) -> str:
        """Execute the tool with the given structured arguments. Always returns a string."""
        ...

    def is_granted(self) -> bool:
        return config.get(f"tools.{self.name}.permission", "none") == "granted" \
               and config.get(f"tools.{self.name}.enabled", False)

    def to_claude_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }


# ---------------------------------------------------------------------------
# Tool: Find Product — research-only, no money moves. Searches for a specific
# item on a named retailer, surfaces price/availability. Checkout/purchase
# execution is intentionally a SEPARATE, more tightly-guarded tool, built in
# its own dedicated session given the real stakes of automated purchasing
# with real money. This tool can never spend anything — it only searches.
# ---------------------------------------------------------------------------

class FindProductTool(BaseTool):
    name = "find_product"
    description = (
        "Search for a specific product on a named retailer (e.g. Amazon, "
        "RotorVillage) to find its price and availability. This is research "
        "only — it does NOT purchase anything. Use this when the user asks "
        "Q2 to find or look up an item to potentially buy."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "item_description": {
                "type": "string",
                "description": "What to search for, e.g. '10mm hex driver' or 'Matek H743-Slim V1'.",
            },
            "merchant": {
                "type": "string",
                "description": "Which retailer to search, e.g. 'amazon.ca' or 'rotorvillage.ca'.",
            },
        },
        "required": ["item_description", "merchant"],
    }

    def run(self, item_description: str = "", merchant: str = "") -> str:
        try:
            import os
            import requests

            api_key = os.environ.get("TAVILY_API_KEY", "")
            if not api_key:
                return "[find_product] TAVILY_API_KEY not set."

            query = f"{item_description} site:{merchant}" if "." in merchant else f"{item_description} {merchant}"
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": 3},
                timeout=10,
            )
            r.raise_for_status()
            results = r.json().get("results", [])

            if not results:
                return f"[find_product] No results found for '{item_description}' on {merchant}."

            # Surface results for Q2 to relay to the user — actual price
            # parsing from arbitrary page text is unreliable, so we hand
            # back the raw snippets and let Q2 read/summarize the price
            # rather than us guessing at a regex for "$XX.XX" that breaks
            # the moment a site's markup changes.
            snippets = [f"{res['title']}: {res['content'][:300]} ({res['url']})" for res in results]
            return (
                f"Found {len(results)} result(s) for '{item_description}' on {merchant}:\n"
                + "\n".join(snippets)
                + "\n\nNote: to actually purchase this, the user must explicitly confirm "
                "a specific item and price first — purchasing is not yet available as an "
                "automated capability."
            )
        except Exception as e:
            return f"[find_product] Error: {e}"


# ---------------------------------------------------------------------------
# Tool: Browser Display — opens webpages, YouTube videos, or image searches
# on the connected display via the desktop's default browser.
# ---------------------------------------------------------------------------

class BrowserDisplayTool(BaseTool):
    name = "show_on_display"
    description = (
        "Open a webpage, YouTube video, or image search results on the connected "
        "display so the user can see it. Use this whenever showing something "
        "visually would help more than describing it in words — e.g. the user "
        "asks to see a video, look at pictures of something, or visit a website."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["url", "youtube_search", "youtube_video", "image_search"],
                "description": (
                    "How to interpret 'query': 'url' opens it directly as a webpage; "
                    "'youtube_search' searches YouTube for the query; 'youtube_video' "
                    "treats query as a specific video to find and open; 'image_search' "
                    "opens a Google Images search for the query."
                ),
            },
            "query": {
                "type": "string",
                "description": "The URL, search terms, or video description, depending on mode.",
            },
        },
        "required": ["mode", "query"],
    }

    @staticmethod
    def _quote(text: str) -> str:
        from urllib.parse import quote_plus
        return quote_plus(text)

    def _build_url(self, mode: str, query: str) -> Optional[str]:
        if mode == "url":
            return query if query.startswith("http") else f"https://{query}"
        elif mode in ("youtube_search", "youtube_video"):
            return f"https://www.youtube.com/results?search_query={self._quote(query)}"
        elif mode == "image_search":
            return f"https://www.google.com/search?tbm=isch&q={self._quote(query)}"
        return None

    def run(self, mode: str = "url", query: str = "") -> str:
        try:
            import subprocess

            target_url = self._build_url(mode, query)
            if target_url is None:
                return f"[show_on_display] Unknown mode: {mode}"

            # xdg-open respects the desktop's configured default browser and
            # opens a new tab if the browser is already running, rather than
            # hardcoding a specific browser binary.
            subprocess.Popen(
                ["xdg-open", target_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info(f"Opened on display: {target_url}")
            return f"Opened on the display: {target_url}"
        except FileNotFoundError:
            return "[show_on_display] xdg-open not found — is a desktop environment running?"
        except Exception as e:
            return f"[show_on_display] Error: {e}"


# ---------------------------------------------------------------------------
# Tool: RC Telemetry — read-only status from William's RC crawler (1:10 scale,
# ArduPilot Rover firmware) via the same MAVLink telemetry logic used in
# ShinLink OS (his existing ground station app). This is intentionally
# READ-ONLY: battery, GPS, armed state, mode. No control/drive capability
# here — that's a separate, more carefully-guarded tool, designed and
# reviewed before being built, the same way purchasing's execute step was
# deliberately kept out of the first pass.
# ---------------------------------------------------------------------------

class RCTelemetryTool(BaseTool):
    name = "get_rc_telemetry"
    description = (
        "Get current status from William's RC crawler (battery voltage, GPS, "
        "whether it's armed, current mode). Read-only — does not control the "
        "vehicle in any way. Connects fresh each call and reads the latest "
        "telemetry over a short window."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        try:
            import json
            from pathlib import Path
            from integrations.telemetry_reader import TelemetryReader
            import time

            # Read connection settings from ShinLink OS's own config.json so
            # there's exactly one place these live, rather than duplicating
            # port/baud values that could drift out of sync.
            config_path = Path(config.get("integrations.shinlink_os.config_path", ""))
            if not config_path.exists():
                return (
                    "[get_rc_telemetry] ShinLink OS config.json not found at "
                    f"'{config_path}'. Check integrations.shinlink_os.config_path in config.yaml."
                )

            shinlink_config = json.loads(config_path.read_text())
            port = shinlink_config.get("telem_port", "/dev/ttyUSB0")
            baud = shinlink_config.get("telem_baud", 57600)

            statuses = []
            reader = TelemetryReader()
            reader.start(port, baud, on_status=lambda msg, color: statuses.append(msg), do_log=False)

            # Give it a real window to connect and receive at least one
            # heartbeat + a round of telemetry messages. wait_heartbeat()
            # itself has a 10s internal timeout, so this needs to be at
            # least that long to get a meaningful read rather than an
            # always-empty snapshot.
            timeout_s = config.get("integrations.shinlink_os.telemetry_timeout_s", 12)
            deadline = time.time() + timeout_s
            while time.time() < deadline and not reader.connected:
                time.sleep(0.2)

            if not reader.connected:
                reader.stop()
                last_status = statuses[-1] if statuses else "no response"
                return f"[get_rc_telemetry] Could not connect to crawler ({last_status}). Is it powered on?"

            # Connected — give it a brief moment more to receive a fresh
            # round of telemetry messages before snapshotting.
            time.sleep(1.5)

            snapshot = (
                f"Crawler status: {'ARMED' if reader.armed else 'disarmed'}, "
                f"mode={reader.flight_mode if reader.flight_mode != '?' else 'unknown'}, "
                f"vehicle_type={reader.vehicle_type}. "
                f"Battery: {reader.batt_voltage:.2f}V"
                + (f", {reader.batt_pct}%" if reader.batt_pct >= 0 else "")
                + f". GPS: fix_type={reader.gps_fix}, {reader.satellites} satellites"
                + (f", lat={reader.lat:.6f} lon={reader.lon:.6f}" if reader.gps_fix >= 2 else " (no fix)")
                + f". Ground speed: {reader.groundspeed:.1f} m/s."
            )

            reader.stop()
            return snapshot

        except Exception as e:
            return f"[get_rc_telemetry] Error: {e}"


class OpenSettingsTool(BaseTool):
    name = "open_settings"
    description = (
        "Open or close the Q2 settings panel on the display. Use when the user "
        "says 'open settings', 'show settings', 'close settings', or 'go back to face'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "close"],
                "description": "'open' to show the settings panel, 'close' to return to the face.",
            }
        },
        "required": ["action"],
    }

    def run(self, action: str = "open") -> str:
        try:
            import subprocess
            from config.loader import config
            port = config.get("face.port", 8765)
            if action == "open":
                url = f"http://127.0.0.1:{port}/settings.html"
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "Settings panel opened on the display."
            else:
                url = f"http://127.0.0.1:{port}/"
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "Returned to the face display."
        except Exception as e:
            return f"[open_settings] Error: {e}"


# ---------------------------------------------------------------------------

class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web for current information. Use this for general questions "
        "about recent events, facts you're unsure of, or anything not covered by "
        "a more specific tool (weather, definitions, translation)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."}
        },
        "required": ["query"],
    }

    def run(self, query: str = "") -> str:
        try:
            import os
            import requests
            api_key = os.environ.get("TAVILY_API_KEY", "")
            if not api_key:
                return "[web_search] TAVILY_API_KEY not set."
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": 3},
                timeout=10,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "[web_search] No results found."
            snippets = [f"{r['title']}: {r['content'][:200]}" for r in results[:3]]
            return "\n".join(snippets)
        except Exception as e:
            return f"[web_search] Error: {e}"


# ---------------------------------------------------------------------------
# Tool: Weather (Open-Meteo — free, no API key required)
# ---------------------------------------------------------------------------

class WeatherTool(BaseTool):
    name = "get_weather"
    description = (
        "Get current weather conditions for a location. Provide latitude/longitude "
        "if known, otherwise a place name to geocode first."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City or place name, e.g. 'Toronto' or 'Toronto, Canada'.",
            },
        },
        "required": ["location"],
    }

    # Open-Meteo's WMO weather codes -> short human description
    _WEATHER_CODES = {
        0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "freezing fog",
        51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
        61: "light rain", 63: "moderate rain", 65: "heavy rain",
        71: "light snow", 73: "moderate snow", 75: "heavy snow",
        80: "light rain showers", 81: "moderate rain showers", 82: "violent rain showers",
        95: "thunderstorm", 96: "thunderstorm with light hail", 99: "thunderstorm with heavy hail",
    }

    def run(self, location: str = "") -> str:
        try:
            import requests

            # Step 1: geocode the place name (Open-Meteo's own free geocoding API)
            geo_r = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1},
                timeout=10,
            )
            geo_r.raise_for_status()
            geo_results = geo_r.json().get("results")
            if not geo_results:
                return f"[get_weather] Could not find location: {location}"

            lat = geo_results[0]["latitude"]
            lon = geo_results[0]["longitude"]
            resolved_name = geo_results[0].get("name", location)

            # Step 2: fetch current weather for those coordinates
            weather_r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": True,
                    "temperature_unit": "celsius",
                    "windspeed_unit": "kmh",
                },
                timeout=10,
            )
            weather_r.raise_for_status()
            current = weather_r.json().get("current_weather", {})

            temp = current.get("temperature")
            wind = current.get("windspeed")
            code = current.get("weathercode")
            condition = self._WEATHER_CODES.get(code, f"code {code}")

            return (
                f"Current weather in {resolved_name}: {temp}°C, {condition}, "
                f"wind {wind} km/h."
            )
        except Exception as e:
            return f"[get_weather] Error: {e}"


# ---------------------------------------------------------------------------
# Tool: Dictionary / Definition (Free Dictionary API — no key required)
# ---------------------------------------------------------------------------

class DefinitionTool(BaseTool):
    name = "define_word"
    description = "Look up the definition of an English word."
    input_schema = {
        "type": "object",
        "properties": {
            "word": {"type": "string", "description": "The word to define."}
        },
        "required": ["word"],
    }

    def run(self, word: str = "") -> str:
        try:
            import requests
            r = requests.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}",
                timeout=10,
            )
            if r.status_code == 404:
                return f"[define_word] No definition found for '{word}'."
            r.raise_for_status()
            data = r.json()[0]
            meanings = data.get("meanings", [])
            if not meanings:
                return f"[define_word] No definition found for '{word}'."

            parts = []
            for m in meanings[:2]:  # cap at 2 parts of speech to keep it concise
                pos = m.get("partOfSpeech", "")
                defs = m.get("definitions", [])
                if defs:
                    parts.append(f"({pos}) {defs[0].get('definition', '')}")
            return f"{word}: " + "; ".join(parts)
        except Exception as e:
            return f"[define_word] Error: {e}"


# ---------------------------------------------------------------------------
# Tool: Translation (via LibreTranslate-compatible endpoint, or fallback to
# Claude's own native multilingual ability — see note in run())
# ---------------------------------------------------------------------------

class TranslateTool(BaseTool):
    name = "translate_text"
    description = (
        "Translate text from one language to another. Useful for short phrases, "
        "signs, or quick translations the user asks about."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to translate."},
            "target_language": {
                "type": "string",
                "description": "Target language name or ISO code, e.g. 'French' or 'fr'.",
            },
            "source_language": {
                "type": "string",
                "description": "Source language, if known. Omit to auto-detect.",
            },
        },
        "required": ["text", "target_language"],
    }

    def run(self, text: str = "", target_language: str = "", source_language: str = "") -> str:
        # Note: Claude itself is genuinely strong at translation natively —
        # this tool exists mainly for cases requiring a verified/structured
        # round-trip (e.g. a dedicated service) rather than because Claude
        # can't translate unaided. For now this is a thin wrapper that lets
        # the tool-use loop be consistent; Claude may often just answer
        # translation questions directly without invoking this tool at all,
        # which is fine and expected.
        return (
            "[translate_text] No translation service configured — Claude should "
            "answer this directly using its own language knowledge rather than "
            "relying on this tool."
        )


# ---------------------------------------------------------------------------
# Tool: Send Email (future)
# ---------------------------------------------------------------------------

class SendEmailTool(BaseTool):
    name = "send_email"
    description = (
        "Send an email from Q2's Gmail account (iamkewtoo@gmail.com) via OAuth. "
        "Use for order confirmations, notifications, forwarding messages, or sending "
        "files and photos to William. Supports file attachments by local path."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to":      {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Email subject line."},
            "body":    {"type": "string", "description": "Email body text (plain text)."},
            "attachments": {
                "type":  "array",
                "items": {"type": "string"},
                "description": "Optional list of local file paths to attach to the email.",
            },
        },
        "required": ["to", "subject", "body"],
    }

    def run(self, to: str = "", subject: str = "", body: str = "",
            attachments: list = None) -> str:
        if not to or not subject:
            return "[send_email] 'to' and 'subject' are required."
        try:
            from integrations.gmail_oauth import send_email
            return send_email(to=to, subject=subject, body=body,
                              attachments=attachments or [])
        except Exception as e:
            return f"[send_email] Error: {e}"


class ExecutePurchaseTool(BaseTool):
    name = "execute_purchase"
    description = (
        "Complete an actual purchase on rotorvillage.ca using Q2's account "
        "and a gift card from the budget ledger. ONLY call this tool after: "
        "(1) find_product has already confirmed the item exists and the user "
        "has seen the price, AND (2) the user has explicitly said to go ahead "
        "and buy it. This tool will ask for confirmation twice before placing "
        "any order — once if shipping exceeds the threshold, and once with a "
        "full order summary. It never places an order without explicit approval."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "product_url": {
                "type": "string",
                "description": "Full URL of the specific product page on rotorvillage.ca.",
            },
            "item_description": {
                "type": "string",
                "description": "Human-readable item name for the ledger and confirmation prompt.",
            },
        },
        "required": ["product_url", "item_description"],
    }

    def run(self, product_url: str = "", item_description: str = "") -> str:
        supported = {
            "rotorvillage.ca": "purchasing.checkout_rotorvillage",
            "amazon.ca":       "purchasing.checkout_amazon",
        }
        merchant = next((k for k in supported if k in product_url), None)
        if not merchant:
            return (
                f"[execute_purchase] Unsupported retailer. Supported: "
                + ", ".join(supported.keys())
            )

        try:
            from purchasing.ledger import BudgetLedger
            import importlib

            ledger  = BudgetLedger()
            balance = ledger.total_available_balance()
            if balance <= 0:
                ledger.close()
                return "[execute_purchase] No gift card balance available."

            def confirm(summary: str) -> bool:
                log.info(f"execute_purchase: requesting confirmation:\n{summary}")
                print(f"\n{'='*50}\n{summary}\n{'='*50}")
                try:
                    from face.server import face_state
                    face_state.set_listening(True)
                except ImportError:
                    pass
                response = input("Type YES to confirm, anything else to cancel: ").strip().lower()
                try:
                    from face.server import face_state
                    face_state.set_listening(False)
                except ImportError:
                    pass
                return response in ("yes", "y")

            mod = importlib.import_module(supported[merchant])

            if merchant == "rotorvillage.ca":
                result = mod.execute_purchase_sync(
                    product_url=product_url, item_description=item_description,
                    confirm_callback_sync=confirm, config=config, ledger=ledger,
                )
            else:  # amazon.ca
                result = mod.execute_amazon_purchase_sync(
                    product_url=product_url, item_description=item_description,
                    confirm_callback_sync=confirm, config=config, ledger=ledger,
                )

            ledger.close()

            if result["ok"]:
                order_id = result.get("order_id") or "unknown"
                return f"Order placed. Order number: {order_id}. {result.get('message', '')}"
            else:
                return f"Purchase not completed: {result.get('message', 'unknown error')}"

        except Exception as e:
            log.error(f"execute_purchase tool error: {e}", exc_info=True)
            return f"[execute_purchase] Error: {e}"


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tool: Read Email — check Q2's Gmail inbox for shipping/order notifications,
# and forward messages to William's personal email on request.
# ---------------------------------------------------------------------------

class ReadEmailTool(BaseTool):
    name = "read_email"
    description = (
        "Check Q2's Gmail inbox (iamkewtoo@gmail.com) for new unread messages, "
        "particularly shipping notifications and order confirmations. Use when the "
        "user asks about orders, packages, or their email. Can also forward messages "
        "to William's personal email when asked."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check_inbox", "forward"],
                "description": "'check_inbox' reads recent unread messages; 'forward' forwards a specific one.",
            },
            "forward_to": {
                "type": "string",
                "description": "Email address to forward to (required when action='forward').",
            },
            "message_id": {
                "type": "string",
                "description": "Gmail message ID to forward (from a prior check_inbox result).",
            },
            "max_messages": {
                "type": "integer",
                "description": "Max unread messages to return for check_inbox (default: 5).",
            },
        },
        "required": ["action"],
    }

    def run(self, action: str = "check_inbox", forward_to: str = "",
            message_id: str = "", max_messages: int = 5) -> str:
        try:
            from integrations.gmail_oauth import check_inbox, forward_message

            if action == "check_inbox":
                messages = check_inbox(max_messages=max_messages)
                if not messages:
                    return "No unread messages in Q2's inbox."
                summaries = []
                for m in messages:
                    summaries.append(
                        f"ID {m['id']}\nFrom: {m['sender']}\nSubject: {m['subject']}\n"
                        f"Date: {m['date']}\n{m['body_preview'].strip()}"
                    )
                return f"{len(messages)} unread message(s):\n\n" + "\n\n---\n\n".join(summaries)

            elif action == "forward":
                if not forward_to or not message_id:
                    return "[read_email] 'forward_to' and 'message_id' are required for forwarding."
                return forward_message(message_id=message_id, to=forward_to)

            else:
                return f"[read_email] Unknown action: {action}"

        except Exception as e:
            return f"[read_email] Error: {e}"



class BodyControlTool(BaseTool):
    name = "body_control"
    description = "Control Q2's physical body/chassis."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Movement or action command."}
        },
        "required": ["action"],
    }

    def run(self, action: str = "") -> str:
        # Stub — implement when embodiment hardware is ready
        return "[body_control] No body connected."


class YouTubeMusicTool(BaseTool):
    name = "youtube_music"
    description = (
        "Create YouTube Music playlists and search for tracks. "
        "Use when William asks to make a playlist, find songs, or get a music link. "
        "The playlist URL works in both YouTube and YouTube Music."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create_playlist", "search_tracks"],
                "description": "'create_playlist' builds and fills a playlist; 'search_tracks' searches without creating.",
            },
            "title":   {"type": "string", "description": "Playlist title (for create_playlist)."},
            "queries": {
                "type": "array", "items": {"type": "string"},
                "description": "Search queries to find tracks. For create_playlist use multiple specific queries.",
            },
            "tracks_per_query": {"type": "integer", "description": "Tracks to add per query (default 3)."},
            "description": {"type": "string", "description": "Playlist description (optional)."},
        },
        "required": ["action"],
    }

    def run(self, action: str = "search_tracks", title: str = "",
            queries: list = None, tracks_per_query: int = 3,
            description: str = "") -> str:
        queries = queries or []
        try:
            if action == "search_tracks":
                from integrations.youtube_music import search_tracks
                q = " ".join(queries) if queries else title
                results = search_tracks(q, max_results=8)
                if not results:
                    return "No tracks found."
                lines = [f"{i+1}. {t['title']} — {t['channel']}\n   {t['url']}"
                         for i, t in enumerate(results)]
                return "\n".join(lines)

            elif action == "create_playlist":
                if not title:
                    return "[youtube_music] Playlist title is required."
                if not queries:
                    queries = [title]
                from integrations.youtube_music import create_music_playlist
                result = create_music_playlist(
                    title=title, search_queries=queries,
                    tracks_per_query=tracks_per_query, description=description,
                )
                if not result.get("ok"):
                    return f"[youtube_music] {result.get('error', 'Unknown error')}"
                track_list = "\n".join(
                    f"  {i+1}. {t['title']}" for i, t in enumerate(result["tracks"])
                )
                return (
                    f"Playlist '{result['title']}' created with {result['count']} tracks:\n"
                    f"{track_list}\n\nLink: {result['url']}"
                )
            else:
                return f"[youtube_music] Unknown action: {action}"
        except Exception as e:
            return f"[youtube_music] Error: {e}"


class GoogleDriveTool(BaseTool):
    name = "google_drive"
    description = (
        "Upload files to Q2's Google Drive and get shareable links. "
        "Use to save documents, exports, or any file William wants stored in Drive."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["upload_file", "upload_text", "list_files", "create_folder"],
            },
            "local_path": {"type": "string", "description": "Path to local file (for upload_file)."},
            "content":    {"type": "string", "description": "Text content to upload (for upload_text)."},
            "filename":   {"type": "string", "description": "Filename for the uploaded file."},
            "folder_name":{"type": "string", "description": "Folder name to create or list."},
        },
        "required": ["action"],
    }

    def run(self, action: str = "list_files", local_path: str = "",
            content: str = "", filename: str = "", folder_name: str = "") -> str:
        try:
            if action == "list_files":
                from integrations.google_drive import list_files
                files = list_files()
                if not files:
                    return "Drive is empty."
                return "\n".join(f"• {f['name']} — {f.get('webViewLink','')}" for f in files)

            elif action == "upload_file":
                if not local_path:
                    return "[google_drive] local_path is required."
                from integrations.google_drive import upload_file
                result = upload_file(local_path, filename=filename or None)
                return f"Uploaded '{result['name']}' to Drive: {result['url']}"

            elif action == "upload_text":
                if not content or not filename:
                    return "[google_drive] content and filename are required."
                from integrations.google_drive import upload_text
                result = upload_text(content, filename=filename)
                return f"Saved '{result['name']}' to Drive: {result['url']}"

            elif action == "create_folder":
                if not folder_name:
                    return "[google_drive] folder_name is required."
                from integrations.google_drive import create_folder
                result = create_folder(folder_name)
                return f"Folder '{result['name']}' created in Drive."

            else:
                return f"[google_drive] Unknown action: {action}"
        except Exception as e:
            return f"[google_drive] Error: {e}"


class GoogleSheetsTool(BaseTool):
    name = "google_sheets"
    description = (
        "Create Google Sheets spreadsheets and write data to them. "
        "Use to export purchase history, track data, or create any tabular output for William."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create_and_fill", "append_rows", "read_rows"],
            },
            "title":   {"type": "string", "description": "Spreadsheet title."},
            "headers": {"type": "array", "items": {"type": "string"}, "description": "Column headers."},
            "rows":    {"type": "array", "items": {"type": "array"}, "description": "Data rows (list of lists). REQUIRED for append_rows — each row is a list of values e.g. [['2026-06-24', 277, 24313, 161, 24474]]."},
            "spreadsheet_id": {"type": "string", "description": "Existing spreadsheet ID (for append/read)."},
            "range_name":     {"type": "string", "description": "Range to read e.g. 'Sheet1' or 'Sheet1!A1:D10'."},
        },
        "required": ["action"],
    }

    def run(self, action: str = "create_and_fill", title: str = "",
            headers: list = None, rows: list = None,
            spreadsheet_id: str = "", range_name: str = "Sheet1") -> str:
        try:
            if action == "create_and_fill":
                if not title:
                    return "[google_sheets] title is required."
                from integrations.google_sheets import create_and_fill
                result = create_and_fill(
                    title=title,
                    headers=headers or [],
                    rows=rows or [],
                )
                # Store the real ID as a fact so future references use the correct URL
                try:
                    from memory.manager import MemoryManager
                    m = MemoryManager()
                    safe_key = title.lower().replace(" ", "_").replace("-", "_")[:40]
                    m.store_fact(
                        subject=f"sheet_id_{safe_key}",
                        content=f"Google Sheet '{result['title']}' ID: {result['id']} URL: {result['url']}",
                        category="project",
                    )
                    m.close()
                except Exception:
                    pass
                return f"Spreadsheet '{result['title']}' created: {result['url']} (ID: {result['id']})"

            elif action == "append_rows":
                if not spreadsheet_id or not rows:
                    return "[google_sheets] spreadsheet_id and rows are required."
                from integrations.google_sheets import append_rows
                count = append_rows(spreadsheet_id, rows)
                return f"Appended {count} rows."

            elif action == "read_rows":
                if not spreadsheet_id:
                    return "[google_sheets] spreadsheet_id is required."
                from integrations.google_sheets import read_rows
                data = read_rows(spreadsheet_id, range_name)
                if not data:
                    return "No data found."
                return "\n".join(["\t".join(row) for row in data[:20]])

            else:
                return f"[google_sheets] Unknown action: {action}"
        except Exception as e:
            return f"[google_sheets] Error: {e}"


class GoogleDocsTool(BaseTool):
    name = "google_docs"
    description = (
        "Create Google Docs documents and write content to them. "
        "Use to write reports, notes, summaries, or any long-form content for William."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create_document", "append_to_document", "read_document"],
            },
            "title":   {"type": "string", "description": "Document title."},
            "content": {"type": "string", "description": "Text content to write."},
            "doc_id":  {"type": "string", "description": "Existing document ID (for append/read)."},
        },
        "required": ["action"],
    }

    def run(self, action: str = "create_document", title: str = "",
            content: str = "", doc_id: str = "") -> str:
        try:
            if action == "create_document":
                if not title:
                    return "[google_docs] title is required."
                from integrations.google_docs import create_document
                result = create_document(title=title, content=content)
                # Store the real ID as a fact so future references use the correct URL
                try:
                    from memory.manager import MemoryManager
                    m = MemoryManager()
                    safe_key = title.lower().replace(" ", "_").replace("-", "_")[:40]
                    m.store_fact(
                        subject=f"doc_id_{safe_key}",
                        content=f"Google Doc '{result['title']}' ID: {result['id']} URL: {result['url']}",
                        category="project",
                    )
                    m.close()
                except Exception:
                    pass
                return f"Document '{result['title']}' created: {result['url']} (ID: {result['id']})"

            elif action == "append_to_document":
                if not doc_id or not content:
                    return "[google_docs] doc_id and content are required."
                from integrations.google_docs import append_to_document
                ok = append_to_document(doc_id, content)
                return "Content appended." if ok else "Failed to append content."

            elif action == "read_document":
                if not doc_id:
                    return "[google_docs] doc_id is required."
                from integrations.google_docs import get_document_text
                text = get_document_text(doc_id)
                return text[:2000] if text else "(empty document)"

            else:
                return f"[google_docs] Unknown action: {action}"
        except Exception as e:
            return f"[google_docs] Error: {e}"


class GoogleCalendarTool(BaseTool):
    name = "google_calendar"
    description = (
        "Check William's Google Calendar for upcoming events and create new ones. "
        "Use when he asks what's coming up, wants to schedule something, or asks about his week."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check_upcoming", "create_event"],
            },
            "days":     {"type": "integer", "description": "Days ahead to look (for check_upcoming, default 7)."},
            "title":    {"type": "string", "description": "Event title (for create_event)."},
            "start_dt": {"type": "string", "description": "ISO 8601 start datetime e.g. '2026-07-01T14:00:00-04:00'."},
            "end_dt":   {"type": "string", "description": "ISO 8601 end datetime (optional)."},
            "duration_minutes": {"type": "integer", "description": "Duration in minutes if no end_dt (default 60)."},
            "description": {"type": "string", "description": "Event description (optional)."},
            "location":    {"type": "string", "description": "Event location (optional)."},
        },
        "required": ["action"],
    }

    def run(self, action: str = "check_upcoming", days: int = 7,
            title: str = "", start_dt: str = "", end_dt: str = "",
            duration_minutes: int = 60, description: str = "",
            location: str = "") -> str:
        try:
            if action == "check_upcoming":
                from integrations.google_calendar import get_upcoming_events, format_events_summary
                events = get_upcoming_events(days=days)
                return format_events_summary(events)

            elif action == "create_event":
                if not title or not start_dt:
                    return "[google_calendar] title and start_dt are required."
                from integrations.google_calendar import create_event
                result = create_event(
                    title=title, start_dt=start_dt,
                    end_dt=end_dt or None,
                    duration_minutes=duration_minutes,
                    description=description, location=location,
                )
                return f"Event '{result['title']}' created: {result['url']}"

            else:
                return f"[google_calendar] Unknown action: {action}"
        except Exception as e:
            return f"[google_calendar] Error: {e}"


class CaptureImageTool(BaseTool):
    name = "capture_image"
    description = (
        "Capture a photo from Q2's webcam (Logitech C920) and analyse it using "
        "Claude Vision. Always saves the photo to /tmp/q2_latest.jpg automatically. "
        "When emailing the photo, use attachments=['/tmp/q2_latest.jpg'] in send_email — "
        "no separate save step needed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What to look for or analyse in the image.",
            },
        },
        "required": ["prompt"],
    }

    SAVE_PATH = "/tmp/q2_latest.jpg"

    def run(self, prompt: str = "Describe what you see in this image.") -> str:
        try:
            import base64
            import anthropic
            import time
            from pathlib import Path as _Path
            from integrations.webcam import webcam

            if not webcam.is_running:
                started = webcam.start()
                if not started:
                    return "[capture_image] Could not open webcam. Is the C920 plugged in?"
                time.sleep(0.8)  # let capture thread get first frame

            jpeg = webcam.grab_frame_for_vision()
            if jpeg is None:
                time.sleep(1.0)
                jpeg = webcam.grab_frame_for_vision()
            if jpeg is None:
                return "[capture_image] No frame available — try again in a moment."

            # Always save to disk immediately — before Vision call
            _Path(self.SAVE_PATH).write_bytes(jpeg)
            log.info(f"capture_image: saved {len(jpeg)} bytes to {self.SAVE_PATH}")

            b64 = base64.standard_b64encode(jpeg).decode("utf-8")
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type":       "base64",
                                "media_type": "image/jpeg",
                                "data":       b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            return resp.content[0].text + f"\n\n[Photo saved to {self.SAVE_PATH}]"

        except Exception as e:
            log.error(f"capture_image error: {e}", exc_info=True)
            return f"[capture_image] Error: {e}"


# ---------------------------------------------------------------------------
# Photo tools — imported from tools/photo_tools.py
# ---------------------------------------------------------------------------

class CaptureImageTool2(BaseTool):
    name = "capture_image"
    description = (
        "Capture a photo from Q2's C920 webcam, save it to photos/captures/ with a "
        "timestamp, and analyze it with Claude Vision. Use when William asks Q2 to "
        "take a photo, look at something, or capture an image. Always saves the photo "
        "permanently — not just to /tmp."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to look for or analyze in the image."},
        },
        "required": [],
    }
    def run(self, prompt: str = "Describe what you see.") -> str:
        from tools.photo_tools import capture_image
        return capture_image(prompt=prompt)


class ShowPhotoTool(BaseTool):
    name = "show_photo"
    description = (
        "Display a saved photo full-screen on the kiosk display. "
        "If no path given, shows the most recent capture. "
        "Use when William asks to see a photo or display an image on screen."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path or filename of photo to display. Omit to show most recent capture."},
        },
        "required": [],
    }
    def run(self, path: str = "") -> str:
        from tools.photo_tools import show_photo
        return show_photo(path=path)


class AnalyzePhotoTool(BaseTool):
    name = "analyze_photo"
    description = (
        "Analyze a photo from the incoming folder using Claude Vision, then move it to processed/. "
        "If no path given, lists photos waiting in ~/imq2/photos/incoming/. "
        "William can drop photos into that folder for Q2 to analyze on demand."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Filename or path of photo to analyze. Omit to list incoming photos."},
            "prompt": {"type": "string", "description": "What to look for or analyze in the photo."},
        },
        "required": [],
    }
    def run(self, path: str = "", prompt: str = "Describe this image in detail.") -> str:
        from tools.photo_tools import analyze_photo
        return analyze_photo(path=path, prompt=prompt)


class GetTokenStatsTool(BaseTool):
    name = "get_token_stats"
    description = (
        "Return token usage statistics from Q2's conversation database. "
        "Use when William asks about token usage, API costs, or wants to log stats to a sheet. "
        "Optionally filter to the last N days with since_days."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "description": "Only count tokens from the last N days. Omit for all-time stats.",
            },
            "append_to_sheet": {
                "type": "string",
                "description": "Spreadsheet ID to append today's stats row to. Use this instead of calling google_sheets separately.",
            },
        },
        "required": [],
    }

    def run(self, since_days: int = None, append_to_sheet: str = "") -> str:
        try:
            import datetime
            from config.loader import config as _cfg
            from memory.manager import MemoryManager
            m = MemoryManager()
            s = m.token_stats(since_days=since_days)
            m.close()
            backend = _cfg.get("llm.backend", "unknown")
            model   = _cfg.get(f"llm.{backend}.model", "unknown")
            period  = f"last {since_days} days" if since_days else "all time"
            today   = datetime.date.today().isoformat()
            PRICING = {
                "claude-sonnet-4-6":         (3.00, 15.00),
                "claude-opus-4-6":           (5.00, 25.00),
                "claude-opus-4-7":           (5.00, 25.00),
                "claude-opus-4-8":           (5.00, 25.00),
                "claude-haiku-4-5-20251001": (1.00,  5.00),
                "gpt-4o":                    (2.50, 10.00),
                "gpt-4o-mini":               (0.15,  0.60),
                "gpt-4.1":                   (2.00,  8.00),
                "grok-3-mini":               (0.30,  0.50),
                "grok-3":                    (3.00, 15.00),
                "glm-5.2":                   (1.39,  1.39),
                "glm-5.1":                   (1.00,  1.00),
                "glm-4.7":                   (0.60,  0.60),
            }
            price = PRICING.get(model)
            if price:
                cost = (s["prompt_tokens"] / 1_000_000 * price[0] +
                        s["completion_tokens"] / 1_000_000 * price[1])
                cost_str = f" Est. cost: ${cost:.4f} USD."
            else:
                cost = None
                cost_str = " (no pricing data for this model)"
            summary = (
                f"Token usage ({period}) as of {today} [{backend}/{model}]: "
                f"{s['turns']} turns, "
                f"{s['prompt_tokens']:,} prompt tokens, "
                f"{s['completion_tokens']:,} completion tokens, "
                f"{s['total_tokens']:,} total tokens."
                f"{cost_str}"
            )
            if append_to_sheet:
                try:
                    from integrations.google_sheets import append_rows
                    cost_val = round(cost, 4) if cost is not None else ""
                    row = [[today, f"{backend}/{model}", s['turns'], s['prompt_tokens'], s['completion_tokens'], s['total_tokens'], cost_val]]
                    append_rows(append_to_sheet, row)
                    summary += " Row appended to sheet."
                except Exception as e:
                    summary += f" (Sheet append failed: {e})"
            return summary
        except Exception as e:
            return f"[get_token_stats] Error: {e}"


class GitTool(BaseTool):
    name = "git_push"
    description = (
        "Stage all changes in the imq2 repo, commit with a message, and push to GitHub. "
        "Use when William asks Q2 to save changes, push to GitHub, or commit work. "
        "Also supports checking git status and viewing recent commits."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "push", "log"], "description": "'status' shows what changed, 'push' commits and pushes, 'log' shows recent commits."},
            "commit_message": {"type": "string", "description": "Commit message (required for push)."},
            "push": {"type": "boolean", "description": "Whether to push after committing (default true)."},
            "repo": {"type": "string", "enum": ["imq2", "shinlink"], "description": "Which repo: 'imq2' (default, ~/imq2) or 'shinlink' (~/shinlink-os)."},
        },
        "required": ["action"],
    }
    def run(self, action: str = "status", commit_message: str = "", push: bool = True, repo: str = "imq2") -> str:
        from pathlib import Path
        repo_path = Path("/home/shinobi/shinlink-os") if repo == "shinlink" else None
        if action == "status":
            from tools.git_tools import git_status
            return git_status(repo=repo_path)
        elif action == "push":
            from tools.git_tools import git_push
            return git_push(commit_message=commit_message, push=push, repo=repo_path)
        elif action == "log":
            from tools.git_tools import git_log
            return git_log(repo=repo_path)
        else:
            return f"[git_push] Unknown action: {action}"





class RaceEngineerTool(BaseTool):
    name = "get_race_telemetry"
    description = (
        "Get live Forza telemetry data. Use during races to check speed, fuel, "
        "tyre temperatures, lap times, and race position. "
        "fields can be: summary (default), tyres, engine, dynamics, or all."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {
                "type": "string",
                "enum": ["summary", "tyres", "engine", "dynamics", "all"],
                "description": "Which telemetry fields to return.",
            },
        },
        "required": [],
    }
    def run(self, fields: str = "summary") -> str:
        from tools.race_engineer import get_race_telemetry
        return get_race_telemetry(fields=fields)


class RaceEngineerStatusTool(BaseTool):
    name = "race_engineer_status"
    description = (
        "Get a concise race engineer status brief - fuel, tyre alerts, lap time delta. "
        "Use for proactive callouts during a race. Short, spoken-word format."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.race_engineer import race_engineer_status
        return race_engineer_status()


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._register_defaults()

    def _register_defaults(self):
        for tool_cls in [WebSearchTool, WeatherTool, DefinitionTool, TranslateTool, BrowserDisplayTool, FindProductTool, OpenSettingsTool, RCTelemetryTool, GetTokenStatsTool, SendEmailTool, ReadEmailTool, ExecutePurchaseTool, BodyControlTool, YouTubeMusicTool, GoogleDriveTool, GoogleSheetsTool, GoogleDocsTool, GoogleCalendarTool, CaptureImageTool2, ShowPhotoTool, AnalyzePhotoTool, GitTool, RaceEngineerTool, RaceEngineerStatusTool]:
            tool = tool_cls()
            self._tools[tool.name] = tool
            status = "granted" if tool.is_granted() else "locked"
            log.info(f"Tool registered: {tool.name} [{status}]")

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def grant(self, tool_name: str):
        """Grant permission to a tool at runtime."""
        if tool_name in self._tools:
            config.raw.setdefault("tools", {}).setdefault(tool_name, {})["permission"] = "granted"
            config.raw["tools"][tool_name]["enabled"] = True
            log.info(f"Tool granted at runtime: {tool_name}")

    def revoke(self, tool_name: str):
        if tool_name in self._tools:
            config.raw.setdefault("tools", {}).setdefault(tool_name, {})["permission"] = "none"
            log.info(f"Tool revoked: {tool_name}")

    def get_granted_schemas(self, backend_format: str = "claude") -> list[dict]:
        """
        Return JSON Schema definitions for all currently-granted tools, in the
        format the active LLM backend expects. Pass to LLMBackend.complete(tools=...).
        """
        schemas = []
        for tool in self._tools.values():
            if not tool.is_granted():
                continue
            if backend_format == "openai":
                schemas.append(tool.to_openai_schema())
            else:
                schemas.append(tool.to_claude_schema())
        return schemas

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """Run a tool by name with the given structured input. Used by the agent's tool-use loop."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return f"[error] Unknown tool: {tool_name}"
        if not tool.is_granted():
            return f"[error] Tool '{tool_name}' is not currently granted."
        try:
            return tool.run(**tool_input)
        except Exception as e:
            log.warning(f"Tool '{tool_name}' raised an exception: {e}")
            return f"[error] Tool '{tool_name}' failed: {e}"

    def list_tools(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "granted": t.is_granted()}
            for t in self._tools.values()
        ]
