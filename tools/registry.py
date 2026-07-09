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
# Tools: RC Telemetry / Network Status — read-only status from the user's RC
# crawler (1:10 scale, ArduPilot Rover firmware), read via ShinLink OS's
# agent bridge (shinlink-os/ground/agent_bridge.py) rather than opening a
# second, independent MAVLink connection of our own. ShinLink OS's ground
# station app already owns the telemetry serial port; a prior version of
# this tool opened its own mavutil connection to the same port (via
# integrations/telemetry_reader.py, now deprecated), which risked two
# processes contending for one serial device whenever the ground station
# app was also running. Now there's exactly one owner.
#
# These are intentionally READ-ONLY: battery, GPS, armed state, mode, link
# quality. No control/drive/arm capability here — arming, mode changes, and
# any RC channel override are deliberately deferred to a separate, more
# carefully-guarded design pass (confirmation flow, hard limits), the same
# way purchasing's execute step was deliberately kept out of FindProductTool.
# Do not add arm/disarm/mode-set tools here without that pass.
# ---------------------------------------------------------------------------

class RCTelemetryTool(BaseTool):
    name = "get_rc_telemetry"
    description = (
        "Get current status from the user's RC crawler (battery voltage, GPS, "
        "whether it's armed, current mode). Read-only — does not control the "
        "vehicle in any way. Reads from ShinLink OS's agent bridge, so ShinLink "
        "OS must be running on your-pi."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        try:
            from integrations.shinlink_bridge import get_telemetry_snapshot

            d = get_telemetry_snapshot()
            if d is None:
                return (
                    "[get_rc_telemetry] Can't reach ShinLink OS's agent bridge "
                    "(127.0.0.1:8095 by default). Is ShinLink OS running on your-pi?"
                )
            if not d.get("connected"):
                return (
                    "[get_rc_telemetry] ShinLink OS is running but has no telemetry "
                    "connection to the crawler. Is it powered on?"
                )

            return (
                f"Crawler status: {'ARMED' if d['armed'] else 'disarmed'}, "
                f"mode={d['flight_mode'] if d['flight_mode'] != '?' else 'unknown'}, "
                f"vehicle_type={d['vehicle_type']}. "
                f"Battery: {d['batt_voltage']:.2f}V"
                + (f", {d['batt_pct']}%" if d['batt_pct'] >= 0 else "")
                + f". GPS: fix_type={d['gps_fix']}, {d['satellites']} satellites"
                + (f", lat={d['lat']:.6f} lon={d['lon']:.6f}" if d['gps_fix'] >= 2 else " (no fix)")
                + f". Ground speed: {d['groundspeed']:.1f} m/s."
            )
        except Exception as e:
            return f"[get_rc_telemetry] Error: {e}"


class ShinLinkNetworkStatusTool(BaseTool):
    name = "get_shinlink_network_status"
    description = (
        "Get ShinLink OS's UDP network-link status to the RC vehicle — link "
        "quality, round-trip latency, and packet counts. Use when the user asks "
        "about connection quality, link status, or whether the network link to "
        "the vehicle is up."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        try:
            from integrations.shinlink_bridge import get_network_snapshot

            d = get_network_snapshot()
            if d is None:
                return (
                    "[get_shinlink_network_status] Can't reach ShinLink OS's agent "
                    "bridge (127.0.0.1:8095 by default). Is ShinLink OS running on your-pi?"
                )
            if not d.get("enabled"):
                return "[get_shinlink_network_status] Network link is not enabled in ShinLink OS."

            return (
                f"Network link: quality {d['link_quality']}%, RTT {d['rtt_ms']:.0f}ms. "
                f"TX {d['tx_packets']} packets, RX {d['rx_packets']} packets. "
                f"Control port {d['ctrl_port']}, telemetry port {d['telem_port']}."
            )
        except Exception as e:
            return f"[get_shinlink_network_status] Error: {e}"


# ---------------------------------------------------------------------------
# Tool: ShinLink Control — Tier 1 discrete, reversible actions against
# ShinLink OS's agent bridge. Mirrors ControlAircraftTool/control_aircraft's
# single-dispatch shape (one tool, an action name, and a generic value).
# Deliberately does NOT include arm/disarm/mode-set/motor-test/RC-channel
# override — see shinlink-os/ground/agent_bridge.py's module docstring for
# why those need a separate, more carefully-guarded pass. Locked
# (permission: none) by default in config.yaml, same as control_aircraft
# and engage_autopilot — this changes real, physical vehicle-facing
# hardware state, so it isn't auto-granted like the read-only RC tools.
# ---------------------------------------------------------------------------

class ShinLinkControlTool(BaseTool):
    name = "shinlink_control"
    description = (
        "Send a control action to ShinLink OS's RC ground station. Actions: "
        "switch_protocol (value: 'cppm', 'sbus', or 'crsf' — changes which "
        "signal drives the vehicle's RC receiver from the trainer jack; "
        "proceeds even if a telemetry link is currently connected, just "
        "logged as a warning), load_preset (value: a saved vehicle preset "
        "name), start_network (value: target vehicle IP address, starts "
        "the UDP network link), stop_network (no value needed), start_adsb "
        "/ start_ais (no value needed — refuses if ADS-B, AIS, APRS, or "
        "Spectrum mode is already running; stop whatever's active first), "
        "stop_adsb / stop_ais (no value needed — refuses if that specific "
        "mode isn't the one currently active), set_vtx (value: an object "
        "with channel/band/power, at least one required), reset_tracker "
        "(no value needed — centres the antenna tracker servos). Requires "
        "ShinLink OS running on your-pi. Does not arm, disarm, or change "
        "vehicle mode — those aren't available yet."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "switch_protocol", "load_preset", "start_network", "stop_network",
                    "start_adsb", "stop_adsb", "start_ais", "stop_ais",
                    "set_vtx", "reset_tracker",
                ],
                "description": "Which ShinLink OS action to perform.",
            },
            "value": {
                "description": (
                    "Action-specific value: protocol name for switch_protocol "
                    "('cppm'/'sbus'/'crsf'), preset name for load_preset, target "
                    "IP address for start_network, or an object for set_vtx e.g. "
                    "{\"channel\": 5, \"band\": \"R\", \"power\": 200} (channel "
                    "1-8, band one of A/B/E/F/R, power in mW — all optional but "
                    "at least one required). Omit for stop_network, start_adsb, "
                    "stop_adsb, start_ais, stop_ais, and reset_tracker."
                ),
            },
        },
        "required": ["action"],
    }

    def run(self, action: str = "", value=None) -> str:
        from tools.shinlink_control import shinlink_control
        return shinlink_control(action=action, value=value)


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
        "Send an email from Q2's Gmail account (your-agent-email@gmail.com) via OAuth. "
        "Use for order confirmations, notifications, forwarding messages, or sending "
        "files and photos to the user. Supports file attachments by local path."
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
# and forward messages to the user's personal email on request.
# ---------------------------------------------------------------------------

