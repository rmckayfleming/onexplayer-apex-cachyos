"""Speaker DSP enhancement for OneXPlayer Apex on Bazzite.

Writes a PipeWire filter-chain config that applies parametric EQ to the
internal speakers only. Uses PipeWire's builtin biquad filters — zero
external dependencies.

Config is written to ~/.config/pipewire/pipewire.conf.d/ so it survives
Bazzite updates and auto-loads on PipeWire startup.
"""

import logging
import os
import pwd
import subprocess

logger = logging.getLogger("OXP-SpeakerDSP")

# Pluggable log callbacks — set by main.py to route logs to the plugin log file.
_log_info_cb = None
_log_error_cb = None
_log_warning_cb = None


def set_log_callbacks(info_fn, error_fn, warning_fn):
    """Set external log callbacks (called by main.py to wire into plugin logging)."""
    global _log_info_cb, _log_error_cb, _log_warning_cb
    _log_info_cb = info_fn
    _log_error_cb = error_fn
    _log_warning_cb = warning_fn


def _log_info(msg):
    if _log_info_cb:
        _log_info_cb(msg)
    else:
        logger.info(msg)


def _log_error(msg):
    if _log_error_cb:
        _log_error_cb(msg)
    else:
        logger.error(msg)


def _log_warning(msg):
    if _log_warning_cb:
        _log_warning_cb(msg)
    else:
        logger.warning(msg)


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


# --- Constants ---

CONFIG_FILENAME = "99-oxp-apex-speaker-dsp.conf"
SPEAKER_NODE = "alsa_output.pci-0000_65_00.6.HiFi__Speaker__sink"

# EQ profiles — each is a list of biquad filter bands.
# Format: (type, freq_hz, gain_db, Q)
# Types: lowshelf, highshelf, peaking, lowpass, highpass
PROFILES = {
    "balanced": {
        "description": "Corrective EQ — roll off sub-bass, boost upper bass, tame harshness, add air",
        "bands": [
            ("highpass", 80, 0.0, 0.7),       # Roll off sub-bass (speakers can't reproduce)
            ("lowshelf", 120, 3.0, 0.7),       # Boost upper bass for warmth
            ("peaking", 250, -1.5, 1.0),       # Reduce muddiness
            ("peaking", 800, 1.0, 1.5),         # Add presence
            ("peaking", 2500, -2.0, 1.2),       # Tame 2-4kHz harshness
            ("peaking", 4000, -1.5, 1.5),       # Reduce sibilance region
            ("peaking", 6000, 1.0, 2.0),        # Add clarity
            ("highshelf", 10000, 2.0, 0.7),     # Add treble air
        ],
    },
    "bass_boost": {
        "description": "Enhanced bass — aggressive low-end boost with harshness correction",
        "bands": [
            ("highpass", 60, 0.0, 0.7),        # Roll off very low sub-bass
            ("lowshelf", 150, 5.0, 0.7),        # Aggressive bass boost
            ("peaking", 250, -1.0, 1.0),        # Control muddiness from bass boost
            ("peaking", 500, 1.5, 1.0),          # Add body
            ("peaking", 2500, -2.0, 1.2),        # Tame harshness
            ("peaking", 4000, -1.5, 1.5),        # Reduce sibilance
            ("highshelf", 10000, 1.0, 0.7),      # Mild treble lift
        ],
    },
    "flat": {
        "description": "Minimal correction — sub-bass rolloff and mild midrange cleanup only",
        "bands": [
            ("highpass", 80, 0.0, 0.7),        # Roll off sub-bass
            ("peaking", 300, -1.0, 0.8),        # Mild muddy-range cut
            ("peaking", 3000, -1.0, 1.0),       # Gentle harshness reduction
        ],
    },
}


