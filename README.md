# Alarm Clock

A modular alarm clock framework with a core scheduler, event-driven architecture, and plugin-based module system. This allows for easy extensibility with features like lights, audio, microphone input, or remote control.

## Quick Start

Follow these steps to get the alarm clock running on your device (e.g., Raspberry Pi).

```bash
git clone git@github.com:Nic01o/Project-Heimdall.git
cd Project-Heimdall

# Create a virtual environment (recommended for Debian/Raspberry Pi OS)
python3 -m venv .venv
source ./.venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run tests to ensure everything is working correctly
python -m pytest

# Start the alarm clock daemon
python -m alarmclock.daemon
```

## Using the Alarm Clock

### Web Interface

Once the daemon is running, you can manage your alarms and modules via a web browser. By default, the interface is available at `http://localhost:5000`.

**Features:**
- **Manage Sleep Plans**: Create groups of weekdays with specific wake times.
- **Module Settings**: Configure hardware modules (like LEDs or Buttons) directly through the UI.
- **Real-time Control**: Start, stop, or snooze active alarms from your browser.

## Testing

You can run the test suite to verify that your installation is correct and that all modules are functioning as expected.

```bash
# Run all tests
python -m pytest

# Run tests for a specific area
python -m pytest tests/test_scheduler.py
```

## Module Ideas (lol - what a wip)

Get inspired! The plugin system allows you to add many features:
- **Light**: Sunrise simulation using RGB LEDs.
- **Audio**: Playback of MP3 files or Spotify integration.
- **Sensors**: Use motion sensors (PIR) to snooze alarms by waving a hand.
- **Voice Control**: Integrate with microphones for voice commands.
- **Smart Home**: Connect via MQTT or Home Assistant.

## License

MIT