class ReadEmailTool(BaseTool):
    name = "read_email"
    description = (
        "Check Q2's Gmail inbox (your-agent-email@gmail.com) for new unread messages, "
        "particularly shipping notifications and order confirmations. Use when the "
        "user asks about orders, packages, or their email. Can also forward messages "
        "to the user's personal email when asked."
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
        "Use when the user asks to make a playlist, find songs, or get a music link. "
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
        "Use to save documents, exports, or any file the user wants stored in Drive."
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
        "Use to export purchase history, track data, or create any tabular output for the user."
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
        "Use to write reports, notes, summaries, or any long-form content for the user."
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
        "Check the user's Google Calendar for upcoming events and create new ones. "
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
        "timestamp, and analyze it with the active LLM backend. Use when the user asks "
        "Q2 to take a photo, look at something, or capture an image. Always saves the "
        "photo permanently — not just to /tmp."
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
        "Use when the user asks to see a photo or display an image on screen."
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
        "Analyze a photo from the incoming folder using the active LLM backend, then move it to processed/. "
        "If no path given, lists photos waiting in ~/imq2/photos/incoming/. "
        "the user can drop photos into that folder for Q2 to analyze on demand."
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
        "Use when the user asks about token usage, API costs, or wants to log stats to a sheet. "
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
                # Free tier is literally $0 up to the daily limits. If the user
                # goes paid, Gemini 2.5 Flash is ~$0.15/$0.60 per million
                # tokens input/output -- update these if that happens.
                "gemini-2.5-flash":          (0.00,  0.00),
                "gemini-2.5-flash-lite":     (0.00,  0.00),
                "gemini-2.0-flash":          (0.00,  0.00),
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
        "Use when the user asks Q2 to save changes, push to GitHub, or commit work. "
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
        repo_path = Path("/home/your-pi/shinlink-os") if repo == "shinlink" else None
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
        "Get live sim racing telemetry data — auto-detects whether Forza or "
        "Assetto Corsa (AC/ACC/AC EVO/AC Rally) is currently running. Use during "
        "races to check speed, fuel, tyre temperatures, lap times, and race position. "
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
        "Auto-detects whether Forza or Assetto Corsa is currently running. "
        "Use for proactive callouts during a race. Short, spoken-word format."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.race_engineer import race_engineer_status
        return race_engineer_status()


class ACRaceEngineerTool(BaseTool):
    name = "get_ac_telemetry"
    description = (
        "Get live sim racing telemetry data — auto-detects whether Assetto Corsa "
        "(AC/ACC/AC EVO/AC Rally) or Forza is currently running. Use during sessions "
        "to check speed, fuel, tyre temps/pressure/wear, and lap times. "
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
        from tools.race_engineer_ac import get_ac_telemetry
        return get_ac_telemetry(fields=fields)


class ACRaceEngineerStatusTool(BaseTool):
    name = "ac_race_engineer_status"
    description = (
        "Get a concise race engineer status brief - fuel, tyre alerts, damage, flags, "
        "lap time delta. Auto-detects whether Assetto Corsa or Forza is currently running. "
        "Use for proactive callouts during a session. Short, spoken-word format."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.race_engineer_ac import ac_race_engineer_status
        return ac_race_engineer_status()


class GetDrivingVibeTool(BaseTool):
    name = "get_driving_vibe"
    description = (
        "Get current Forza Horizon open world driving status - speed, drift state, "
        "airtime. Use when the player asks what's happening or wants a status update "
        "while free roaming (not in a scored race)."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.forza_openworld import get_driving_vibe
        return get_driving_vibe()


class MarkLocationTool(BaseTool):
    name = "mark_location"
    description = (
        "Save the current Forza Horizon GPS position as a named landmark. Use when the "
        "player says 'mark this location as X' or 'save this spot as X'. Builds a "
        "personal map of the open world over time."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Name for this landmark."}},
        "required": ["name"],
    }
    def run(self, name: str) -> str:
        from tools.forza_openworld import mark_location
        return mark_location(name)


class WhereAreWeTool(BaseTool):
    name = "where_are_we"
    description = "Get a location description based on the current Forza Horizon GPS position. Use when the player asks where they are."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.forza_openworld import where_are_we
        return where_are_we()


class ListLocationsTool(BaseTool):
    name = "list_locations"
    description = "List all saved Forza Horizon landmarks and their visit counts."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.forza_openworld import list_locations
        return list_locations()


class RemoveLocationTool(BaseTool):
    name = "remove_location"
    description = "Remove a saved Forza Horizon landmark by name."
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Name of the landmark to remove."}},
        "required": ["name"],
    }
    def run(self, name: str) -> str:
        from tools.forza_openworld import remove_location
        return remove_location(name)


class ListNearbyLandmarksTool(BaseTool):
    name = "list_nearby_landmarks"
    description = "List known Forza Horizon landmarks near the current position. Use when the player asks 'what's nearby?'."
    input_schema = {
        "type": "object",
        "properties": {"radius_m": {"type": "integer", "description": "Search radius in metres (default 500)."}},
        "required": [],
    }
    def run(self, radius_m: int = 500) -> str:
        from tools.forza_openworld import list_nearby_landmarks
        return list_nearby_landmarks(radius_m=radius_m)


class GetLocationCalloutInfoTool(BaseTool):
    name = "get_location_callout_info"
    description = "Get full notes/context on a specific named Forza Horizon landmark. Use when the player asks 'tell me about X'."
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Landmark name."}},
        "required": ["name"],
    }
    def run(self, name: str = "") -> str:
        from tools.forza_openworld import get_location_callout_info
        return get_location_callout_info(name)


class ImportLocationMapTool(BaseTool):
    name = "import_location_map"
    description = (
        "Import a community Forza Horizon landmark map (JSON file path), or pass 'reload' "
        "to re-scan all map sources from disk. Use when the player gives a map file path."
    )
    input_schema = {
        "type": "object",
        "properties": {"file_path": {"type": "string", "description": "Path to a .json map file, or 'reload'."}},
        "required": ["file_path"],
    }
    def run(self, file_path: str = "reload") -> str:
        from tools.forza_openworld import import_location_map
        return import_location_map(file_path)


class ExportPersonalMapTool(BaseTool):
    name = "export_personal_map"
    description = "Export the player's personal Forza Horizon landmarks to a shareable JSON map file."
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Export file name, no extension."}},
        "required": [],
    }
    def run(self, name: str = "my_fh6_map") -> str:
        from tools.forza_openworld import export_personal_map
        return export_personal_map(name)


class GetRaceStatusTool(BaseTool):
    name = "get_race_status"
    description = (
        "Get current Forza Horizon race status - position, lap, lap times, positions "
        "gained/lost since the start. Use when the player asks about the race during an "
        "active FH6 race event (not free roam)."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.forza_openworld import get_race_status
        return get_race_status()


class GetDriftStatsTool(BaseTool):
    name = "get_drift_stats"
    description = (
        "Get recent Forza Horizon drift session statistics - duration, angle, peak yaw "
        "rate, and score per drift."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.forza_openworld import get_drift_stats
        return get_drift_stats()


class GenerateACCSetupTool(BaseTool):
    name = "generate_acc_setup"
    description = (
        "Generate an ACC car setup using current meta research and LLM. Researches "
        "community setups online, generates valid ACC JSON, saves to the companion app, "
        "and applies directly to ACC game. Required: car (ACC car name), track (ACC track "
        "name). Optional: session_type (sprint/endurance/qualifying, default sprint), "
        "weather (dry/wet/mixed, default dry), ambient_temp (celsius, default 22), "
        "track_temp (celsius, default 28), notes (additional requirements like 'understeer tendency')."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": "ACC internal car name, e.g. 'ferrari_296_gt3'."},
            "track": {"type": "string", "description": "ACC internal track name, e.g. 'spa'."},
            "session_type": {"type": "string", "enum": ["sprint", "endurance", "qualifying"]},
            "weather": {"type": "string", "enum": ["dry", "wet", "mixed"]},
            "ambient_temp": {"type": "integer", "description": "Ambient temperature in Celsius (default 22)."},
            "track_temp": {"type": "integer", "description": "Track temperature in Celsius (default 28)."},
            "notes": {"type": "string", "description": "Special requirements, e.g. 'understeer tendency'."},
        },
        "required": ["car", "track"],
    }
    def run(self, car: str = "", track: str = "", session_type: str = "sprint",
            weather: str = "dry", ambient_temp: int = 22, track_temp: int = 28,
            notes: str = "") -> str:
        from tools.acc_setup_generator import generate_acc_setup
        return generate_acc_setup(car=car, track=track, session_type=session_type,
                                   weather=weather, ambient_temp=ambient_temp,
                                   track_temp=track_temp, notes=notes)


class ListACCSetupsTool(BaseTool):
    name = "list_acc_setups"
    description = "List saved ACC setups from the companion app. Optional filters: car, track."
    input_schema = {
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": "Filter by ACC car name (optional)."},
            "track": {"type": "string", "description": "Filter by ACC track name (optional)."},
        },
        "required": [],
    }
    def run(self, car: str = "", track: str = "") -> str:
        from tools.acc_setup_generator import list_acc_setups
        return list_acc_setups(car=car or None, track=track or None)


class ApplyACCSetupTool(BaseTool):
    name = "apply_acc_setup"
    description = "Apply a saved ACC setup to the game by setup ID. Get IDs from list_acc_setups."
    input_schema = {
        "type": "object",
        "properties": {
            "setup_id": {"type": "integer", "description": "Setup ID from list_acc_setups."},
        },
        "required": ["setup_id"],
    }
    def run(self, setup_id: int = 0) -> str:
        from tools.acc_setup_generator import apply_acc_setup
        return apply_acc_setup(setup_id=setup_id)


class DeleteACCSetupTool(BaseTool):
    name = "delete_acc_setup"
    description = "Delete a saved ACC setup by ID."
    input_schema = {
        "type": "object",
        "properties": {
            "setup_id": {"type": "integer", "description": "Setup ID from list_acc_setups."},
        },
        "required": ["setup_id"],
    }
    def run(self, setup_id: int = 0) -> str:
        from tools.acc_setup_generator import delete_acc_setup
        return delete_acc_setup(setup_id=setup_id)


class GeneratePopupsTool(BaseTool):
    name = "generate_popups"
    description = (
        "Generate Pop Up Video style fact bubbles for a film or TV show. Researches "
        "production trivia, cast facts, music, filming locations, technical details, and "
        "historical context, then distributes timestamped pop-ups across the runtime. "
        "Call this before the user starts watching, once they've said what they're "
        "watching in Pop-Up Video mode."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Film or show title, e.g. 'Hackers'."},
            "year": {"type": "integer", "description": "Release year, e.g. 1995."},
            "episode": {"type": "string", "description": "TV episode code, e.g. 'S01E03'."},
        },
        "required": ["title"],
    }
    def run(self, title: str = "", year: int = None, episode: str = None) -> str:
        from tools.popup_video import generate_popups
        return generate_popups(title=title, year=year, episode=episode)


class GetPopupTool(BaseTool):
    name = "get_popup"
    description = (
        "Get the Pop-Up Video fact for the current timestamp during Pop-Up viewing. "
        "Call when the user says a time like '7 minutes', '12:30', or 'forty minutes in'. "
        "Returns the fact and pushes it to the companion panel."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "timestamp": {"type": "string", "description": "e.g. '7:23' or 'forty minutes'."},
        },
        "required": ["timestamp"],
    }
    def run(self, timestamp: str = "") -> str:
        from tools.popup_video import get_popup
        return get_popup(timestamp=timestamp)


class GetNextPopupsTool(BaseTool):
    name = "get_next_popups"
    description = (
        "Get the next upcoming Pop-Up Video timestamps and headlines. Use when the user "
        "asks what's coming up or wants to know when the next interesting fact is."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "timestamp": {"type": "string", "description": "Current playback position."},
            "count": {"type": "integer", "description": "How many upcoming pop-ups to return (default 3)."},
        },
        "required": ["timestamp"],
    }
    def run(self, timestamp: str = "", count: int = 3) -> str:
        from tools.popup_video import get_next_popups
        return get_next_popups(timestamp=timestamp, count=count)


class ListPopupTitlesTool(BaseTool):
    name = "list_popup_titles"
    description = "List all films and shows with saved Pop-Up Video data."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.popup_video import list_popup_titles
        return list_popup_titles()


class SetPopupTitleTool(BaseTool):
    name = "set_popup_title"
    description = (
        "Load a previously-generated Pop-Up Video session without regenerating it. Use when "
        "the user wants to resume watching something they already made pop-ups for."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Film or show title, e.g. 'Hackers'."},
            "year": {"type": "integer", "description": "Release year, e.g. 1995."},
        },
        "required": ["title"],
    }
    def run(self, title: str = "", year: int = None) -> str:
        from tools.popup_video import set_popup_title
        return set_popup_title(title=title, year=year)


class ClearPopupSessionTool(BaseTool):
    name = "clear_popup_session"
    description = "Clear the active Pop-Up Video session, e.g. when switching to a different title."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.popup_video import clear_popup_session
        return clear_popup_session()


class GetFlightStatusTool(BaseTool):
    name = "get_flight_status"
    description = (
        "Get live Microsoft Flight Simulator telemetry — position, engines, autopilot, "
        "fuel, systems, weather, or nav data. Use during a flight to check altitude, "
        "airspeed, heading, autopilot state, fuel endurance, or the next waypoint. "
        "fields can be: summary (default), position, engines, autopilot, fuel, systems, "
        "weather, nav, or all."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {
                "type": "string",
                "enum": ["summary", "position", "engines", "autopilot", "fuel", "systems", "weather", "nav", "all"],
                "description": "Which telemetry fields to return.",
            },
        },
        "required": [],
    }
    def run(self, fields: str = "summary") -> str:
        from tools.first_officer import get_flight_status
        return get_flight_status(fields=fields)