def _get_user_info():
    """Get the real (non-root) user's info: (username, home_dir, uid).

    Decky runs as root, so we need to find the actual user.
    """
    # Check SUDO_USER first
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            pw = pwd.getpwnam(sudo_user)
            return (pw.pw_name, pw.pw_dir, pw.pw_uid)
        except KeyError:
            pass

    # Infer from Decky plugin dir path
    try:
        import decky
        plugin_dir = decky.DECKY_PLUGIN_DIR
        if plugin_dir.startswith("/home/"):
            parts = plugin_dir.split("/")
            if len(parts) >= 3:
                username = parts[2]
                try:
                    pw = pwd.getpwnam(username)
                    return (pw.pw_name, pw.pw_dir, pw.pw_uid)
                except KeyError:
                    pass
    except ImportError:
        pass

    # Fallback: first non-root user in /home
    try:
        for name in sorted(os.listdir("/home")):
            path = f"/home/{name}"
            if os.path.isdir(path) and name != "root":
                try:
                    pw = pwd.getpwnam(name)
                    return (pw.pw_name, pw.pw_dir, pw.pw_uid)
                except KeyError:
                    pass
    except OSError:
        pass

    return ("root", "/root", 0)


def _get_config_path():
    """Get the path for the PipeWire config file."""
    _, home_dir, _ = _get_user_info()
    config_dir = os.path.join(home_dir, ".config", "pipewire", "pipewire.conf.d")
    return os.path.join(config_dir, CONFIG_FILENAME)


def _find_speaker_node():
    """Try to auto-detect the speaker ALSA node, fall back to hardcoded.

    Runs pw-cli as the real user to list PipeWire nodes and find
    the ALC245 speaker sink.
    """
    username, _, uid = _get_user_info()
    try:
        env = _clean_env()
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
        r = subprocess.run(
            ["runuser", "-u", username, "--", "pw-cli", "list-objects", "Node"],
            capture_output=True, text=True, timeout=10,
            env=env,
        )
        if r.returncode == 0 and "Speaker" in r.stdout and "HiFi" in r.stdout:
            # Parse output to find speaker sink node name
            for line in r.stdout.splitlines():
                stripped = line.strip()
                if "node.name" in stripped and "Speaker" in stripped and "sink" in stripped:
                    # Format: node.name = "alsa_output..."
                    parts = stripped.split("=", 1)
                    if len(parts) == 2:
                        node = parts[1].strip().strip('"').strip()
                        if node:
                            _log_info(f"Auto-detected speaker node: {node}")
                            return node
    except Exception as e:
        _log_warning(f"Speaker node auto-detection failed: {e}")

    _log_info(f"Using hardcoded speaker node: {SPEAKER_NODE}")
    return SPEAKER_NODE


def _biquad_str(band_type, freq, gain, q):
    """Generate a single biquad filter entry for PipeWire SPA-JSON config."""
    if band_type == "highpass":
        return f"""            {{
                type  = bq_highpass
                freq  = {freq}
                Q     = {q}
            }}"""
    elif band_type == "lowpass":
        return f"""            {{
                type  = bq_lowpass
                freq  = {freq}
                Q     = {q}
            }}"""
    elif band_type == "lowshelf":
        return f"""            {{
                type  = bq_lowshelf
                freq  = {freq}
                gain  = {gain}
                Q     = {q}
            }}"""
    elif band_type == "highshelf":
        return f"""            {{
                type  = bq_highshelf
                freq  = {freq}
                gain  = {gain}
                Q     = {q}
            }}"""
    else:  # peaking
        return f"""            {{
                type  = bq_peaking
                freq  = {freq}
                gain  = {gain}
                Q     = {q}
            }}"""


