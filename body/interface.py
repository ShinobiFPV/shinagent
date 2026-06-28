"""
IMQ2 Body Interface
Abstract base class for Q2's physical embodiment.
Implement this for each chassis: rover, humanoid, or whatever Q2 chooses next.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class SensorData:
    battery_voltage: Optional[float] = None
    cpu_temp: Optional[float] = None
    imu_roll: Optional[float] = None
    imu_pitch: Optional[float] = None
    imu_yaw: Optional[float] = None
    ultrasonic_front_cm: Optional[float] = None
    camera_active: bool = False
    extra: dict = None


class BodyInterface(ABC):
    """
    All chassis implementations must implement this interface.
    Q2 will use this to interact with its physical form regardless of hardware.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the body hardware. Returns True if successful."""
        ...

    @abstractmethod
    def disconnect(self):
        ...

    @abstractmethod
    def move(self, direction: str, speed: float = 0.5, duration_s: Optional[float] = None):
        """
        direction: 'forward' | 'backward' | 'left' | 'right' | 'stop'
        speed: 0.0 – 1.0
        duration_s: if set, auto-stop after this many seconds
        """
        ...

    @abstractmethod
    def look(self, pan_deg: float = 0.0, tilt_deg: float = 0.0):
        """Pan/tilt camera or head. 0,0 = centre."""
        ...

    @abstractmethod
    def get_sensor_data(self) -> SensorData:
        ...

    @abstractmethod
    def say_ready(self):
        """Hardware startup signal — LED blink, beep, etc."""
        ...


class NullBody(BodyInterface):
    """Placeholder body when no hardware is connected."""

    def connect(self) -> bool:
        return True

    def disconnect(self):
        pass

    def move(self, direction, speed=0.5, duration_s=None):
        print(f"[NullBody] move({direction}, speed={speed})")

    def look(self, pan_deg=0.0, tilt_deg=0.0):
        print(f"[NullBody] look(pan={pan_deg}, tilt={tilt_deg})")

    def get_sensor_data(self) -> SensorData:
        return SensorData()

    def say_ready(self):
        print("[NullBody] Ready.")


class RoverBody(BodyInterface):
    """
    Stub for the first physical rover chassis.
    Wire this up to your motor controller (serial/ROS2/ArduPilot).
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baud: int = 115200):
        self._port = port
        self._baud = baud
        self._serial = None

    def connect(self) -> bool:
        try:
            import serial
            self._serial = serial.Serial(self._port, self._baud, timeout=1)
            return True
        except Exception as e:
            print(f"[RoverBody] connect failed: {e}")
            return False

    def disconnect(self):
        if self._serial:
            self._serial.close()

    def move(self, direction, speed=0.5, duration_s=None):
        cmd = f"MOVE {direction.upper()} {int(speed * 100)}\n"
        if self._serial:
            self._serial.write(cmd.encode())
        # TODO: implement duration_s auto-stop via threading.Timer

    def look(self, pan_deg=0.0, tilt_deg=0.0):
        cmd = f"LOOK {int(pan_deg)} {int(tilt_deg)}\n"
        if self._serial:
            self._serial.write(cmd.encode())

    def get_sensor_data(self) -> SensorData:
        # TODO: query onboard MCU for sensor telemetry
        return SensorData()

    def say_ready(self):
        if self._serial:
            self._serial.write(b"READY\n")