class FirstOfficerStatusTool(BaseTool):
    name = "first_officer_status"
    description = (
        "Get a concise first-officer status brief - altitude alerting, gear/fuel checks, "
        "bank angle, autopilot/engine faults, waypoint proximity, approach checklist, "
        "weather advisories. Use for proactive callouts during a flight. Short, spoken-word format."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.first_officer import first_officer_status
        return first_officer_status()


class ControlAircraftTool(BaseTool):
    name = "control_aircraft"
    description = (
        "Send a control command to MSFS via the bridge. Use for autopilot, flaps, "
        "gear, lights, throttle, transponder, frequencies. Requires msfs_bridge.py "
        "running on the Windows PC. Commands: autopilot_toggle, autopilot_on, "
        "autopilot_off, set_autopilot_altitude (feet), set_autopilot_heading (degrees), "
        "set_autopilot_airspeed (knots), set_autopilot_vs (fpm), enable_altitude_hold, "
        "enable_heading_hold, enable_nav_hold, enable_approach_hold, enable_autothrottle, "
        "toggle_vnav, set_flaps (0-4), flaps_up, flaps_down, gear_toggle, gear_up, "
        "gear_down, set_transponder (code), set_com1 (MHz), set_nav1 (MHz), "
        "set_throttle (0-100), throttle_full, throttle_idle, toggle_engine_1, "
        "toggle_engine_2, landing_lights_on, landing_lights_off, strobes_toggle, "
        "nav_lights_toggle, pause_sim, unpause_sim, set_sim_rate (e.g. 1.0/2.0/0.5)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command name — see description for the full list."},
            "value": {"type": "number", "description": "Numeric value, required for set_* commands (feet/degrees/knots/fpm/code/MHz/percent/rate)."},
        },
        "required": ["command"],
    }
    def run(self, command: str = "", value: float = None) -> str:
        from tools.first_officer import control_aircraft
        return control_aircraft(command=command, value=value)


class EngageAutopilotTool(BaseTool):
    name = "engage_autopilot"
    description = (
        "Engage autopilot with altitude and optionally heading/airspeed. Compound "
        "command: sets altitude (and heading/airspeed if given), enables the matching "
        "holds, then turns the AP master on. Use when the pilot says 'take over', "
        "'engage autopilot', 'level off at X thousand feet', 'maintain altitude'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "altitude_ft": {"type": "integer", "description": "Target altitude in feet."},
            "heading_deg": {"type": "integer", "description": "Target heading in degrees (optional)."},
            "airspeed_kts": {"type": "integer", "description": "Target airspeed in knots (optional)."},
        },
        "required": ["altitude_ft"],
    }
    def run(self, altitude_ft: int = 0, heading_deg: int = None, airspeed_kts: int = None) -> str:
        from tools.first_officer import engage_autopilot_level_off
        return engage_autopilot_level_off(altitude_ft=altitude_ft, heading_deg=heading_deg, airspeed_kts=airspeed_kts)


class ApproachChecklistTool(BaseTool):
    name = "run_approach_checklist"
    description = (
        "Execute approach checklist: gear down, flaps to approach, landing lights on, "
        "strobes on. Use when the pilot says 'run the approach checklist', 'prepare "
        "for landing', 'gear down and locked'."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.first_officer import execute_approach_checklist
        return execute_approach_checklist()


class EmergencySquawkTool(BaseTool):
    name = "emergency_squawk"
    description = "Set transponder to 7700 emergency squawk."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.first_officer import set_emergency_transponder
        return set_emergency_transponder()


class GetF1StatusTool(BaseTool):
    name = "get_f1_status"
    description = (
        "Get live Formula 1 race status via OpenF1 — current lap, positions, gaps, "
        "tyres, or weather. Use during Watchalong Live mode (F1) to check the live standings. "
        "fields can be: summary (default), positions, tyres, weather, or all."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {
                "type": "string",
                "enum": ["summary", "positions", "tyres", "weather", "all"],
                "description": "Which status fields to return.",
            },
        },
        "required": [],
    }
    def run(self, fields: str = "summary") -> str:
        from tools.f1_analyst import get_f1_status
        return get_f1_status(fields=fields)


class GetF1DriverTool(BaseTool):
    name = "get_f1_driver"
    description = (
        "Get a specific F1 driver's live race status — position, gap to leader, "
        "tyre compound and age. Use when the user asks how a specific driver is doing "
        "during Watchalong Live mode (F1). Accepts a driver number, acronym (e.g. VER), or name."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "driver": {"type": "string", "description": "Driver number, acronym, or (partial) name, e.g. 'VER' or 'Norris'."},
        },
        "required": ["driver"],
    }
    def run(self, driver: str = "") -> str:
        from tools.f1_analyst import get_f1_driver
        return get_f1_driver(driver=driver)


class F1RaceAlertTool(BaseTool):
    name = "f1_race_alert"
    description = (
        "Check for any new significant live F1 race events since the last check — "
        "safety cars, flags, fastest laps, leader changes, penalties, pit stops. "
        "Use when the user asks 'anything happening?' during Watchalong Live mode (F1)."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.f1_analyst import f1_race_alert
        return f1_race_alert()


class GetReplayLapTool(BaseTool):
    name = "get_replay_lap"
    description = (
        "Get race narrative for a specific lap during Watchalong Replay mode (F1). Call when "
        "the user mentions a lap number while watching a recorded race. Returns position, "
        "gaps, pit stops, incidents, and strategy context for that lap without spoilers."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "lap_number": {"type": "integer", "description": "The lap number the user just called out."},
        },
        "required": ["lap_number"],
    }
    def run(self, lap_number: int = 0) -> str:
        from tools.f1_analyst import get_replay_lap
        return get_replay_lap(lap_number=lap_number)


class GetReplayStatusTool(BaseTool):
    name = "get_replay_status"
    description = (
        "Get strategy/tyre detail for a specific lap during Watchalong Replay mode (F1) — "
        "like get_f1_status but for a historical lap instead of live data. Use fields='tyres' "
        "for a compound/tyre-age breakdown, or 'summary' for the standard position/gap view."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "lap_number": {"type": "integer", "description": "The lap number the user just called out."},
            "fields": {"type": "string", "enum": ["summary", "tyres"], "description": "Default 'summary'."},
        },
        "required": ["lap_number"],
    }
    def run(self, lap_number: int = 0, fields: str = "summary") -> str:
        from tools.f1_analyst import get_replay_status
        return get_replay_status(lap_number=lap_number, fields=fields)


class ListF1RacesTool(BaseTool):
    name = "list_f1_races"
    description = (
        "List available F1 races by year. Use when the user asks what race data is "
        "available for Watchalong Replay mode."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "year": {"type": "integer", "description": "Year to list races for. Omit to list 2023-present."},
        },
        "required": [],
    }
    def run(self, year: int = None) -> str:
        from tools.f1_analyst import list_f1_races
        return list_f1_races(year=year)


class StartF1ReplaySessionTool(BaseTool):
    name = "start_f1_replay_session"
    description = (
        "Find a historical F1 race by name/year (e.g. 'Monaco 2024', 'last year "
        "Singapore', 'Spa') and activate it as the Watchalong Replay session. Call this as "
        "soon as the user tells Q2 which race he's watching in Watchalong Replay mode (F1), "
        "before he starts calling out lap numbers."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Race name and/or year, e.g. 'Monaco 2024' or 'the Japanese GP'."},
            "session_type": {
                "type": "string",
                "enum": ["Race", "Qualifying", "Sprint", "Practice 1", "Practice 2", "Practice 3"],
                "description": "Which session of that race weekend. Defaults to 'Race'.",
            },
        },
        "required": ["query"],
    }
    def run(self, query: str = "", session_type: str = "Race") -> str:
        from tools.f1_analyst import start_replay_session
        return start_replay_session(query=query, session_type=session_type)


