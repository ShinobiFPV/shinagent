"""
Circuit Builder Component Database
====================================
Boards and components with pin definitions, electrical characteristics,
and SVG rendering data.

Each component has:
  - id:          unique string identifier
  - name:        display name
  - category:    board / sensor / actuator / passive / display /
                 power / communication
  - description: one-liner
  - voltage:     operating voltage(s) -- a float (e.g. 5.0) or a
                 descriptive range string (e.g. "3.3-5V")
  - pins:        list of pin definitions
  - svg_color:   brand/category colour for the HUD diagram
  - notes:       gotchas, warnings, tips

Pin definition:
  - id:      pin identifier (D2, GPIO4, SDA, etc.)
  - name:    friendly name
  - type:    power / ground / digital / analog / pwm / i2c /
             spi / uart / special
  - number:  physical pin number (boards only)
  - voltage: max voltage on this pin, when it differs from the board's
             general logic voltage (e.g. VIN, or a 5V-output ECHO pin)
"""

COMPONENTS = {

    # -- BOARDS --------------------------------------------------------

    "arduino_uno": {
        "id": "arduino_uno",
        "name": "Arduino Uno R3",
        "category": "board",
        "family": "arduino",
        "description": "Classic 5V Arduino. Best for beginners and 5V sensors.",
        "voltage": 5.0,
        "logic": 5.0,
        "flash_kb": 32,
        "ram_kb": 2,
        "clock_mhz": 16,
        "cpu": "ATmega328P",
        "dimensions": {"w": 68.6, "h": 53.4},
        "pins": [
            {"id": "D0", "name": "D0/RX", "type": "digital/uart", "number": 0},
            {"id": "D1", "name": "D1/TX", "type": "digital/uart", "number": 1},
            {"id": "D2", "name": "D2", "type": "digital", "number": 2},
            {"id": "D3", "name": "D3 (PWM)", "type": "pwm", "number": 3},
            {"id": "D4", "name": "D4", "type": "digital", "number": 4},
            {"id": "D5", "name": "D5 (PWM)", "type": "pwm", "number": 5},
            {"id": "D6", "name": "D6 (PWM)", "type": "pwm", "number": 6},
            {"id": "D7", "name": "D7", "type": "digital", "number": 7},
            {"id": "D8", "name": "D8", "type": "digital", "number": 8},
            {"id": "D9", "name": "D9 (PWM)", "type": "pwm", "number": 9},
            {"id": "D10", "name": "D10 (PWM/SS)", "type": "pwm/spi", "number": 10},
            {"id": "D11", "name": "D11 (PWM/MOSI)", "type": "pwm/spi", "number": 11},
            {"id": "D12", "name": "D12 (MISO)", "type": "spi", "number": 12},
            {"id": "D13", "name": "D13 (SCK/LED)", "type": "spi", "number": 13},
            {"id": "A0", "name": "A0", "type": "analog", "number": 14, "adc_bits": 10},
            {"id": "A1", "name": "A1", "type": "analog", "number": 15, "adc_bits": 10},
            {"id": "A2", "name": "A2", "type": "analog", "number": 16, "adc_bits": 10},
            {"id": "A3", "name": "A3", "type": "analog", "number": 17, "adc_bits": 10},
            {"id": "A4", "name": "A4 (SDA)", "type": "analog/i2c", "number": 18},
            {"id": "A5", "name": "A5 (SCL)", "type": "analog/i2c", "number": 19},
            {"id": "5V", "name": "5V", "type": "power", "voltage": 5.0},
            {"id": "3V3", "name": "3.3V", "type": "power", "voltage": 3.3},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "VIN", "name": "VIN", "type": "power", "voltage": "7-12V"},
        ],
        "i2c_pins": {"sda": "A4", "scl": "A5"},
        "spi_pins": {"mosi": "D11", "miso": "D12", "sck": "D13", "ss": "D10"},
        "uart_pins": {"tx": "D1", "rx": "D0"},
        "notes": [
            "5V logic -- use a level shifter with 3.3V sensors",
            "PWM pins: 3, 5, 6, 9, 10, 11",
            "Analog pins A0-A3 also usable as digital I/O",
            "Max 40mA per I/O pin, 200mA total from all pins",
        ],
        "libraries": ["Wire (I2C)", "SPI", "Serial"],
        "svg_color": "#00979d",
    },

    "arduino_nano": {
        "id": "arduino_nano",
        "name": "Arduino Nano",
        "category": "board",
        "family": "arduino",
        "description": "Compact 5V Arduino. Breadboard-friendly form factor.",
        "voltage": 5.0,
        "logic": 5.0,
        "cpu": "ATmega328P",
        "dimensions": {"w": 18.5, "h": 43.2},
        "pins": [
            {"id": "D2", "name": "D2", "type": "digital"},
            {"id": "D3", "name": "D3 (PWM)", "type": "pwm"},
            {"id": "D4", "name": "D4", "type": "digital"},
            {"id": "D5", "name": "D5 (PWM)", "type": "pwm"},
            {"id": "D6", "name": "D6 (PWM)", "type": "pwm"},
            {"id": "D7", "name": "D7", "type": "digital"},
            {"id": "D8", "name": "D8", "type": "digital"},
            {"id": "D9", "name": "D9 (PWM)", "type": "pwm"},
            {"id": "D10", "name": "D10 (PWM)", "type": "pwm/spi"},
            {"id": "D11", "name": "D11 (MOSI)", "type": "spi"},
            {"id": "D12", "name": "D12 (MISO)", "type": "spi"},
            {"id": "D13", "name": "D13 (SCK)", "type": "spi"},
            {"id": "A0", "name": "A0", "type": "analog"},
            {"id": "A1", "name": "A1", "type": "analog"},
            {"id": "A2", "name": "A2", "type": "analog"},
            {"id": "A3", "name": "A3", "type": "analog"},
            {"id": "A4", "name": "A4 (SDA)", "type": "i2c"},
            {"id": "A5", "name": "A5 (SCL)", "type": "i2c"},
            {"id": "A6", "name": "A6", "type": "analog"},
            {"id": "A7", "name": "A7", "type": "analog"},
            {"id": "5V", "name": "5V", "type": "power", "voltage": 5.0},
            {"id": "3V3", "name": "3.3V", "type": "power", "voltage": 3.3},
            {"id": "GND", "name": "GND", "type": "ground"},
        ],
        "i2c_pins": {"sda": "A4", "scl": "A5"},
        "notes": [
            "Same as Uno but breadboard-friendly -- 2x32 pins",
            "5V logic like the Uno",
            "Great for permanent projects",
        ],
        "svg_color": "#00979d",
    },

    "esp32_devkit": {
        "id": "esp32_devkit",
        "name": "ESP32 DevKit V1",
        "category": "board",
        "family": "esp32",
        "description": "WiFi+BT, 3.3V, 38-pin. Most popular ESP32 dev board.",
        "voltage": 3.3,
        "logic": 3.3,
        "flash_kb": 4096,
        "ram_kb": 520,
        "clock_mhz": 240,
        "cpu": "ESP32-WROOM-32",
        "wifi": True,
        "bluetooth": True,
        "dimensions": {"w": 25.4, "h": 48.0},
        "pins": [
            {"id": "GPIO0", "name": "GPIO0 (BOOT)", "type": "digital/special"},
            {"id": "GPIO1", "name": "GPIO1 (TX0)", "type": "uart"},
            {"id": "GPIO2", "name": "GPIO2 (LED)", "type": "digital"},
            {"id": "GPIO3", "name": "GPIO3 (RX0)", "type": "uart"},
            {"id": "GPIO4", "name": "GPIO4", "type": "digital"},
            {"id": "GPIO5", "name": "GPIO5 (SS)", "type": "digital/spi"},
            {"id": "GPIO12", "name": "GPIO12 (MISO)", "type": "spi"},
            {"id": "GPIO13", "name": "GPIO13 (MOSI)", "type": "spi"},
            {"id": "GPIO14", "name": "GPIO14 (SCK)", "type": "spi"},
            {"id": "GPIO15", "name": "GPIO15", "type": "digital"},
            {"id": "GPIO16", "name": "GPIO16", "type": "digital"},
            {"id": "GPIO17", "name": "GPIO17", "type": "digital"},
            {"id": "GPIO18", "name": "GPIO18 (SCK)", "type": "digital/spi"},
            {"id": "GPIO19", "name": "GPIO19 (MISO)", "type": "digital/spi"},
            {"id": "GPIO21", "name": "GPIO21 (SDA)", "type": "i2c"},
            {"id": "GPIO22", "name": "GPIO22 (SCL)", "type": "i2c"},
            {"id": "GPIO23", "name": "GPIO23 (MOSI)", "type": "digital/spi"},
            {"id": "GPIO25", "name": "GPIO25 (DAC1)", "type": "analog/dac"},
            {"id": "GPIO26", "name": "GPIO26 (DAC2)", "type": "analog/dac"},
            {"id": "GPIO27", "name": "GPIO27", "type": "digital"},
            {"id": "GPIO32", "name": "GPIO32", "type": "digital/analog"},
            {"id": "GPIO33", "name": "GPIO33", "type": "digital/analog"},
            {"id": "GPIO34", "name": "GPIO34", "type": "analog"},
            {"id": "GPIO35", "name": "GPIO35", "type": "analog"},
            {"id": "GPIO36", "name": "GPIO36 (VP)", "type": "analog"},
            {"id": "GPIO39", "name": "GPIO39 (VN)", "type": "analog"},
            {"id": "3V3", "name": "3.3V", "type": "power", "voltage": 3.3},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "VIN", "name": "VIN (5V)", "type": "power", "voltage": 5.0},
            {"id": "EN", "name": "EN (Reset)", "type": "special"},
        ],
        "i2c_pins": {"sda": "GPIO21", "scl": "GPIO22"},
        "spi_pins": {"mosi": "GPIO23", "miso": "GPIO19", "sck": "GPIO18", "ss": "GPIO5"},
        "uart_pins": {"tx": "GPIO1", "rx": "GPIO3"},
        "notes": [
            "3.3V logic -- NOT 5V tolerant on most pins",
            "GPIO34-39 are INPUT ONLY",
            "GPIO0, 2, 12, 15 affect boot -- use carefully",
            "Two cores at 240MHz, hardware crypto, Hall sensor",
            "Built-in WiFi AND Bluetooth LE",
            "Use ESP32 board package in Arduino IDE",
        ],
        "svg_color": "#e74c3c",
    },

    "esp32_c3_mini": {
        "id": "esp32_c3_mini",
        "name": "ESP32-C3 Mini",
        "category": "board",
        "family": "esp32",
        "description": "Tiny WiFi+BT RISC-V ESP32. Great for wearables.",
        "voltage": 3.3,
        "logic": 3.3,
        "cpu": "ESP32-C3",
        "wifi": True,
        "bluetooth": True,
        "dimensions": {"w": 13.2, "h": 16.0},
        "notes": [
            "RISC-V based -- different from original ESP32",
            "Much smaller than DevKit -- great for wearables",
            "18 GPIO pins",
        ],
        "svg_color": "#c0392b",
    },

    "raspberry_pi_pico": {
        "id": "raspberry_pi_pico",
        "name": "Raspberry Pi Pico",
        "category": "board",
        "family": "raspberry_pi",
        "description": "RP2040, 3.3V, 26 GPIO, MicroPython or C++",
        "voltage": 3.3,
        "logic": 3.3,
        "cpu": "RP2040 (dual ARM Cortex-M0+)",
        "clock_mhz": 133,
        "dimensions": {"w": 21.0, "h": 51.3},
        "pins": [
            {"id": "GP0", "name": "GP0 (UART0 TX)", "type": "digital/uart"},
            {"id": "GP1", "name": "GP1 (UART0 RX)", "type": "digital/uart"},
            {"id": "GP2", "name": "GP2", "type": "digital"},
            {"id": "GP3", "name": "GP3", "type": "digital"},
            {"id": "GP4", "name": "GP4 (SDA)", "type": "digital/i2c"},
            {"id": "GP5", "name": "GP5 (SCL)", "type": "digital/i2c"},
            {"id": "GP6", "name": "GP6", "type": "digital"},
            {"id": "GP7", "name": "GP7", "type": "digital"},
            {"id": "GP8", "name": "GP8", "type": "digital"},
            {"id": "GP9", "name": "GP9", "type": "digital"},
            {"id": "GP10", "name": "GP10 (SCK)", "type": "digital/spi"},
            {"id": "GP11", "name": "GP11 (MOSI)", "type": "digital/spi"},
            {"id": "GP12", "name": "GP12 (MISO)", "type": "digital/spi"},
            {"id": "GP13", "name": "GP13 (SS)", "type": "digital/spi"},
            {"id": "GP14", "name": "GP14", "type": "digital"},
            {"id": "GP15", "name": "GP15", "type": "digital"},
            {"id": "GP16", "name": "GP16", "type": "digital"},
            {"id": "GP17", "name": "GP17", "type": "digital"},
            {"id": "GP18", "name": "GP18", "type": "digital"},
            {"id": "GP19", "name": "GP19", "type": "digital"},
            {"id": "GP20", "name": "GP20", "type": "digital"},
            {"id": "GP21", "name": "GP21", "type": "digital"},
            {"id": "GP26", "name": "GP26 (ADC0)", "type": "analog"},
            {"id": "GP27", "name": "GP27 (ADC1)", "type": "analog"},
            {"id": "GP28", "name": "GP28 (ADC2)", "type": "analog"},
            {"id": "3V3", "name": "3.3V", "type": "power", "voltage": 3.3},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "VSYS", "name": "VSYS (1.8-5.5V)", "type": "power"},
            {"id": "VBUS", "name": "VBUS (5V USB)", "type": "power", "voltage": 5.0},
        ],
        "i2c_pins": {"sda": "GP4", "scl": "GP5"},
        "spi_pins": {"sck": "GP10", "mosi": "GP11", "miso": "GP12", "ss": "GP13"},
        "notes": [
            "All GPIO are 3.3V -- not 5V tolerant",
            "Dual-core at 133MHz",
            "3 ADC channels on GP26-28",
            "All GPIO support PWM",
            "MicroPython or C++ (Pico SDK)",
            "Pico W adds WiFi+BT",
        ],
        "svg_color": "#c51a4a",
    },

    "raspberry_pi_pico_w": {
        "id": "raspberry_pi_pico_w",
        "name": "Raspberry Pi Pico W",
        "category": "board",
        "family": "raspberry_pi",
        "description": "Pico with WiFi + Bluetooth. Same pinout as Pico.",
        "voltage": 3.3,
        "logic": 3.3,
        "wifi": True,
        "bluetooth": True,
        "notes": [
            "Same pinout as Pico -- drop-in for wireless projects",
            "WiFi via CYW43439 chip",
            "LED is on the wireless chip, not GPIO25",
        ],
        "svg_color": "#c51a4a",
    },

    # -- SENSORS ---------------------------------------------------------

    "dht22": {
        "id": "dht22",
        "name": "DHT22 Temp/Humidity Sensor",
        "category": "sensor",
        "description": "Temp (-40 to 80C) and humidity (0-100%). Single wire protocol.",
        "voltage": "3.3-5V",
        "interface": "1-wire",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power"},
            {"id": "DATA", "name": "DATA", "type": "digital"},
            {"id": "GND", "name": "GND", "type": "ground"},
        ],
        "notes": [
            "Needs 10k ohm pull-up resistor on DATA pin",
            "Min 2 seconds between readings",
            "Use DHT library in Arduino",
            "More accurate than DHT11",
        ],
        "libraries": ["DHT sensor library by Adafruit"],
        "resistors": [{"value": "10k", "between": ["VCC", "DATA"]}],
        "svg_color": "#27ae60",
    },

    "dht11": {
        "id": "dht11",
        "name": "DHT11 Temp/Humidity Sensor",
        "category": "sensor",
        "description": "Basic temp (0-50C) and humidity sensor. Cheap, lower accuracy.",
        "voltage": "3.3-5V",
        "interface": "1-wire",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power"},
            {"id": "DATA", "name": "DATA", "type": "digital"},
            {"id": "GND", "name": "GND", "type": "ground"},
        ],
        "notes": [
            "Needs 10k ohm pull-up resistor on DATA pin",
            "1 second minimum between readings",
            "Less accurate than DHT22 but cheaper",
        ],
        "libraries": ["DHT sensor library by Adafruit"],
        "resistors": [{"value": "10k", "between": ["VCC", "DATA"]}],
        "svg_color": "#2ecc71",
    },

    "hc_sr04": {
        "id": "hc_sr04",
        "name": "HC-SR04 Ultrasonic Distance",
        "category": "sensor",
        "description": "Distance sensor 2-400cm. TRIG sends pulse, ECHO returns width.",
        "voltage": 5.0,
        "interface": "digital",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power", "voltage": 5.0},
            {"id": "TRIG", "name": "TRIG", "type": "digital"},
            {"id": "ECHO", "name": "ECHO", "type": "digital", "voltage": 5.0},
            {"id": "GND", "name": "GND", "type": "ground"},
        ],
        "notes": [
            "5V ONLY -- ECHO pin outputs 5V (use voltage divider with 3.3V boards)",
            "TRIG: 10us HIGH pulse",
            "ECHO HIGH time = distance in time",
            "distance_cm = duration / 58.0",
        ],
        "svg_color": "#3498db",
    },

    "pir_motion": {
        "id": "pir_motion",
        "name": "PIR Motion Sensor (HC-SR501)",
        "category": "sensor",
        "description": "Passive infrared motion detector. HIGH when motion detected.",
        "voltage": "3.3-5V",
        "interface": "digital",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power"},
            {"id": "OUT", "name": "OUT", "type": "digital"},
            {"id": "GND", "name": "GND", "type": "ground"},
        ],
        "notes": [
            "Has sensitivity and delay potentiometers on board",
            "Warm-up time ~30 seconds on first power",
            "Detect range: up to 7m",
            "H trigger: stays HIGH while motion. L trigger: single pulse",
        ],
        "svg_color": "#e67e22",
    },

    "ds18b20": {
        "id": "ds18b20",
        "name": "DS18B20 Waterproof Temp Sensor",
        "category": "sensor",
        "description": "Waterproof temperature probe. OneWire bus, multiple on same pin.",
        "voltage": "3.3-5V",
        "interface": "1-wire",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power"},
            {"id": "DATA", "name": "DATA", "type": "digital"},
            {"id": "GND", "name": "GND", "type": "ground"},
        ],
        "notes": [
            "4.7k ohm pull-up resistor on DATA pin",
            "Multiple sensors on same pin -- each has unique address",
            "+/-0.5C accuracy",
            "Great for liquids, outdoor use",
        ],
        "libraries": ["OneWire", "DallasTemperature"],
        "resistors": [{"value": "4.7k", "between": ["VCC", "DATA"]}],
        "svg_color": "#16a085",
    },

    "mpu6050": {
        "id": "mpu6050",
        "name": "MPU-6050 Gyroscope/Accelerometer",
        "category": "sensor",
        "description": "6-DOF IMU: 3-axis gyro + 3-axis accel. I2C.",
        "voltage": "3.3-5V",
        "interface": "i2c",
        "i2c_address": "0x68 (0x69 if AD0 HIGH)",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power"},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "SCL", "name": "SCL", "type": "i2c"},
            {"id": "SDA", "name": "SDA", "type": "i2c"},
            {"id": "XDA", "name": "XDA", "type": "special"},
            {"id": "XCL", "name": "XCL", "type": "special"},
            {"id": "AD0", "name": "AD0", "type": "digital"},
            {"id": "INT", "name": "INT", "type": "digital"},
        ],
        "notes": [
            "I2C address 0x68 by default, 0x69 if AD0 pulled HIGH",
            "Breakout boards have onboard 3.3V regulator",
            "Onboard temperature sensor too",
        ],
        "libraries": ["MPU6050 by Electronic Cats", "Wire"],
        "svg_color": "#8e44ad",
    },

    # -- ACTUATORS ---------------------------------------------------------

    "servo_motor": {
        "id": "servo_motor",
        "name": "Servo Motor (SG90 / MG996R)",
        "category": "actuator",
        "description": "RC servo. 0-180 degree position control via PWM.",
        "voltage": "4.8-6V (5V typical)",
        "interface": "pwm",
        "pins": [
            {"id": "VCC", "name": "VCC (Red)", "type": "power", "voltage": 5.0},
            {"id": "GND", "name": "GND (Brown/Black)", "type": "ground"},
            {"id": "SIGNAL", "name": "Signal (Orange/Yellow)", "type": "pwm"},
        ],
        "notes": [
            "PWM frequency: 50Hz, pulse: 1-2ms",
            "SG90: plastic gear, 1.8kg/cm, 9g",
            "MG996R: metal gear, 10kg/cm, 55g",
            "Power servo from separate 5V if possible",
            "Can cause voltage drops on Arduino 5V pin",
        ],
        "libraries": ["Servo (built-in Arduino)"],
        "svg_color": "#e74c3c",
    },

    "stepper_28byj48": {
        "id": "stepper_28byj48",
        "name": "28BYJ-48 Stepper + ULN2003 Driver",
        "category": "actuator",
        "description": "Cheap 5V stepper motor kit. 64 steps/rev * 64:1 = 4096 steps/rev.",
        "voltage": 5.0,
        "interface": "digital",
        "pins": [
            {"id": "IN1", "name": "IN1", "type": "digital"},
            {"id": "IN2", "name": "IN2", "type": "digital"},
            {"id": "IN3", "name": "IN3", "type": "digital"},
            {"id": "IN4", "name": "IN4", "type": "digital"},
            {"id": "VCC", "name": "VCC (5V)", "type": "power", "voltage": 5.0},
            {"id": "GND", "name": "GND", "type": "ground"},
        ],
        "notes": [
            "Comes with ULN2003 driver board",
            "Power motor from separate 5V supply",
            "64 steps (half step) per revolution of shaft * 64:1 gearbox = 4096",
            "Slow but strong torque for the price",
        ],
        "libraries": ["Stepper (built-in)", "AccelStepper"],
        "svg_color": "#2980b9",
    },

    "relay_5v": {
        "id": "relay_5v",
        "name": "5V Relay Module",
        "category": "actuator",
        "description": "Electromechanical relay. Control mains AC or high voltage DC.",
        "voltage": 5.0,
        "interface": "digital",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power", "voltage": 5.0},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "IN", "name": "IN", "type": "digital"},
            {"id": "COM", "name": "COM", "type": "special"},
            {"id": "NO", "name": "NO", "type": "special"},
            {"id": "NC", "name": "NC", "type": "special"},
        ],
        "notes": [
            "ACTIVE LOW -- LOW signal closes relay on most modules",
            "COM = Common, NO = Normally Open, NC = Normally Closed",
            "Max: 10A/250VAC, 10A/30VDC",
            "Use flyback diode if driving without module",
            "Isolate AC wiring -- DANGEROUS if done wrong",
        ],
        "svg_color": "#c0392b",
    },

    # -- LEDs AND LIGHTING ---------------------------------------------------------

    "led_single": {
        "id": "led_single",
        "name": "LED (Single)",
        "category": "passive",
        "description": "Basic LED. Always use with current limiting resistor.",
        "voltage": "1.8-3.3V forward voltage",
        "interface": "digital/analog",
        "pins": [
            {"id": "ANODE", "name": "Anode (+)", "type": "power"},
            {"id": "CATHODE", "name": "Cathode (-)", "type": "ground"},
        ],
        "notes": [
            "ALWAYS use current limiting resistor",
            "Resistor = (supply - LED forward voltage) / desired current",
            "Typical: (5V - 2V) / 0.02A = 150 ohm minimum",
            "Use 220-470 ohm for typical LEDs from Arduino pins",
        ],
        "svg_color": "#f39c12",
    },

    "ws2812b": {
        "id": "ws2812b",
        "name": "WS2812B NeoPixel LED Strip/Ring",
        "category": "actuator",
        "description": "Addressable RGB LEDs. Chain hundreds on a single data pin.",
        "voltage": 5.0,
        "interface": "digital",
        "pins": [
            {"id": "VCC", "name": "5V", "type": "power", "voltage": 5.0},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "DIN", "name": "DIN", "type": "digital"},
            {"id": "DOUT", "name": "DOUT", "type": "digital"},
        ],
        "notes": [
            "5V ONLY for the LEDs themselves",
            "Data can be 3.3V or 5V (usually works with 3.3V)",
            "Add 300-500 ohm resistor on DATA line",
            "Add 1000uF capacitor across VCC/GND at power inject",
            "Each LED draws up to 60mA at full white",
            "10 LEDs at full brightness = 600mA -- use external supply",
            "3.3V boards: add level shifter or 300 ohm resistor usually works",
        ],
        "libraries": ["Adafruit NeoPixel", "FastLED"],
        "resistors": [{"value": "300-500 ohm", "on": "DIN"}],
        "svg_color": "#9b59b6",
    },

    # -- DISPLAYS ---------------------------------------------------------

    "oled_128x64": {
        "id": "oled_128x64",
        "name": "0.96in OLED Display 128x64 I2C",
        "category": "display",
        "description": "Tiny but sharp OLED. 128x64 pixels, I2C, SSD1306 driver.",
        "voltage": "3.3-5V",
        "interface": "i2c",
        "i2c_address": "0x3C (or 0x3D)",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power"},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "SCL", "name": "SCL", "type": "i2c"},
            {"id": "SDA", "name": "SDA", "type": "i2c"},
        ],
        "notes": [
            "I2C address: 0x3C (most), 0x3D if jumper set",
            "Works at 3.3V or 5V",
            "SSD1306 driver chip",
            "Pixel colors: ON (white/blue/yellow) or OFF (black)",
        ],
        "libraries": ["Adafruit SSD1306", "Adafruit GFX"],
        "svg_color": "#1abc9c",
    },

    "lcd_16x2": {
        "id": "lcd_16x2",
        "name": "16x2 LCD Display (I2C backpack)",
        "category": "display",
        "description": "Classic character LCD with I2C backpack module.",
        "voltage": 5.0,
        "interface": "i2c",
        "i2c_address": "0x27 (or 0x3F)",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power", "voltage": 5.0},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "SCL", "name": "SCL", "type": "i2c"},
            {"id": "SDA", "name": "SDA", "type": "i2c"},
        ],
        "notes": [
            "I2C backpack uses only 4 wires vs. 16 without it",
            "5V only for backlight",
            "I2C address usually 0x27 but scan to confirm",
        ],
        "libraries": ["LiquidCrystal_I2C"],
        "svg_color": "#27ae60",
    },

    # -- PASSIVE COMPONENTS ---------------------------------------------------------

    "resistor": {
        "id": "resistor",
        "name": "Resistor",
        "category": "passive",
        "description": "Fixed resistor. Common values: 220, 1k, 4.7k, 10k, 47k ohm",
        "pins": [
            {"id": "A", "name": "Lead A", "type": "passive"},
            {"id": "B", "name": "Lead B", "type": "passive"},
        ],
        "svg_color": "#7f8c8d",
    },

    "button_momentary": {
        "id": "button_momentary",
        "name": "Momentary Push Button",
        "category": "passive",
        "description": "Push to connect. Use with INPUT_PULLUP on Arduino.",
        "voltage": "any",
        "interface": "digital",
        "pins": [
            {"id": "PIN1A", "name": "Pin 1A", "type": "digital"},
            {"id": "PIN1B", "name": "Pin 1B", "type": "digital"},
            {"id": "PIN2A", "name": "Pin 2A", "type": "digital"},
            {"id": "PIN2B", "name": "Pin 2B", "type": "digital"},
        ],
        "notes": [
            "Use INPUT_PULLUP to avoid floating pin",
            "Button pulled to GND -- reads LOW when pressed",
            "1A/1B connected together, 2A/2B connected together",
            "Add debounce in code or 100nF capacitor",
        ],
        "svg_color": "#95a5a6",
    },

    "potentiometer": {
        "id": "potentiometer",
        "name": "Potentiometer (10k ohm)",
        "category": "passive",
        "description": "Variable resistor for analog control.",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power"},
            {"id": "WIPER", "name": "Wiper", "type": "analog"},
            {"id": "GND", "name": "GND", "type": "ground"},
        ],
        "notes": [
            "Wiper to analog input gives 0 to VCC",
            "Common values: 1k, 10k, 100k",
        ],
        "svg_color": "#bdc3c7",
    },

    "capacitor": {
        "id": "capacitor",
        "name": "Electrolytic Capacitor",
        "category": "passive",
        "description": "For decoupling power lines. Common: 100uF, 1000uF",
        "pins": [
            {"id": "+", "name": "Positive (+)", "type": "power"},
            {"id": "-", "name": "Negative (-)", "type": "ground"},
        ],
        "notes": [
            "POLARITY MATTERS -- stripe side is negative",
            "Place across VCC/GND near power injection points",
            "1000uF for NeoPixel power lines",
        ],
        "svg_color": "#ecf0f1",
    },

    # -- COMMUNICATION ---------------------------------------------------------

    "hc_05_bluetooth": {
        "id": "hc_05_bluetooth",
        "name": "HC-05 Bluetooth Module",
        "category": "communication",
        "description": "Classic Bluetooth serial bridge. 5V module, 3.3V logic TX.",
        "voltage": "3.6-6V",
        "interface": "uart",
        "pins": [
            {"id": "VCC", "name": "VCC", "type": "power", "voltage": 5.0},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "TXD", "name": "TXD", "type": "uart", "voltage": 3.3},
            {"id": "RXD", "name": "RXD", "type": "uart"},
            {"id": "STATE", "name": "STATE", "type": "digital"},
            {"id": "EN", "name": "EN", "type": "digital"},
        ],
        "notes": [
            "Module VCC is 5V but TX outputs 3.3V",
            "RXD needs voltage divider if connected to 5V board TX",
            "Default: 9600 baud, AT mode with EN pin HIGH at power",
            "Pairs to phone as serial port",
        ],
        "svg_color": "#2980b9",
    },

    "nrf24l01": {
        "id": "nrf24l01",
        "name": "nRF24L01 2.4GHz Radio",
        "category": "communication",
        "description": "Long range 2.4GHz radio module. SPI, 3.3V.",
        "voltage": 3.3,
        "interface": "spi",
        "pins": [
            {"id": "VCC", "name": "VCC (3.3V)", "type": "power", "voltage": 3.3},
            {"id": "GND", "name": "GND", "type": "ground"},
            {"id": "CE", "name": "CE", "type": "digital"},
            {"id": "CSN", "name": "CSN", "type": "digital/spi"},
            {"id": "SCK", "name": "SCK", "type": "spi"},
            {"id": "MOSI", "name": "MOSI", "type": "spi"},
            {"id": "MISO", "name": "MISO", "type": "spi"},
            {"id": "IRQ", "name": "IRQ", "type": "digital"},
        ],
        "notes": [
            "3.3V ONLY -- 5V will destroy it",
            "Use 10uF + 100nF capacitors across VCC/GND",
            "Range: 100m (without PA/LNA) to 1100m (with PA/LNA version)",
            "For use with 5V Arduino: VCC to 3.3V, logic signals OK",
        ],
        "libraries": ["RF24 by TMRh20"],
        "svg_color": "#16a085",
    },

    # -- POWER ---------------------------------------------------------

    "lm7805": {
        "id": "lm7805",
        "name": "LM7805 5V Voltage Regulator",
        "category": "power",
        "description": "Linear regulator. Input 7-35V, output 5V, max 1A.",
        "pins": [
            {"id": "IN", "name": "Input", "type": "power"},
            {"id": "GND", "name": "Ground", "type": "ground"},
            {"id": "OUT", "name": "Output", "type": "power", "voltage": 5.0},
        ],
        "notes": [
            "Gets HOT under load -- heatsink recommended over 500mA",
            "Input must be at least 7V for stable 5V output",
            "Linear = inefficient, converts excess to heat",
            "Add 100nF caps on input and output",
        ],
        "svg_color": "#7f8c8d",
    },

    "tp4056": {
        "id": "tp4056",
        "name": "TP4056 LiPo Charger Module",
        "category": "power",
        "description": "1A LiPo/LiIon charger with protection. USB-C or micro-USB input.",
        "pins": [
            {"id": "USB_VCC", "name": "USB VCC", "type": "power", "voltage": 5.0},
            {"id": "USB_GND", "name": "USB GND", "type": "ground"},
            {"id": "BAT_PLUS", "name": "B+ (Battery)", "type": "power"},
            {"id": "BAT_MINUS", "name": "B- (Battery)", "type": "ground"},
            {"id": "OUT_PLUS", "name": "OUT+ (Load)", "type": "power"},
            {"id": "OUT_MINUS", "name": "OUT- (Load)", "type": "ground"},
        ],
        "notes": [
            "OUT+/OUT- for your circuit (has protection)",
            "B+/B- for the LiPo cell",
            "Built-in overcharge and over-discharge protection",
            "LED indicators: charging = red, complete = blue",
            "Max output: 3.7V (LiPo voltage, not 5V)",
        ],
        "svg_color": "#e74c3c",
    },
}


def get_component(component_id: str) -> dict:
    return COMPONENTS.get(component_id, {})


def search_components(query: str, category: str = None) -> list:
    """Search components by name, description, or id."""
    query_lower = query.lower()
    results = []
    for comp in COMPONENTS.values():
        if category and comp.get("category") != category:
            continue
        if (query_lower in comp["name"].lower()
                or query_lower in comp.get("description", "").lower()
                or query_lower in comp["id"].lower()):
            results.append(comp)
    return results


def list_by_category(category: str) -> list:
    return [c for c in COMPONENTS.values() if c.get("category") == category]


def get_all_ids() -> list:
    return list(COMPONENTS.keys())
