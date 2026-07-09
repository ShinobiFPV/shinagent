"""
ShinAgent Retro Gaming Module
=============================
Manages RetroArch emulation and Q2's virtual Player 2 controller.
Windows only -- requires RetroArch and ViGEmBus driver installed.

Setup:
    1. Install RetroArch from retroarch.com
    2. pip install vgamepad
    3. ViGEmBus driver installs automatically with vgamepad
    4. Enable Network Commands in RetroArch:
       Settings > Network > Network Commands: ON
       Network Command Port: 55355
"""

import socket
import time
import subprocess
import sys
from pathlib import Path
from typing import Optional

IS_WINDOWS = sys.platform == 'win32'

# ── RetroArch cores for each system ──────────────────────────────

CORES = {
    'nes': {
        'name': 'Nintendo Entertainment System',
        'core': 'mesen_libretro.dll',      # primary
        'core_alt': 'fceumm_libretro.dll', # fallback
        'extensions': ['.nes', '.zip', '.7z'],
        'buttons': ['A', 'B', 'START', 'SELECT', 'UP', 'DOWN', 'LEFT', 'RIGHT'],
    },
    'snes': {
        'name': 'Super Nintendo',
        'core': 'snes9x_libretro.dll',
        'core_alt': 'bsnes_libretro.dll',
        'extensions': ['.snes', '.smc', '.sfc', '.zip', '.7z'],
        'buttons': ['A', 'B', 'X', 'Y', 'L', 'R', 'START', 'SELECT',
                    'UP', 'DOWN', 'LEFT', 'RIGHT'],
    },
    'genesis': {
        'name': 'Sega Genesis / Mega Drive',
        'core': 'genesis_plus_gx_libretro.dll',
        'core_alt': 'picodrive_libretro.dll',
        'extensions': ['.md', '.gen', '.smd', '.bin', '.zip', '.7z'],
        'buttons': ['A', 'B', 'C', 'X', 'Y', 'Z', 'START', 'MODE',
                    'UP', 'DOWN', 'LEFT', 'RIGHT'],
    },
}

# RetroArch button mapping to vgamepad XBox360 buttons
# RetroArch RetroPad -> XBox360
RETROPAD_TO_XBOX = {
    'B':      'XUSB_GAMEPAD_A',           # B on RetroPad = A on Xbox
    'A':      'XUSB_GAMEPAD_B',           # A on RetroPad = B on Xbox
    'Y':      'XUSB_GAMEPAD_X',
    'X':      'XUSB_GAMEPAD_Y',
    'L':      'XUSB_GAMEPAD_LEFT_SHOULDER',
    'R':      'XUSB_GAMEPAD_RIGHT_SHOULDER',
    'SELECT': 'XUSB_GAMEPAD_BACK',
    'START':  'XUSB_GAMEPAD_START',
    'UP':     'XUSB_GAMEPAD_DPAD_UP',
    'DOWN':   'XUSB_GAMEPAD_DPAD_DOWN',
    'LEFT':   'XUSB_GAMEPAD_DPAD_LEFT',
    'RIGHT':  'XUSB_GAMEPAD_DPAD_RIGHT',
}


class RetroArchController:
    """
    Controls RetroArch via UDP Network Control Interface.
    Sends commands and reads RAM.
    """

    def __init__(self, host='127.0.0.1', port=55355):
        self._host = host
        self._port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(1.0)

    def send(self, command: str) -> Optional[str]:
        """Send a command and optionally receive response."""
        try:
            self._sock.sendto(
                command.encode(), (self._host, self._port))
            try:
                data, _ = self._sock.recvfrom(4096)
                return data.decode().strip()
            except socket.timeout:
                return None
        except Exception:
            return None

    def get_status(self) -> dict:
        """Get current RetroArch status."""
        resp = self.send('GET_STATUS')
        if not resp:
            return {'connected': False}

        # Response: GET_STATUS PLAYING system_id,game,crc32=...
        #        or GET_STATUS CONTENTLESS
        parts = resp.split(' ', 2)
        if len(parts) < 2:
            return {'connected': True, 'status': 'unknown'}

        status = parts[1]  # PLAYING, PAUSED, CONTENTLESS
        result = {'connected': True, 'status': status}

        if len(parts) > 2:
            content = parts[2]
            content_parts = content.split(',')
            if content_parts:
                result['system'] = content_parts[0]
                if len(content_parts) > 1:
                    result['game'] = content_parts[1]

        return result

    def is_connected(self) -> bool:
        status = self.get_status()
        return status.get('connected', False)

    def is_playing(self) -> bool:
        status = self.get_status()
        return status.get('status') in ('PLAYING', 'PAUSED')

    def pause(self):
        self.send('PAUSE_TOGGLE')

    def unpause(self):
        self.send('PAUSE_TOGGLE')

    def save_state(self):
        self.send('SAVE_STATE')

    def load_state(self):
        self.send('LOAD_STATE')

    def reset(self):
        self.send('RESET')

    def read_ram(self, address: int, num_bytes: int) -> Optional[list]:
        """
        Read bytes from core RAM.
        Returns list of integers or None on failure.
        Address in hex (passed as int, converted internally).
        """
        resp = self.send(f'READ_CORE_RAM {address:x} {num_bytes}')
        if not resp or (resp.startswith('READ_CORE_RAM') and '-1' in resp):
            return None

        # Response: READ_CORE_RAM <addr> <byte1> <byte2> ...
        parts = resp.split()
        if len(parts) < 3:
            return None

        try:
            return [int(b, 16) for b in parts[2:]]
        except ValueError:
            return None

    def write_ram(self, address: int, *bytes_values: int) -> bool:
        """Write bytes to core RAM."""
        hex_bytes = ' '.join(f'{b:02x}' for b in bytes_values)
        resp = self.send(f'WRITE_CORE_RAM {address:x} {hex_bytes}')
        return resp is not None and '-1' not in resp