class SwitchAgentModeTool(BaseTool):
    name = "switch_agent_mode"
    description = (
        "Switch Q2 to a different agent mode/personality profile — e.g. Watchalong Live, "
        "Watchalong Replay, Race Engineer, First Officer, Radio DJ, or back to normal Q2. Use when "
        "the user asks to switch modes, such as 'switch to watchalong live', 'watch F1 live', "
        "'watch the UFC', 'watch the NBA', 'watch the NHL', 'watch Formula Drift', 'watch X Games', "
        "'switch to watchalong replay', 'be my DJ', or 'go back to normal Q2'. For the two watchalong "
        "profiles, also pass sport ('f1', 'ufc', 'nba', 'nhl', 'formula_drift', or 'xgames') if "
        "the user named a sport — omit it to leave the sport as it already was."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "profile": {
                "type": "string",
                "description": (
                    "Profile name/stem to switch to, e.g. 'watchalong_live', 'watchalong_replay', "
                    "'q2_default', 'race_engineer', 'first_officer'."
                ),
            },
            "sport": {
                "type": "string",
                "enum": ["f1", "ufc", "nba", "nhl", "nfl", "mlb", "formula_drift", "xgames"],
                "description": (
                    "Only meaningful for profile 'watchalong_live'/'watchalong_replay' — which "
                    "sport to activate. Omit to keep whichever sport was already active."
                ),
            },
        },
        "required": ["profile"],
    }

    def run(self, profile: str = "", sport: str = "") -> str:
        stem = profile.strip().lower().replace(" ", "_").replace("-", "_")
        available = config.list_profiles()
        if stem not in available:
            return f"[switch_agent_mode] Unknown profile '{profile}'. Available: {', '.join(available)}"

        try:
            config.load_profile(stem)
            config.save()
        except Exception as e:
            return f"[switch_agent_mode] Failed to switch profile: {e}"

        mode_name = config.profile.get("name", stem)
        note = ""

        # Watchalong modes announce their own activation state so this single
        # tool result gives the LLM everything it needs to speak the right
        # activation message in the same turn, without a separate hook. Sport
        # is a runtime setting shared by both watchalong profiles (see
        # config.yaml's watchalong.active_sport) rather than a separate
        # profile per sport — this is the one place voice activation sets it.
        if stem in ("watchalong_live", "watchalong_replay"):
            sport_key = sport.strip().lower() if sport else config.get("watchalong.active_sport", "f1")
            if sport_key not in ("f1", "ufc", "nba", "nhl", "nfl", "mlb", "formula_drift", "xgames"):
                sport_key = "f1"
            config.raw.setdefault("watchalong", {})["active_sport"] = sport_key
            config.raw["watchalong"]["mode"] = "live" if stem == "watchalong_live" else "replay"
            config.save()

            if stem == "watchalong_live":
                if sport_key == "f1":
                    try:
                        from integrations.f1_watchalong import detect_live_session, next_upcoming_race
                        session = detect_live_session()
                        if session and session.get("is_live"):
                            from tools.f1_analyst import get_f1_status
                            note = "\n" + get_f1_status(fields="summary")
                        else:
                            upcoming = next_upcoming_race()
                            if upcoming:
                                note = f"\nNo live session detected. Next race is {upcoming['name']} on {upcoming['date']}."
                            else:
                                note = "\nNo live session detected and no upcoming race found."
                    except Exception as e:
                        note = f"\n(Could not check live session: {e})"
                elif sport_key == "ufc":
                    try:
                        from tools.ufc_analyst import get_ufc_status
                        note = "\n" + get_ufc_status(fields="card")
                    except Exception as e:
                        note = f"\n(Could not check tonight's card: {e})"
                elif sport_key == "nba":
                    try:
                        from tools.nba_analyst import get_nba_status
                        note = "\n" + get_nba_status(fields="summary")
                    except Exception as e:
                        note = f"\n(Could not check today's NBA game: {e})"
                elif sport_key == "nhl":
                    try:
                        from tools.nhl_analyst import get_nhl_status
                        note = "\n" + get_nhl_status(fields="summary")
                    except Exception as e:
                        note = f"\n(Could not check today's NHL game: {e})"
                elif sport_key == "nfl":
                    try:
                        from tools.nfl_analyst import get_nfl_status
                        note = "\n" + get_nfl_status(fields="summary")
                    except Exception as e:
                        note = f"\n(Could not check today's NFL game: {e})"
                elif sport_key == "mlb":
                    try:
                        from tools.mlb_analyst import get_mlb_status
                        note = "\n" + get_mlb_status(fields="summary")
                    except Exception as e:
                        note = f"\n(Could not check today's MLB game: {e})"
                elif sport_key == "formula_drift":
                    # No live feed exists for Formula Drift at all -- "Live"
                    # here means standings/driver context, not a real-time
                    # bracket, so this is honest about that rather than
                    # pretending to check something live.
                    try:
                        from tools.formula_drift_analyst import get_fd_standings
                        note = "\n(No live Formula Drift feed exists -- here's the current standings.)\n" + get_fd_standings()
                    except Exception as e:
                        note = f"\n(Could not check standings: {e})"
                elif sport_key == "xgames":
                    try:
                        from tools.xgames_analyst import get_xgames_results
                        note = "\n(X Games results only post after events end.)\n" + get_xgames_results()
                    except Exception as e:
                        note = f"\n(Could not check results: {e})"
            else:  # watchalong_replay
                if sport_key == "f1":
                    note = (
                        "\nWhich race are you watching? Say the race name and year, "
                        "for example 'Monaco 2024' or 'last year's British Grand Prix'."
                    )
                elif sport_key == "ufc":
                    note = (
                        "\nWhich fight are you watching? You can say the event -- 'UFC 300', "
                        "or the fighters -- 'Poirier vs Makhachev', or describe it -- "
                        "'the Khabib retirement fight'."
                    )
                elif sport_key == "nba":
                    note = (
                        "\nWhich game are you watching? Say the date and a team, for example "
                        "'Lakers, January 15th' or 'last night's Celtics game'."
                    )
                elif sport_key == "nhl":
                    note = (
                        "\nWhich game are you watching? Say the date and both teams, for example "
                        "'Leafs versus Canadiens, last Tuesday'."
                    )
                elif sport_key == "nfl":
                    note = (
                        "\nWhich game are you watching? Say the date and both teams, for example "
                        "'Chiefs versus Bills, last Sunday'."
                    )
                elif sport_key == "mlb":
                    note = (
                        "\nWhich game are you watching? Say the date and a team, for example "
                        "'Blue Jays, last night' or 'Yankees game from Tuesday'."
                    )
                elif sport_key == "formula_drift":
                    note = (
                        "\nWhich round are you watching? Say the round number or event name, for "
                        "example 'round 3' or 'Orlando'."
                    )
                elif sport_key == "xgames":
                    note = (
                        "\nWhich event or discipline are you watching? For example "
                        "'snowboard superpipe' or 'X Games Aspen 2025'."
                    )

        return f"Switched to {mode_name} mode.{note}"


class GetUFCStatusTool(BaseTool):
    name = "get_ufc_status"
    description = (
        "Get UFC event information — tonight's card, next upcoming event, or a "
        "fighter's ESPN record. Use in Watchalong Live mode (UFC). fields can be: "
        "card (default), upcoming, fighter, event, or all."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {
                "type": "string",
                "enum": ["card", "upcoming", "fighter", "event", "all"],
                "description": "Which status fields to return.",
            },
            "fighter": {"type": "string", "description": "Fighter name (required when fields='fighter')."},
        },
        "required": [],
    }
    def run(self, fields: str = "card", fighter: str = "") -> str:
        from tools.ufc_analyst import get_ufc_status
        return get_ufc_status(fields=fields, fighter=fighter)


class GetUFCFightTool(BaseTool):
    name = "get_ufc_fight"
    description = (
        "Get a fighter's ESPN record, or head-to-head history between two fighters "
        "if they've fought before. Works in both Watchalong Live and Watchalong Replay modes (UFC)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fighter1": {"type": "string", "description": "First fighter's name."},
            "fighter2": {"type": "string", "description": "Second fighter's name, for a head-to-head lookup (optional)."},
        },
        "required": ["fighter1"],
    }
    def run(self, fighter1: str = "", fighter2: str = "") -> str:
        from tools.ufc_analyst import get_ufc_fight
        return get_ufc_fight(fighter1=fighter1, fighter2=fighter2)


class UFCPrefightBriefTool(BaseTool):
    name = "ufc_prefight_brief"
    description = (
        "Get pre-fight context (ESPN records) for two fighters before a bout starts. "
        "Use in Watchalong Live mode (UFC) before each fight — add the actual style/strategy "
        "analysis yourself from your own knowledge of the fighters."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fighter1": {"type": "string", "description": "First fighter's name."},
            "fighter2": {"type": "string", "description": "Second fighter's name."},
        },
        "required": ["fighter1", "fighter2"],
    }
    def run(self, fighter1: str = "", fighter2: str = "") -> str:
        from tools.ufc_analyst import ufc_prefight_brief
        return ufc_prefight_brief(fighter1=fighter1, fighter2=fighter2)


class PopulateUFCEventTool(BaseTool):
    name = "populate_ufc_event"
    description = (
        "Find a UFC event by name/year (e.g. 'UFC 300', 'last year's International "
        "Fight Week') and activate it for Watchalong Replay mode — fetches and caches the "
        "full fight card from ESPN. Call this as soon as the user says which event "
        "he's watching, e.g. 'Q2, we're going to watch UFC 300, populate your stats.'"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Event name and/or year, e.g. 'UFC 300' or 'the Khabib retirement fight'."},
        },
        "required": ["query"],
    }
    def run(self, query: str = "") -> str:
        from tools.ufc_analyst import populate_ufc_event
        return populate_ufc_event(query=query)


class StartUFCReplayFightTool(BaseTool):
    name = "start_ufc_replay_fight"
    description = (
        "Select a specific bout within the already-populated UFC event to start "
        "tracking round numbers for. Call once the user says which fight he's "
        "starting with (a fighter name is enough) — requires populate_ufc_event "
        "to have been called first."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fighter1": {"type": "string", "description": "Either fighter's name from the bout the user is starting."},
            "fighter2": {"type": "string", "description": "The other fighter's name (optional, helps disambiguate)."},
        },
        "required": ["fighter1"],
    }
    def run(self, fighter1: str = "", fighter2: str = "") -> str:
        from tools.ufc_analyst import start_ufc_replay_fight
        return start_ufc_replay_fight(fighter1=fighter1, fighter2=fighter2)


class GetUFCRoundTool(BaseTool):
    name = "get_ufc_round"
    description = (
        "Get the round boundary/context for the current Watchalong Replay fight. Use when "
        "the user calls out a round number. Enforces spoiler protection — narrate the "
        "round from your own knowledge of the fight, never referencing rounds beyond it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "round_number": {"type": "integer", "description": "The round number the user just called out."},
        },
        "required": ["round_number"],
    }
    def run(self, round_number: int = 0) -> str:
        from tools.ufc_analyst import get_ufc_round
        return get_ufc_round(round_number=round_number)


class GetUFCScorecardTool(BaseTool):
    name = "get_ufc_scorecard"
    description = (
        "Get a running-scorecard boundary check for the current Watchalong Replay fight "
        "through the specified round. Does not reveal future rounds — give the actual "
        "scorecard from your own knowledge, bounded to rounds already reached."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "through_round": {"type": "integer", "description": "Score the fight through this round."},
        },
        "required": ["through_round"],
    }
    def run(self, through_round: int = 0) -> str:
        from tools.ufc_analyst import get_ufc_scorecard
        return get_ufc_scorecard(through_round=through_round)


class ListUFCEventsTool(BaseTool):
    name = "list_ufc_events"
    description = "List UFC events available for Watchalong Replay mode, by year."
    input_schema = {
        "type": "object",
        "properties": {
            "year": {"type": "integer", "description": "Year to list events for. Omit for recent years."},
        },
        "required": [],
    }
    def run(self, year: int = None) -> str:
        from tools.ufc_analyst import list_ufc_events
        return list_ufc_events(year=year)


class SearchUFCEventTool(BaseTool):
    name = "search_ufc_event"
    description = (
        "Find a UFC event or specific fight for Watchalong Replay mode. Accepts an event "
        "name, fighter names, a year, or a description — use when the user isn't sure "
        "of the exact event name or when multiple events might match."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Event name, fighter names, year, or description."},
        },
        "required": ["query"],
    }
    def run(self, query: str = "") -> str:
        from tools.ufc_analyst import search_ufc_event
        return search_ufc_event(query=query)


class GetNBAStatusTool(BaseTool):
    name = "get_nba_status"
    description = (
        "Get live NBA game status — score, quarter, leading team. Use during NBA "
        "Watchalong Live mode. fields can be: summary (default) or score."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {"type": "string", "enum": ["summary", "score"], "description": "Which status fields to return."},
        },
        "required": [],
    }
    def run(self, fields: str = "summary") -> str:
        from tools.nba_analyst import get_nba_status
        return get_nba_status(fields=fields)