def _generate_config(profile_name, speaker_node):
    """Generate PipeWire SPA-JSON filter-chain config string.

    Follows the same format as the GPD Win Mini config in Bazzite
    (/usr/share/pipewire/hardware-profiles/gpd-g1617-01/).
    """
    profile = PROFILES.get(profile_name)
    if not profile:
        raise ValueError(f"Unknown profile: {profile_name}")

    bands = profile["bands"]
    filter_entries = "\n".join(
        _biquad_str(btype, freq, gain, q) for btype, freq, gain, q in bands
    )

    return f"""\
# OXP Apex Speaker DSP — {profile_name} profile
# Auto-generated by OneXPlayer Apex Tools
# Applies parametric EQ to internal speakers only.

context.modules = [
    {{
        name = libpipewire-module-filter-chain
        args = {{
            node.description = "OXP Apex Speaker EQ"
            media.name        = "OXP Apex Speaker EQ"
            filter.graph = {{
                nodes = [
                    {{
                        type   = builtin
                        name   = eq_band_l
                        label  = bq_eq
                        control = {{
{filter_entries}
                        }}
                    }}
                    {{
                        type   = builtin
                        name   = eq_band_r
                        label  = bq_eq
                        control = {{
{filter_entries}
                        }}
                    }}
                ]
                links = [
                    {{ output = "eq_band_l:Out" input = "playback_l:In" }}
                    {{ output = "eq_band_r:Out" input = "playback_r:In" }}
                ]
                inputs  = [ "eq_band_l:In" "eq_band_r:In" ]
                outputs = [ "playback_l:Out" "playback_r:Out" ]
            }}
            capture.props = {{
                node.name         = "oxp_apex_speaker_eq_sink"
                media.class       = Audio/Sink
                audio.channels    = 2
                audio.position    = [ FL FR ]
                priority.driver   = 1009
                priority.session  = 1009
            }}
            playback.props = {{
                node.name         = "oxp_apex_speaker_eq_output"
                node.target       = "{speaker_node}"
                audio.channels    = 2
                audio.position    = [ FL FR ]
                node.passive      = true
                stream.dont-remix = true
            }}
        }}
    }}
]
"""


def _restart_pipewire():
    """Restart PipeWire user service so the config takes effect."""
    username, _, uid = _get_user_info()
    env = _clean_env()
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

    _log_info(f"Restarting PipeWire for user {username} (uid={uid})...")
    try:
        r = subprocess.run(
            ["runuser", "-u", username, "--",
             "systemctl", "--user", "restart", "pipewire.service"],
            capture_output=True, text=True, timeout=15,
            env=env,
        )
        if r.returncode != 0:
            _log_warning(f"PipeWire restart returned {r.returncode}: {r.stderr.strip()}")
            # Also try pipewire-pulse in case it needs a kick
            subprocess.run(
                ["runuser", "-u", username, "--",
                 "systemctl", "--user", "restart", "pipewire-pulse.service"],
                capture_output=True, text=True, timeout=15,
                env=env,
            )
        else:
            _log_info("PipeWire restarted successfully")
    except subprocess.TimeoutExpired:
        _log_error("PipeWire restart timed out")
        raise
    except Exception as e:
        _log_error(f"PipeWire restart failed: {e}")
        raise


def get_status():
    """Check if speaker DSP is currently enabled and which profile is active.

    Returns: {"enabled": bool, "profile": str|None, "speaker_node": str|None}
    """
    config_path = _get_config_path()

    if not os.path.exists(config_path):
        return {"enabled": False, "profile": None, "speaker_node": None}

    # Parse profile from the config file comment header
    profile = None
    try:
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("# OXP Apex Speaker DSP"):
                    # Format: "# OXP Apex Speaker DSP — balanced profile"
                    if "—" in line and "profile" in line:
                        part = line.split("—", 1)[1].strip()
                        profile = part.replace("profile", "").strip()
                        if profile not in PROFILES:
                            profile = None
                    break
    except Exception:
        pass

    # Try to read speaker node from config
    speaker_node = None
    try:
        with open(config_path) as f:
            content = f.read()
        for line in content.splitlines():
            if "node.target" in line and "=" in line:
                parts = line.split("=", 1)
                if len(parts) == 2:
                    speaker_node = parts[1].strip().strip('"').strip()
                    break
    except Exception:
        pass

    return {
        "enabled": True,
        "profile": profile,
        "speaker_node": speaker_node,
    }