class VirtualP2Controller:
    """
    Creates a virtual Xbox360 gamepad that acts as Player 2.
    Requires vgamepad and ViGEmBus driver on Windows.
    """

    def __init__(self):
        self._gamepad = None
        self._available = False
        self._held_buttons = set()
        self._init()

    def _init(self):
        if not IS_WINDOWS:
            return
        try:
            import vgamepad as vg
            self._vg = vg
            self._gamepad = vg.VX360Gamepad()
            self._available = True
        except ImportError:
            pass
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self._available

    def press(self, button: str, duration_ms: int = 100):
        """Press a button for duration_ms milliseconds."""
        if not self._available:
            return

        xbox_btn = RETROPAD_TO_XBOX.get(button.upper())
        if not xbox_btn:
            return

        vg_button = getattr(self._vg.XUSB_BUTTON, xbox_btn, None)
        if not vg_button:
            return

        self._gamepad.press_button(button=vg_button)
        self._gamepad.update()
        time.sleep(duration_ms / 1000)
        self._gamepad.release_button(button=vg_button)
        self._gamepad.update()

    def hold(self, button: str):
        """Hold a button until release() is called."""
        if not self._available:
            return
        xbox_btn = RETROPAD_TO_XBOX.get(button.upper())
        if not xbox_btn:
            return
        vg_button = getattr(self._vg.XUSB_BUTTON, xbox_btn, None)
        if vg_button:
            self._gamepad.press_button(button=vg_button)
            self._gamepad.update()
            self._held_buttons.add(button.upper())

    def release(self, button: str):
        """Release a held button."""
        if not self._available:
            return
        xbox_btn = RETROPAD_TO_XBOX.get(button.upper())
        if not xbox_btn:
            return
        vg_button = getattr(self._vg.XUSB_BUTTON, xbox_btn, None)
        if vg_button:
            self._gamepad.release_button(button=vg_button)
            self._gamepad.update()
            self._held_buttons.discard(button.upper())

    def release_all(self):
        """Release all held buttons."""
        for btn in list(self._held_buttons):
            self.release(btn)

    def combo(self, buttons: list, duration_ms: int = 100):
        """Press multiple buttons simultaneously."""
        if not self._available:
            return
        for btn in buttons:
            xbox_btn = RETROPAD_TO_XBOX.get(btn.upper())
            if xbox_btn:
                vg_button = getattr(self._vg.XUSB_BUTTON, xbox_btn, None)
                if vg_button:
                    self._gamepad.press_button(button=vg_button)
        self._gamepad.update()
        time.sleep(duration_ms / 1000)
        for btn in buttons:
            xbox_btn = RETROPAD_TO_XBOX.get(btn.upper())
            if xbox_btn:
                vg_button = getattr(self._vg.XUSB_BUTTON, xbox_btn, None)
                if vg_button:
                    self._gamepad.release_button(button=vg_button)
        self._gamepad.update()

    def set_stick(self, stick: str, x: float, y: float):
        """Set analog stick (-1.0 to 1.0)."""
        if not self._available:
            return
        x_val = int(x * 32767)
        y_val = int(y * 32767)
        if stick == 'left':
            self._gamepad.left_joystick(x_value=x_val, y_value=y_val)
        elif stick == 'right':
            self._gamepad.right_joystick(x_value=x_val, y_value=y_val)
        self._gamepad.update()

    def destroy(self):
        """Release the virtual gamepad."""
        self.release_all()
        self._gamepad = None
        self._available = False