class GetNBAReplayTool(BaseTool):
    name = "get_nba_replay_period"
    description = (
        "Get NBA game state through a given quarter for Watchalong Replay mode — "
        "spoiler-protected, only shows data up to the specified quarter. Use when "
        "the user calls out a quarter while watching a recorded NBA game."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "game_date": {"type": "string", "description": "Game date, YYYY-MM-DD."},
            "team": {"type": "string", "description": "Either team's name or abbreviation."},
            "through_period": {"type": "integer", "description": "1=after Q1, 2=halftime, 3=after Q3, 4=final (5+=OT)."},
        },
        "required": ["game_date", "team", "through_period"],
    }
    def run(self, game_date: str = "", team: str = "", through_period: int = 1) -> str:
        from tools.nba_analyst import get_nba_replay_period
        return get_nba_replay_period(game_date=game_date, team=team, through_period=through_period)


class NBAAlertTool(BaseTool):
    name = "nba_game_alert"
    description = (
        "Check for new NBA scoring events or quarter changes since the last check. "
        "Use when the user asks 'anything happening?' during NBA Watchalong Live mode."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.nba_analyst import nba_game_alert
        return nba_game_alert()


class GetNHLStatusTool(BaseTool):
    name = "get_nhl_status"
    description = (
        "Get live NHL game status — score, period, clock, leading team. Use during NHL "
        "Watchalong Live mode. fields can be: summary (default) or score."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {"type": "string", "enum": ["summary", "score"], "description": "Which status fields to return."},
        },
        "required": [],
    }
    def run(self, fields: str = "summary") -> str:
        from tools.nhl_analyst import get_nhl_status
        return get_nhl_status(fields=fields)


class GetNHLReplayTool(BaseTool):
    name = "get_nhl_replay_period"
    description = (
        "Get NHL game state through a given period for Watchalong Replay mode — "
        "spoiler-protected, only shows data up to the specified period. Use when "
        "the user calls out a period while watching a recorded NHL game."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "game_date": {"type": "string", "description": "Game date, YYYY-MM-DD."},
            "home_team": {"type": "string", "description": "Home team abbreviation, e.g. TOR."},
            "away_team": {"type": "string", "description": "Away team abbreviation, e.g. MTL."},
            "through_period": {"type": "integer", "description": "1-3 for regulation periods, 4=OT."},
        },
        "required": ["game_date", "home_team", "away_team", "through_period"],
    }
    def run(self, game_date: str = "", home_team: str = "", away_team: str = "", through_period: int = 1) -> str:
        from tools.nhl_analyst import get_nhl_replay_period
        return get_nhl_replay_period(game_date=game_date, home_team=home_team, away_team=away_team, through_period=through_period)


class NHLAlertTool(BaseTool):
    name = "nhl_game_alert"
    description = (
        "Check for new NHL goals or period changes since the last check. "
        "Use when the user asks 'anything happening?' during NHL Watchalong Live mode."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.nhl_analyst import nhl_game_alert
        return nhl_game_alert()


class GetNFLStatusTool(BaseTool):
    name = "get_nfl_status"
    description = (
        "Get live NFL game status — score, quarter, clock, down and distance, possession. "
        "Use during NFL Watchalong Live mode. fields can be: summary (default) or score."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {"type": "string", "enum": ["summary", "score"], "description": "Which status fields to return."},
        },
        "required": [],
    }
    def run(self, fields: str = "summary") -> str:
        from tools.nfl_analyst import get_nfl_status
        return get_nfl_status(fields=fields)


class GetNFLReplayTool(BaseTool):
    name = "get_nfl_replay_quarter"
    description = (
        "Get NFL game state through a given quarter for Watchalong Replay mode — spoiler-"
        "protected, only shows data up to the specified quarter. Use when the user calls out "
        "a quarter while watching a recorded NFL game."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "game_date": {"type": "string", "description": "Game date, YYYY-MM-DD."},
            "home_team": {"type": "string", "description": "Home team name or abbreviation."},
            "away_team": {"type": "string", "description": "Away team name or abbreviation."},
            "through_quarter": {"type": "integer", "description": "1=after Q1, 2=halftime, 3=after Q3, 4=final (5+=OT)."},
        },
        "required": ["game_date", "home_team", "away_team", "through_quarter"],
    }
    def run(self, game_date: str = "", home_team: str = "", away_team: str = "", through_quarter: int = 1) -> str:
        from tools.nfl_analyst import get_nfl_replay_quarter
        return get_nfl_replay_quarter(game_date=game_date, home_team=home_team, away_team=away_team, through_quarter=through_quarter)


class NFLAlertTool(BaseTool):
    name = "nfl_game_alert"
    description = (
        "Check for new NFL scoring events or quarter changes since the last check. "
        "Use when the user asks 'anything happening?' during NFL Watchalong Live mode."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.nfl_analyst import nfl_game_alert
        return nfl_game_alert()


class GetMLBStatusTool(BaseTool):
    name = "get_mlb_status"
    description = (
        "Get live MLB game status — score, inning, count, outs, current batter/pitcher. "
        "Use during MLB Watchalong Live mode. fields can be: summary (default) or score."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {"type": "string", "enum": ["summary", "score"], "description": "Which status fields to return."},
        },
        "required": [],
    }
    def run(self, fields: str = "summary") -> str:
        from tools.mlb_analyst import get_mlb_status
        return get_mlb_status(fields=fields)


class GetMLBReplayTool(BaseTool):
    name = "get_mlb_replay_inning"
    description = (
        "Get MLB game state through a specific inning for Watchalong Replay mode — spoiler-"
        "protected. Use when the user calls out an inning while watching a recorded MLB game."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "game_date": {"type": "string", "description": "Game date, YYYY-MM-DD."},
            "team": {"type": "string", "description": "Either team's name or abbreviation."},
            "through_inning": {"type": "integer", "description": "Inning number, 1-9+."},
            "through_half": {"type": "string", "enum": ["top", "bottom"], "description": "Default 'bottom'."},
        },
        "required": ["game_date", "team", "through_inning"],
    }
    def run(self, game_date: str = "", team: str = "", through_inning: int = 1, through_half: str = "bottom") -> str:
        from tools.mlb_analyst import get_mlb_replay_inning
        return get_mlb_replay_inning(game_date=game_date, team=team, through_inning=through_inning, through_half=through_half)


class MLBAlertTool(BaseTool):
    name = "mlb_game_alert"
    description = (
        "Check for new MLB scoring plays, home runs, or inning changes since the last check. "
        "Use when the user asks 'anything happening?' during MLB Watchalong Live mode."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.mlb_analyst import mlb_game_alert
        return mlb_game_alert()


class GenerateDJSetTool(BaseTool):
    name = "generate_dj_set"
    description = (
        "Generate and start a themed DJ set played via YouTube, with Q2 providing DJ "
        "commentary between tracks. Use when the user says 'play a DJ set', 'be my DJ', "
        "'play something themed', or names a specific musical theme. "
        "theme can be a specific idea or 'free choice' to let Q2 pick based on time of day."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "theme": {"type": "string", "description": "A specific theme (e.g. 'The Neptunes 2001-2004'), or 'free choice'."},
            "style": {
                "type": "string",
                "enum": ["back_announce", "pre_announce", "mid_song", "liner_notes"],
                "description": "Commentary style. Defaults to back_announce.",
            },
            "track_count": {"type": "integer", "description": "Number of tracks, 3-10. Defaults to 5."},
            "mood": {"type": "string", "description": "How the set's energy should evolve, e.g. journey/chill/energetic/building. Defaults to journey."},
        },
        "required": ["theme"],
    }
    def run(self, theme: str = "free choice", style: str = None, track_count: int = None, mood: str = None) -> str:
        from tools.radio_dj import generate_dj_set
        return generate_dj_set(theme=theme, style=style, track_count=track_count, mood=mood)


class StartDJPresetTool(BaseTool):
    name = "start_dj_preset"
    description = (
        "Start one of the ready-made DJ set presets instantly, with no generation delay. "
        "Use list_dj_presets to see what's available if unsure of the exact name."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "preset": {"type": "string", "description": "Preset name, e.g. 'uk_garage', 'amen_break', 'late_night'."},
        },
        "required": ["preset"],
    }
    def run(self, preset: str = "") -> str:
        from tools.radio_dj import start_preset_set
        return start_preset_set(preset)


class DJStatusTool(BaseTool):
    name = "dj_status"
    description = "Get current DJ set status -- what's playing, theme, progress through the set."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.radio_dj import dj_status
        return dj_status()


class DJSkipTool(BaseTool):
    name = "dj_skip"
    description = "Skip the current track and move to the next one in the DJ set."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.radio_dj import dj_skip
        return dj_skip()


class DJStopTool(BaseTool):
    name = "dj_stop"
    description = "Stop the current DJ session."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.radio_dj import dj_stop
        return dj_stop()


class DJTrackInfoTool(BaseTool):
    name = "dj_track_info"
    description = (
        "Get interesting facts about the currently playing DJ-set track. "
        "Use when the user asks about the current song during DJ mode."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.radio_dj import dj_track_info
        return dj_track_info()


class ListDJPresetsTool(BaseTool):
    name = "list_dj_presets"
    description = "List DJ set theme ideas Q2 can generate, and note which ones are ready-made presets."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.radio_dj import list_dj_presets
        return list_dj_presets()


class GetFDStandingsTool(BaseTool):
    name = "get_fd_standings"
    description = (
        "Get Formula Drift PRO championship standings for a year. Formula Drift has no live "
        "data feed, so use this for standings/driver context during Watchalong (sport: formula_drift), "
        "not real-time bracket results."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "year": {"type": "integer", "description": "Season year. Omit for the current year."},
        },
        "required": [],
    }
    def run(self, year: int = None) -> str:
        from tools.formula_drift_analyst import get_fd_standings
        return get_fd_standings(year=year)


class GetFDRoundTool(BaseTool):
    name = "get_fd_round"
    description = "Get Formula Drift round details (event name, location, date, status) from the season schedule."
    input_schema = {
        "type": "object",
        "properties": {
            "round_num": {"type": "integer", "description": "Round number, e.g. 1."},
            "year": {"type": "integer", "description": "Season year. Omit for the current year."},
        },
        "required": ["round_num"],
    }
    def run(self, round_num: int = 1, year: int = None) -> str:
        from tools.formula_drift_analyst import get_fd_round
        return get_fd_round(round_num=round_num, year=year)