def enable(profile="balanced"):
    """Enable speaker DSP with the specified profile.

    Detects the speaker node, writes the PipeWire config, and restarts PipeWire.
    """
    _log_info(f"=== Speaker DSP Enable ({profile}) ===")

    if profile not in PROFILES:
        return {"success": False, "error": f"Unknown profile: {profile}"}

    # Detect speaker node
    speaker_node = _find_speaker_node()

    # Generate config
    try:
        config = _generate_config(profile, speaker_node)
    except Exception as e:
        _log_error(f"Failed to generate config: {e}")
        return {"success": False, "error": str(e)}

    # Ensure config directory exists
    config_path = _get_config_path()
    config_dir = os.path.dirname(config_path)
    try:
        os.makedirs(config_dir, exist_ok=True)
    except Exception as e:
        _log_error(f"Failed to create config directory: {e}")
        return {"success": False, "error": f"Cannot create config directory: {e}"}

    # Write config file
    try:
        with open(config_path, "w") as f:
            f.write(config)
        _log_info(f"Wrote config to {config_path}")
    except Exception as e:
        _log_error(f"Failed to write config: {e}")
        return {"success": False, "error": f"Cannot write config: {e}"}

    # chown to the real user (PipeWire runs as user, not root)
    username, _, uid = _get_user_info()
    try:
        gid = pwd.getpwnam(username).pw_gid
        # chown the config file and parent dirs we may have created
        os.chown(config_path, uid, gid)
        # Walk up and fix ownership for dirs we created
        d = config_dir
        while d and not d.endswith(".config"):
            try:
                os.chown(d, uid, gid)
            except Exception:
                break
            d = os.path.dirname(d)
    except Exception as e:
        _log_warning(f"chown failed (config may not load): {e}")

    # Restart PipeWire
    try:
        _restart_pipewire()
    except Exception as e:
        return {
            "success": True,
            "warning": f"Config written but PipeWire restart failed: {e}",
            "profile": profile,
            "speaker_node": speaker_node,
        }

    _log_info(f"Speaker DSP enabled with {profile} profile")
    return {
        "success": True,
        "message": f"Speaker DSP enabled — {profile} profile",
        "profile": profile,
        "speaker_node": speaker_node,
    }


def disable():
    """Disable speaker DSP by removing the config file and restarting PipeWire."""
    _log_info("=== Speaker DSP Disable ===")

    config_path = _get_config_path()

    if not os.path.exists(config_path):
        return {"success": True, "message": "Already disabled"}

    try:
        os.remove(config_path)
        _log_info(f"Removed config: {config_path}")
    except Exception as e:
        _log_error(f"Failed to remove config: {e}")
        return {"success": False, "error": f"Cannot remove config: {e}"}

    # Restart PipeWire
    try:
        _restart_pipewire()
    except Exception as e:
        return {
            "success": True,
            "warning": f"Config removed but PipeWire restart failed: {e}",
        }

    _log_info("Speaker DSP disabled")
    return {"success": True, "message": "Speaker DSP disabled"}


def set_profile(name):
    """Switch to a different EQ profile. Rewrites config and restarts PipeWire."""
    _log_info(f"=== Speaker DSP Set Profile: {name} ===")

    if name not in PROFILES:
        return {"success": False, "error": f"Unknown profile: {name}"}

    # If not currently enabled, just enable with the new profile
    status = get_status()
    if not status["enabled"]:
        return enable(name)

    # Re-enable with new profile (overwrites config + restarts)
    return enable(name)


def list_profiles():
    """Return available EQ profiles with descriptions."""
    return {
        name: {"description": p["description"]}
        for name, p in PROFILES.items()
    }
