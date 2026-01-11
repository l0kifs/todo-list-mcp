# Sound Assets

## Default Reminder Sound

The package includes a built-in reminder sound: `reminder_chime.wav`

### Characteristics
- **Format**: WAV (PCM 16-bit)
- **Sample Rate**: 44.1 kHz
- **Channels**: Mono
- **Duration**: 0.8 seconds
- **File Size**: ~69 KB

### Sound Design
The reminder chime consists of three ascending musical notes (E6, G6, C7) that create a pleasant, attention-grabbing notification sound:
- Quick attack for immediate attention
- Gradual decay for a smooth, non-jarring experience
- Bell-like harmonic quality

### Usage

The sound is automatically used when creating sounds without specifying a source:

```python
from todo_list_mcp.sound_client import SoundClient

client = SoundClient()
# Uses default sound automatically
sound_id = client.create_sound(loop=False)
```

You can also explicitly get the path to the default sound:

```python
from todo_list_mcp.sound_client import get_default_sound_path

default_path = get_default_sound_path()
print(f"Default sound: {default_path}")
```

### Custom Sounds

To use a custom sound file instead:

```python
# Use a custom WAV file
sound_id = client.create_sound(source="/path/to/custom.wav", loop=False)
```

### Platform Compatibility
- **Linux**: Supported via paplay, aplay, or ffplay
- **macOS**: Supported via afplay
- **Windows**: Supported via winsound module