class GetXGamesResultsTool(BaseTool):
    name = "get_xgames_results"
    description = (
        "Get the latest available X Games results, optionally filtered to a discipline "
        "(snowboard/ski/skateboard/bmx/moto_x). X Games results only post after events end -- "
        "not a live scoring feed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "discipline": {
                "type": "string",
                "enum": ["all", "snowboard", "ski", "skateboard", "bmx", "moto_x"],
                "description": "Filter to one discipline. Defaults to all.",
            },
            "year": {"type": "integer", "description": "Only affects hand-curated historical entries, not live scraping."},
        },
        "required": [],
    }
    def run(self, discipline: str = "all", year: int = None) -> str:
        from tools.xgames_analyst import get_xgames_results
        return get_xgames_results(discipline=discipline, year=year)


class PlanMealTool(BaseTool):
    name = "plan_meal"
    description = (
        "Plan a MasterChef cooking session -- Gordon Ramsay proposes a themed menu based on cuisine "
        "and occasion. Use as soon as the user says he wants to cook, names a cuisine, or says "
        "'you pick' (pass cuisine='free' for that case)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "cuisine": {"type": "string", "description": "mexican/italian/chinese/thai/desserts_baking, or 'free' to let Gordon pick."},
            "occasion": {"type": "string", "description": "weeknight/dinner_party/date_night/family/solo. Defaults to weeknight."},
            "dietary_notes": {"type": "string", "description": "Any restrictions or preferences, e.g. 'no pork', 'vegetarian'."},
        },
        "required": ["cuisine"],
    }
    def run(self, cuisine: str = "free", occasion: str = "weeknight", dietary_notes: str = "") -> str:
        from tools.masterchef import plan_meal
        return plan_meal(cuisine=cuisine, occasion=occasion, dietary_notes=dietary_notes)


class BuildShoppingListTool(BaseTool):
    name = "build_shopping_list"
    description = (
        "Build a Gordon Ramsay-annotated shopping list for selected MasterChef dishes. Groups by "
        "category with specific brand and quality recommendations. Call once the menu is confirmed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "dishes": {"type": "array", "items": {"type": "string"}, "description": "Dish name(s) from the confirmed menu."},
        },
        "required": ["dishes"],
    }
    def run(self, dishes: list = None) -> str:
        from tools.masterchef import build_shopping_list
        return build_shopping_list(dishes or [])


class StartRecipeTool(BaseTool):
    name = "start_recipe"
    description = (
        "Begin cooking a specific dish in MasterChef mode. Returns Gordon's intro and first step. "
        "Use when the user says he's ready to start cooking."
    )
    input_schema = {
        "type": "object",
        "properties": {"dish_name": {"type": "string", "description": "The dish to start cooking."}},
        "required": ["dish_name"],
    }
    def run(self, dish_name: str = "") -> str:
        from tools.masterchef import start_recipe
        return start_recipe(dish_name)


class NextStepTool(BaseTool):
    name = "next_step"
    description = (
        "Advance to the next step in the current MasterChef recipe. Use when the user says he's done "
        "with the current step -- 'yes chef', 'done', 'next', 'what's next', 'ready'."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.masterchef import next_step
        return next_step()


class GetTechniqueVideoTool(BaseTool):
    name = "get_technique_video"
    description = (
        "Find a video demonstrating a specific cooking technique. Use when a MasterChef step "
        "references a named technique, or the user asks how to do something."
    )
    input_schema = {
        "type": "object",
        "properties": {"technique": {"type": "string", "description": "Technique name, e.g. 'julienne knife technique'."}},
        "required": ["technique"],
    }
    def run(self, technique: str = "") -> str:
        from tools.masterchef import get_technique_video
        return get_technique_video(technique)


class GordonCritiqueTool(BaseTool):
    name = "gordon_critique"
    description = (
        "Get Gordon Ramsay's diagnosis of what's going wrong mid-cook. the user describes a problem "
        "and Gordon gives a specific technical fix, in character."
    )
    input_schema = {
        "type": "object",
        "properties": {"problem": {"type": "string", "description": "What the user describes going wrong."}},
        "required": ["problem"],
    }
    def run(self, problem: str = "") -> str:
        from tools.masterchef import gordon_critique
        return gordon_critique(problem)


class GetCurrentStepTool(BaseTool):
    name = "get_current_step"
    description = "Repeat the current MasterChef cooking step. Use when the user asks 'what was that again', 'say that again', 'repeat'."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.masterchef import get_current_step
        return get_current_step()


class GetFullRecipeTool(BaseTool):
    name = "get_full_recipe"
    description = "Get the complete MasterChef recipe for a dish before starting. Use when the user wants to review before cooking."
    input_schema = {
        "type": "object",
        "properties": {"dish_name": {"type": "string", "description": "The dish to look up."}},
        "required": ["dish_name"],
    }
    def run(self, dish_name: str = "") -> str:
        from tools.masterchef import get_full_recipe
        return get_full_recipe(dish_name)


class ListDishesTool(BaseTool):
    name = "list_dishes"
    description = "List available MasterChef dishes for a cuisine, noting which have a full recipe ready vs. which are ideas only."
    input_schema = {
        "type": "object",
        "properties": {"cuisine": {"type": "string", "description": "mexican/italian/chinese/thai/desserts_baking. Omit to list all cuisines."}},
        "required": [],
    }
    def run(self, cuisine: str = "") -> str:
        from tools.masterchef import list_dishes
        return list_dishes(cuisine)


class StartMetronomeTool(BaseTool):
    name = "start_metronome"
    description = "Start the Whiplash tap-sync metronome at a given BPM. Use when the user wants to practice with a click track."
    input_schema = {
        "type": "object",
        "properties": {"bpm": {"type": "integer", "description": "Beats per minute, e.g. 100."}},
        "required": [],
    }
    def run(self, bpm: int = 100) -> str:
        from tools.whiplash import start_metronome
        return start_metronome(bpm=bpm)


class StopMetronomeTool(BaseTool):
    name = "stop_metronome"
    description = "Stop the Whiplash metronome."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.whiplash import stop_metronome
        return stop_metronome()


class SyncMetronomeTool(BaseTool):
    name = "sync_metronome"
    description = "Tap-to-sync the Whiplash metronome grid to right now. Call this the exact instant the user says beat one has landed."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.whiplash import sync_metronome
        return sync_metronome()


class SetTempoTool(BaseTool):
    name = "set_tempo"
    description = "Change the Whiplash metronome's tempo without resetting the sync point."
    input_schema = {
        "type": "object",
        "properties": {"bpm": {"type": "integer", "description": "New beats per minute."}},
        "required": ["bpm"],
    }
    def run(self, bpm: int = 100) -> str:
        from tools.whiplash import set_tempo
        return set_tempo(bpm=bpm)


class GetMidiStatusTool(BaseTool):
    name = "get_midi_status"
    description = "Check whether a MIDI drum kit is connected for Whiplash mode and how many hits have been recorded."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.whiplash import get_midi_status
        return get_midi_status()


class GetTimingStatsTool(BaseTool):
    name = "get_timing_stats"
    description = "Get Fletcher's read on the user's recent kick/snare timing against the synced metronome grid."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.whiplash import get_timing_stats
        return get_timing_stats()


class ListGroovesTool(BaseTool):
    name = "list_grooves"
    description = "List the funk grooves available to practice in Whiplash mode."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.whiplash import list_grooves
        return list_grooves()


class GetGrooveInfoTool(BaseTool):
    name = "get_groove_info"
    description = "Get the full teaching breakdown of a named funk groove without starting practice mode."
    input_schema = {
        "type": "object",
        "properties": {"groove": {"type": "string", "description": "Groove name, e.g. 'Funky Drummer' or 'purdie_shuffle'."}},
        "required": ["groove"],
    }
    def run(self, groove: str = "") -> str:
        from tools.whiplash import get_groove_info
        return get_groove_info(groove)


class StartGroovePracticeTool(BaseTool):
    name = "start_groove_practice"
    description = "Begin practicing a named funk groove -- sets tempo, syncs the metronome grid, and clears prior MIDI hits."
    input_schema = {
        "type": "object",
        "properties": {"groove": {"type": "string", "description": "Groove name, e.g. 'The Pocket', 'Cold Sweat', 'Rosanna Shuffle'."}},
        "required": ["groove"],
    }
    def run(self, groove: str = "") -> str:
        from tools.whiplash import start_groove_practice
        return start_groove_practice(groove)


class FletcherCritiqueTool(BaseTool):
    name = "fletcher_critique"
    description = "Get Fletcher's diagnosis of what's going wrong while practicing drums. Use when the user describes a problem mid-practice."
    input_schema = {
        "type": "object",
        "properties": {"problem": {"type": "string", "description": "What the user says is going wrong, in his own words."}},
        "required": ["problem"],
    }
    def run(self, problem: str = "") -> str:
        from tools.whiplash import fletcher_critique
        return fletcher_critique(problem)


class GenerateBBCandidatesTool(BaseTool):
    name = "generate_bb_candidates"
    description = "Generate 20 music video candidates for Beavis and Butthead mode. Mix of videos they'd love, hate, and be confused by. Call at the start of a new session."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.beavis_butthead import generate_video_candidates
        return generate_video_candidates()


class SelectBBVideosTool(BaseTool):
    name = "select_bb_videos"
    description = "Parse the user's video selection and set up the Beavis and Butthead session playlist."
    input_schema = {
        "type": "object",
        "properties": {"selection": {"type": "string", "description": "e.g. '1, 7, 12, 15, 19' or 'surprise me'."}},
        "required": ["selection"],
    }
    def run(self, selection: str = "") -> str:
        from tools.beavis_butthead import select_videos
        return select_videos(selection)


class StartBBVideoTool(BaseTool):
    name = "start_bb_video"
    description = "Start playing the current Beavis and Butthead video and generate opening commentary. Call when a video begins playing."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.beavis_butthead import start_video
        return start_video()


class ReactToBBVideoTool(BaseTool):
    name = "react_to_bb_video"
    description = "Generate mid-video Beavis/Butthead commentary. Call periodically during playback or when something notable happens on screen."
    input_schema = {
        "type": "object",
        "properties": {"moment": {"type": "string", "description": "Optional description of what's on screen right now."}},
        "required": [],
    }
    def run(self, moment: str = "") -> str:
        from tools.beavis_butthead import react_to_video
        return react_to_video(moment)


class BBUserCommentTool(BaseTool):
    name = "bb_user_comment"
    description = "Q2 reacts to the user's Beavis or Butthead comment, creating the back-and-forth conversation dynamic. Call when the user says something in character."
    input_schema = {
        "type": "object",
        "properties": {"comment": {"type": "string", "description": "What the user said, in character."}},
        "required": ["comment"],
    }
    def run(self, comment: str = "") -> str:
        from tools.beavis_butthead import user_comment
        return user_comment(comment)


class BBVideoEndTool(BaseTool):
    name = "bb_video_end"
    description = "Generate end-of-video Beavis/Butthead commentary and rating. Call when a video finishes."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.beavis_butthead import video_end_commentary
        return video_end_commentary()


class NextBBVideoTool(BaseTool):
    name = "next_bb_video"
    description = "Advance to the next video in the Beavis and Butthead session."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.beavis_butthead import next_video
        return next_video()


class ToggleBBNiceGuyTool(BaseTool):
    name = "toggle_bb_nice_guy"
    description = "Toggle Nice Guy mode on/off for Beavis and Butthead mode -- flips to sincere, positive commentary, or back to the classic voice."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.beavis_butthead import toggle_nice_guy
        return toggle_nice_guy()


class SwapBBCharactersTool(BaseTool):
    name = "swap_bb_characters"
    description = "Flip which character Q2 plays in Beavis and Butthead mode (Butthead <-> Beavis)."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.beavis_butthead import swap_characters
        return swap_characters()


class SetBBReplayTool(BaseTool):
    name = "set_bb_replay"
    description = "Mark the current Beavis and Butthead video as replay-allowed or not. Use when the user says 'add this to replay' or 'this one again'."
    input_schema = {
        "type": "object",
        "properties": {"allowed": {"type": "boolean", "description": "True to add to the replay list, false to exclude it."}},
        "required": [],
    }
    def run(self, allowed: bool = True) -> str:
        from tools.beavis_butthead import set_replay
        return set_replay(allowed)


class GetBBReplayListTool(BaseTool):
    name = "get_bb_replay_list"
    description = "Get the list of Beavis and Butthead videos marked as replay-OK across sessions."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.beavis_butthead import get_replay_list
        return get_replay_list()


class BBSessionSummaryTool(BaseTool):
    name = "bb_session_summary"
    description = "Get a summary of the current or just-completed Beavis and Butthead session."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.beavis_butthead import get_session_summary
        return get_session_summary()


class SearchComponentsTool(BaseTool):
    name = "search_components"
    description = ("Search the electronics component database. Find Arduino, ESP32, Raspberry Pi boards, "
                    "sensors, actuators, displays, and passive components.")
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search term."},
            "category": {"type": "string", "enum": ["board", "sensor", "actuator", "passive", "display", "power", "communication"],
                         "description": "Optional category filter."},
        },
        "required": ["query"],
    }
    def run(self, query: str = "", category: str = "") -> str:
        from tools.circuit_builder import search_components_tool
        return search_components_tool(query, category)