class RetroGameManager:
    """
    Manages the full retro gaming experience:
    - Scans ROM folders
    - Launches games in RetroArch
    - Manages the virtual P2 controller
    - Coordinates with the AI controller
    """

    def __init__(self, retroarch_path: str = None, rom_folder: str = None):
        self._retroarch = self._find_retroarch(retroarch_path)
        self._rom_folder = Path(rom_folder) if rom_folder else self._find_roms()
        self._ra_ctrl = RetroArchController()
        self._p2 = VirtualP2Controller()
        self._process = None
        self._current_game = None
        self._current_system = None
        self._ai_controller = None

    def _find_retroarch(self, explicit_path=None) -> Optional[Path]:
        """Find RetroArch executable."""
        if explicit_path:
            p = Path(explicit_path)
            if p.exists():
                return p

        candidates = [
            Path(r'C:\RetroArch-Win64\retroarch.exe'),
            Path(r'C:\Program Files\RetroArch\retroarch.exe'),
            Path(r'C:\RetroArch\retroarch.exe'),
            Path.home() / 'RetroArch' / 'retroarch.exe',
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def _find_roms(self) -> Optional[Path]:
        """Find ROM folder."""
        candidates = [
            Path.home() / 'ROMs',
            Path.home() / 'roms',
            Path(r'C:\ROMs'),
            Path(r'D:\ROMs'),
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def _find_core(self, system: str) -> Optional[Path]:
        """Find the libretro core for a system."""
        if not self._retroarch:
            return None

        core_info = CORES.get(system, {})
        cores_dir = self._retroarch.parent / 'cores'

        for core_name in [core_info.get('core'), core_info.get('core_alt')]:
            if core_name:
                core_path = cores_dir / core_name
                if core_path.exists():
                    return core_path
        return None

    def scan_roms(self) -> list:
        """Scan ROM folder and return list of game dicts."""
        if not self._rom_folder or not self._rom_folder.exists():
            return []

        games = []
        for system, info in CORES.items():
            for ext in info['extensions']:
                for rom in self._rom_folder.rglob(f'*{ext}'):
                    # Skip bios files
                    if 'bios' in rom.name.lower():
                        continue
                    games.append({
                        'name':   rom.stem,
                        'path':   str(rom),
                        'system': system,
                        'system_name': info['name'],
                        'ext':    rom.suffix,
                    })

        return sorted(games, key=lambda g: (g['system'], g['name'].lower()))

    def launch_game(self, rom_path: str, system: str) -> dict:
        """Launch a game in RetroArch."""
        if not self._retroarch:
            return {'ok': False, 'error': 'RetroArch not found'}

        core = self._find_core(system)
        if not core:
            return {'ok': False,
                    'error': f'Core not found for {system}. '
                             f'Open RetroArch and download the core first.'}

        rom = Path(rom_path)
        if not rom.exists():
            return {'ok': False, 'error': f'ROM not found: {rom_path}'}

        # Kill existing RetroArch if running
        if self._process and self._process.poll() is None:
            self._process.terminate()
            time.sleep(0.5)

        cmd = [
            str(self._retroarch),
            '--libretro', str(core),
            str(rom),
        ]

        try:
            self._process = subprocess.Popen(cmd)
            self._current_game = rom.stem
            self._current_system = system

            # Wait for RetroArch to start and accept network commands
            time.sleep(3.0)

            # Verify connection
            status = self._ra_ctrl.get_status()
            if not status.get('connected'):
                return {
                    'ok': False,
                    'error': 'RetroArch started but network commands not responding. '
                             'Enable: Settings > Network > Network Commands'
                }

            return {
                'ok':     True,
                'game':   self._current_game,
                'system': system,
                'core':   core.stem,
                'p2_available': self._p2.available,
            }

        except Exception as e:
            return {'ok': False, 'error': str(e)}

    @property
    def p2(self) -> VirtualP2Controller:
        return self._p2

    @property
    def ra(self) -> RetroArchController:
        return self._ra_ctrl

    @property
    def current_game(self):
        return self._current_game

    @property
    def current_system(self):
        return self._current_system

    @property
    def ai_controller(self):
        return self._ai_controller

    @ai_controller.setter
    def ai_controller(self, controller):
        self._ai_controller = controller

    def get_status(self) -> dict:
        ra_status = self._ra_ctrl.get_status()
        return {
            'retroarch_found': self._retroarch is not None,
            'retroarch_path':  str(self._retroarch) if self._retroarch else None,
            'roms_found':      self._rom_folder is not None,
            'rom_folder':      str(self._rom_folder) if self._rom_folder else None,
            'p2_available':    self._p2.available,
            'ra_connected':    ra_status.get('connected', False),
            'ra_status':       ra_status.get('status', 'offline'),
            'current_game':    self._current_game,
            'current_system':  self._current_system,
        }

    def close(self):
        """Clean up."""
        if self._ai_controller:
            self._ai_controller.stop()
        self._p2.destroy()
        if self._process and self._process.poll() is None:
            self._process.terminate()


# Singleton
_manager: Optional[RetroGameManager] = None

def get_manager() -> RetroGameManager:
    global _manager
    if _manager is None:
        _manager = RetroGameManager()
    return _manager