class GetComponentDetailTool(BaseTool):
    name = "get_component_detail"
    description = "Get full details about a specific component including all pins, voltage requirements, and usage notes."
    input_schema = {
        "type": "object",
        "properties": {"component_id": {"type": "string", "description": "e.g. 'arduino_uno', 'esp32_devkit', 'dht22'."}},
        "required": ["component_id"],
    }
    def run(self, component_id: str = "") -> str:
        from tools.circuit_builder import get_component_detail
        return get_component_detail(component_id)


class DesignCircuitTool(BaseTool):
    name = "design_circuit"
    description = ("Start designing a circuit project. Returns component database context for Q2 to use "
                    "when generating a complete circuit descriptor. Call before generating circuit JSON.")
    input_schema = {
        "type": "object",
        "properties": {
            "project_description": {"type": "string", "description": "What the user wants to build."},
            "board_id": {"type": "string", "description": "Preferred board if specified."},
            "components_have": {"type": "array", "items": {"type": "string"}, "description": "Components the user already has."},
            "language": {"type": "string", "enum": ["arduino_cpp", "micropython", "circuitpython"]},
        },
        "required": ["project_description"],
    }
    def run(self, project_description: str = "", board_id: str = "", components_have: list = None, language: str = "arduino_cpp") -> str:
        from tools.circuit_builder import design_circuit
        return design_circuit(project_description, board_id, components_have, language)


class CreateProjectFromJSONTool(BaseTool):
    name = "create_project_from_json"
    description = ("Parse circuit descriptor JSON and save as a project. The diagram immediately appears "
                    "in the HUD. circuit_json must include components, connections, code, bom, and build_steps.")
    input_schema = {
        "type": "object",
        "properties": {"circuit_json": {"type": "string", "description": "Complete circuit JSON."}},
        "required": ["circuit_json"],
    }
    def run(self, circuit_json: str = "") -> str:
        from tools.circuit_builder import create_project_from_json
        return create_project_from_json(circuit_json)


class GetProjectCodeTool(BaseTool):
    name = "get_project_code"
    description = "Get the generated code for the active or a specific circuit project."
    input_schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string", "description": "Optional -- defaults to the active project."}},
        "required": [],
    }
    def run(self, project_id: str = "") -> str:
        from tools.circuit_builder import get_project_code
        return get_project_code(project_id)


class ListCircuitProjectsTool(BaseTool):
    name = "list_circuit_projects"
    description = "List all saved circuit projects."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.circuit_builder import list_projects_tool
        return list_projects_tool()


class LoadCircuitProjectTool(BaseTool):
    name = "load_circuit_project"
    description = "Load a saved circuit project as the active project. Displays it in the HUD Circuit Builder tab."
    input_schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string", "description": "Saved project ID."}},
        "required": ["project_id"],
    }
    def run(self, project_id: str = "") -> str:
        from tools.circuit_builder import load_project
        return load_project(project_id)


class GetCircuitBOMTool(BaseTool):
    name = "get_circuit_bom"
    description = "Get the bill of materials for the active (or a specific) circuit project."
    input_schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string", "description": "Optional -- defaults to the active project."}},
        "required": [],
    }
    def run(self, project_id: str = "") -> str:
        from tools.circuit_builder import get_bom
        return get_bom(project_id)


class ExplainBuildStepsTool(BaseTool):
    name = "explain_build_steps"
    description = "Walk through the build steps for the active project. Includes warnings and library installation steps."
    input_schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string", "description": "Optional -- defaults to the active project."}},
        "required": [],
    }
    def run(self, project_id: str = "") -> str:
        from tools.circuit_builder import explain_build_steps
        return explain_build_steps(project_id)


class CheckCircuitCompatibilityTool(BaseTool):
    name = "check_circuit_compatibility"
    description = ("Check voltage and interface compatibility between a board and a list of components. "
                    "Returns warnings about 5V/3.3V mismatches, etc.")
    input_schema = {
        "type": "object",
        "properties": {
            "board_id": {"type": "string", "description": "Board component ID, e.g. 'esp32_devkit'."},
            "component_ids": {"type": "array", "items": {"type": "string"}, "description": "Component IDs to check."},
        },
        "required": ["board_id", "component_ids"],
    }
    def run(self, board_id: str = "", component_ids: list = None) -> str:
        from tools.circuit_builder import check_compatibility
        return check_compatibility(board_id, component_ids or [])


class GetEDStatusTool(BaseTool):
    name = "get_ed_status"
    description = (
        "Get Elite Dangerous ship and session status. Use during ED gameplay "
        "for ship state, location, fuel, recent events. fields: summary/location/"
        "ship/fuel/cargo/status/events/session/all."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "fields": {
                "type": "string",
                "enum": ["summary", "location", "ship", "fuel", "cargo", "status", "events", "session", "all"],
                "description": "Which status fields to return.",
            },
        },
        "required": [],
    }
    def run(self, fields: str = "summary") -> str:
        from tools.ship_computer import get_ed_status
        return get_ed_status(fields=fields)


class SearchGalaxyTool(BaseTool):
    name = "search_galaxy"
    description = (
        "Search INARA and EDSM for Elite Dangerous galaxy data. Use when the "
        "commander asks about systems, stations, commodities, engineers, ships, "
        "materials, or trade routes. Natural language query."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language galaxy query, e.g. 'where can I sell void opals'."},
        },
        "required": ["query"],
    }
    def run(self, query: str = "") -> str:
        from tools.ship_computer import search_galaxy
        return search_galaxy(query=query)


class ProcessPasteTool(BaseTool):
    name = "process_ed_paste"
    description = (
        "Process text pasted from the Elite Dangerous game UI. The commander pastes "
        "station names, system info, mission text, market data, or scan results. "
        "Q2 interprets and responds."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The raw pasted text from the game."},
        },
        "required": ["text"],
    }
    def run(self, text: str = "") -> str:
        from tools.ship_computer import process_paste
        return process_paste(text=text)


class GetEDAlertTool(BaseTool):
    name = "ed_alert"
    description = (
        "Check for Elite Dangerous proactive alerts: fuel low, under attack, "
        "interdiction, overheating, shields down, valuable scan detected."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.ship_computer import ed_alert
        return ed_alert()


class StartGameSessionTool(BaseTool):
    name = "start_game_session"
    description = (
        "Start a Game Companion session for a specific game. Call as soon as the user says he's "
        "playing a game or wants help with one."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "game_name": {"type": "string", "description": "The game he's playing, e.g. 'Elden Ring'."},
            "platform": {"type": "string", "description": "PC/PS5/Xbox/Switch, if mentioned."},
            "character_info": {"type": "string", "description": "Class/build/level info, if mentioned."},
            "spoiler_level": {"type": "string", "enum": ["ask", "minimal", "full"], "description": "Defaults to 'ask'."},
        },
        "required": ["game_name"],
    }
    def run(self, game_name: str = "", platform: str = "", character_info: str = "", spoiler_level: str = "ask") -> str:
        from tools.game_companion import start_game_session
        return start_game_session(game_name, platform=platform, character_info=character_info, spoiler_level=spoiler_level)


class GetGameSessionTool(BaseTool):
    name = "get_game_session"
    description = (
        "Get the current game session context -- game name, build, current area, progress notes. "
        "Call at the start of any game-related response."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.game_companion import get_game_session
        return get_game_session()


class UpdateGameSessionTool(BaseTool):
    name = "update_game_session"
    description = (
        "Update the game session with new info the user shares -- level, area, character, or what "
        "he's already tried. All parameters optional, only pass what changed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "progress_note": {"type": "string", "description": "A milestone reached, e.g. 'Defeated Godrick'."},
            "current_area": {"type": "string", "description": "Where he currently is in the game."},
            "character_info": {"type": "string", "description": "Class/build/level info."},
            "stuck_on": {"type": "string", "description": "What he's currently stuck on."},
            "tried": {"type": "string", "description": "Something he already tried for the current problem."},
            "spoiler_level": {"type": "string", "enum": ["ask", "minimal", "full"]},
        },
        "required": [],
    }
    def run(self, progress_note: str = "", current_area: str = "", character_info: str = "",
            stuck_on: str = "", tried: str = "", spoiler_level: str = "") -> str:
        from tools.game_companion import update_game_session
        return update_game_session(progress_note=progress_note, current_area=current_area,
                                    character_info=character_info, stuck_on=stuck_on,
                                    tried=tried, spoiler_level=spoiler_level)


class GetBossHelpTool(BaseTool):
    name = "get_boss_help"
    description = (
        "Get help with a specific boss, enemy, or difficult encounter. Formats context for web "
        "search and advice. Use when the user says he's stuck on a boss or enemy."
    )
    input_schema = {
        "type": "object",
        "properties": {"boss_name": {"type": "string", "description": "Name of the boss/enemy/encounter."}},
        "required": ["boss_name"],
    }
    def run(self, boss_name: str = "") -> str:
        from tools.game_companion import get_boss_help
        return get_boss_help(boss_name)


class GetBuildAdviceTool(BaseTool):
    name = "get_build_advice"
    description = (
        "Get build, loadout, or character optimization advice for the current game. Formats "
        "context for a current-meta web search."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "playstyle": {"type": "string", "description": "e.g. aggressive/stealth/ranged/support."},
            "constraints": {"type": "string", "description": "e.g. no spoilers/early game/specific weapon."},
        },
        "required": [],
    }
    def run(self, playstyle: str = "", constraints: str = "") -> str:
        from tools.game_companion import get_build_advice
        return get_build_advice(playstyle=playstyle, constraints=constraints)


class GetProgressionHintTool(BaseTool):
    name = "get_progression_hint"
    description = (
        "Help the user figure out where to go or what to do next. Respects his spoiler "
        "preference. Use when he says he's lost or doesn't know what to do next."
    )
    input_schema = {
        "type": "object",
        "properties": {"vague_first": {"type": "boolean", "description": "Give a vague hint before specifics. Default true."}},
        "required": [],
    }
    def run(self, vague_first: bool = True) -> str:
        from tools.game_companion import get_progression_hint
        return get_progression_hint(vague_first=vague_first)


class SearchGameKnowledgeTool(BaseTool):
    name = "search_game_knowledge"
    description = (
        "Format a game-specific knowledge query for web search -- mechanics, item locations, "
        "quest details, or anything game-specific. Always web search after calling this."
    )
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to look up."}},
        "required": ["query"],
    }
    def run(self, query: str = "") -> str:
        from tools.game_companion import search_game_knowledge
        return search_game_knowledge(query)


class EndGameSessionTool(BaseTool):
    name = "end_game_session"
    description = "End the current Game Companion session and save it to history. Use when the user is done playing or switching games."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.game_companion import end_game_session
        return end_game_session()


class ListRecentGamesTool(BaseTool):
    name = "list_recent_games"
    description = "List recently played games from Game Companion session history."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.game_companion import list_recent_games
        return list_recent_games()


class GetGameInfoTool(BaseTool):
    name = "get_game_info"
    description = "Get database info about a game -- developer, genre, key systems, wiki link. Uses the current session's game if none specified."
    input_schema = {
        "type": "object",
        "properties": {"game_name": {"type": "string", "description": "Optional -- uses current session if omitted."}},
        "required": [],
    }
    def run(self, game_name: str = "") -> str:
        from tools.game_companion import get_game_info
        return get_game_info(game_name)


class GenerateGameTool(BaseTool):
    name = "generate_game"
    description = (
        "Get the question-generation prompt for 'Who Wants a Hundred Bucks?' trivia game show mode. "
        "Call this first, generate 15 questions per the prompt's instructions, then call start_game "
        "with the resulting JSON. difficulty: 1 (easiest) to 5 (hardest), default 3."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "difficulty": {"type": "integer", "description": "1 (easiest) to 5 (hardest). Default 3."},
        },
        "required": [],
    }
    def run(self, difficulty: int = 3) -> str:
        from tools.game_show import generate_game
        return generate_game(difficulty)


class StartGameTool(BaseTool):
    name = "start_game"
    description = (
        "Start a 'Who Wants a Hundred Bucks?' game with pre-generated questions. Call generate_game "
        "first to get the question prompt, generate 15 questions as JSON per its instructions, then "
        "pass that JSON here. The game screen appears on the kiosk automatically."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "difficulty": {"type": "integer", "description": "1 (easiest) to 5 (hardest) -- must match the value passed to generate_game."},
            "questions_json": {"type": "string", "description": "The JSON array of 15 generated questions."},
        },
        "required": ["difficulty", "questions_json"],
    }
    def run(self, difficulty: int = 3, questions_json: str = "") -> str:
        from tools.game_show import start_game
        return start_game(difficulty, questions_json)


class AnswerQuestionTool(BaseTool):
    name = "answer_question"
    description = (
        "Submit an answer for the current 'Who Wants a Hundred Bucks?' question. answer: A, B, C, or "
        "D. Only call after the player has confirmed it's their final answer."
    )
    input_schema = {
        "type": "object",
        "properties": {"answer": {"type": "string", "enum": ["A", "B", "C", "D"]}},
        "required": ["answer"],
    }
    def run(self, answer: str = "") -> str:
        from tools.game_show import answer_question
        return answer_question(answer)


class UseLifelineTool(BaseTool):
    name = "use_lifeline"
    description = "Use a 'Who Wants a Hundred Bucks?' lifeline. lifeline: fifty_fifty, phone_friend, or ask_audience."
    input_schema = {
        "type": "object",
        "properties": {"lifeline": {"type": "string", "enum": ["fifty_fifty", "phone_friend", "ask_audience"]}},
        "required": ["lifeline"],
    }
    def run(self, lifeline: str = "") -> str:
        from tools.game_show import use_lifeline
        return use_lifeline(lifeline)


class WalkAwayGameShowTool(BaseTool):
    name = "walk_away_game_show"
    description = "Player walks away from 'Who Wants a Hundred Bucks?' with their safe-haven amount, ending the game."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.game_show import walk_away
        return walk_away()


class GetGameShowStateTool(BaseTool):
    name = "get_game_show_state"
    description = "Get the current 'Who Wants a Hundred Bucks?' state -- question, answers, lifelines, current value."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def run(self) -> str:
        from tools.game_show import get_game_state
        return get_game_state()


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._register_defaults()

    def _register_defaults(self):
        for tool_cls in [WebSearchTool, WeatherTool, DefinitionTool, TranslateTool, BrowserDisplayTool, FindProductTool, OpenSettingsTool, RCTelemetryTool, ShinLinkNetworkStatusTool, ShinLinkControlTool, GetTokenStatsTool, SendEmailTool, ReadEmailTool, ExecutePurchaseTool, BodyControlTool, YouTubeMusicTool, GoogleDriveTool, GoogleSheetsTool, GoogleDocsTool, GoogleCalendarTool, CaptureImageTool2, ShowPhotoTool, AnalyzePhotoTool, GitTool, RaceEngineerTool, RaceEngineerStatusTool, ACRaceEngineerTool, ACRaceEngineerStatusTool, GetDrivingVibeTool, MarkLocationTool, WhereAreWeTool, ListLocationsTool, RemoveLocationTool, ListNearbyLandmarksTool, GetLocationCalloutInfoTool, ImportLocationMapTool, ExportPersonalMapTool, GetDriftStatsTool, GetRaceStatusTool, GetFlightStatusTool, FirstOfficerStatusTool, GetF1StatusTool, GetF1DriverTool, F1RaceAlertTool, GetReplayLapTool, GetReplayStatusTool, ListF1RacesTool, StartF1ReplaySessionTool, SwitchAgentModeTool, GetUFCStatusTool, GetUFCFightTool, UFCPrefightBriefTool, PopulateUFCEventTool, StartUFCReplayFightTool, GetUFCRoundTool, GetUFCScorecardTool, ListUFCEventsTool, SearchUFCEventTool, GetNBAStatusTool, GetNBAReplayTool, NBAAlertTool, GetNHLStatusTool, GetNHLReplayTool, NHLAlertTool, GetNFLStatusTool, GetNFLReplayTool, NFLAlertTool, GetMLBStatusTool, GetMLBReplayTool, MLBAlertTool, GenerateDJSetTool, StartDJPresetTool, DJStatusTool, DJSkipTool, DJStopTool, DJTrackInfoTool, ListDJPresetsTool, GetFDStandingsTool, GetFDRoundTool, GetXGamesResultsTool, PlanMealTool, BuildShoppingListTool, StartRecipeTool, NextStepTool, GetTechniqueVideoTool, GordonCritiqueTool, GetCurrentStepTool, GetFullRecipeTool, ListDishesTool, StartMetronomeTool, StopMetronomeTool, SyncMetronomeTool, SetTempoTool, GetMidiStatusTool, GetTimingStatsTool, ListGroovesTool, GetGrooveInfoTool, StartGroovePracticeTool, FletcherCritiqueTool, GenerateBBCandidatesTool, SelectBBVideosTool, StartBBVideoTool, ReactToBBVideoTool, BBUserCommentTool, BBVideoEndTool, NextBBVideoTool, ToggleBBNiceGuyTool, SwapBBCharactersTool, SetBBReplayTool, GetBBReplayListTool, BBSessionSummaryTool, SearchComponentsTool, GetComponentDetailTool, DesignCircuitTool, CreateProjectFromJSONTool, GetProjectCodeTool, ListCircuitProjectsTool, LoadCircuitProjectTool, GetCircuitBOMTool, ExplainBuildStepsTool, CheckCircuitCompatibilityTool, GetEDStatusTool, SearchGalaxyTool, ProcessPasteTool, GetEDAlertTool, ControlAircraftTool, EngageAutopilotTool, ApproachChecklistTool, EmergencySquawkTool, GenerateACCSetupTool, ListACCSetupsTool, ApplyACCSetupTool, DeleteACCSetupTool, GeneratePopupsTool, GetPopupTool, GetNextPopupsTool, ListPopupTitlesTool, SetPopupTitleTool, ClearPopupSessionTool, StartGameSessionTool, GetGameSessionTool, UpdateGameSessionTool, GetBossHelpTool, GetBuildAdviceTool, GetProgressionHintTool, SearchGameKnowledgeTool, EndGameSessionTool, ListRecentGamesTool, GetGameInfoTool, GenerateGameTool, StartGameTool, AnswerQuestionTool, UseLifelineTool, WalkAwayGameShowTool, GetGameShowStateTool]:
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
