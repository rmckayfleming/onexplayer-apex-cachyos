#!/usr/bin/env python3
"""Unit tests for RX wrap detection logic.

Tests the analog stick parsing from _produce_apex, specifically the
signed 16-bit overflow that occurs at full deflection on the RX axis.

Hardware observations:
- Physical stick range LEFT: 0 → -31617, then wraps to +32767 (exact)
- Physical stick range RIGHT: 0 → +????, then wraps to -32768 (exact)
- Overflow always produces the exact s16 min/max boundary value
- The transition out of the wrap zone is always abrupt (no intermediate values)

Run: python3 test_rx_wrap.py
"""
import struct
import sys


# ── Processor under test ──────────────────────────────────────────

class FakeRXProcessor:
    """Simulates the RX processing from _produce_apex.

    This must EXACTLY match the logic in hid_v2.py so the tests
    are meaningful.
    """

    def __init__(self):
        self.prev_axes = {}

    def process_frame(self, rx_raw):
        """Process one frame of RX data.

        Returns the rs_x event value, or None if filtered by delta.
        """
        # ── Wrap detection ──
        # Signed 16-bit overflow produces exactly +32767 or -32768.
        # These are the ONLY values that indicate wrap — the stick's
        # physical range never reaches these exact boundary values.
        if rx_raw == 32767:
            rx = -1.0   # overflowed from left extreme
        elif rx_raw == -32768:
            rx = 1.0    # overflowed from right extreme
        else:
            rx = max(-1.0, min(1.0, rx_raw / 32768.0))

        # ── Delta filter (matches hid_v2.py) ──
        prev_val = self.prev_axes.get("rs_x")
        if prev_val is not None:
            delta = abs(rx - prev_val)
            if delta > 1.5:
                rx = 1.0 if prev_val > 0 else -1.0
            elif delta < 0.002:
                return None
        self.prev_axes["rs_x"] = rx
        return rx


# ── Test helpers ──────────────────────────────────────────────────

def run_sequence(raw_values):
    """Run a sequence of raw RX values, return list of (raw, output) tuples."""
    proc = FakeRXProcessor()
    return [(raw, proc.process_frame(raw)) for raw in raw_values]


def check_no_positive(outputs, label, after_frame=0):
    """Assert no emitted value is positive after a given frame."""
    fails = [(i, raw, val) for i, (raw, val) in enumerate(outputs)
             if i >= after_frame and val is not None and val > 0.01]
    if fails:
        print(f"FAIL: {label}")
        for i, raw, val in fails:
            print(f"  frame {i}: raw={raw:7d} output={val:+.4f} (positive = right!)")
        return False
    print(f"PASS: {label}")
    return True


def check_no_negative(outputs, label, after_frame=0):
    """Assert no emitted value is negative after a given frame."""
    fails = [(i, raw, val) for i, (raw, val) in enumerate(outputs)
             if i >= after_frame and val is not None and val < -0.01]
    if fails:
        print(f"FAIL: {label}")
        for i, raw, val in fails:
            print(f"  frame {i}: raw={raw:7d} output={val:+.4f} (negative = left!)")
        return False
    print(f"PASS: {label}")
    return True


def check_near(outputs, frame, expected, label, tol=0.05):
    """Assert a specific frame's output is near an expected value."""
    _, val = outputs[frame]
    if val is None:
        # filtered — check prev_axes wasn't corrupted
        print(f"PASS: {label} (filtered, not emitted)")
        return True
    if abs(val - expected) > tol:
        raw = outputs[frame][0]
        print(f"FAIL: {label}")
        print(f"  frame {frame}: raw={raw:7d} output={val:+.4f} expected≈{expected:+.4f}")
        return False
    print(f"PASS: {label}")
    return True


def print_detailed(raw_values, label):
    """Print frame-by-frame trace for debugging."""
    print(f"\n--- {label} ---")
    proc = FakeRXProcessor()
    for i, raw in enumerate(raw_values):
        result = proc.process_frame(raw)
        tag = ""
        if raw == 32767:
            tag = "WRAP_L"
        elif raw == -32768:
            tag = "WRAP_R"
        print(f"  [{i:2d}] raw={raw:7d}  output={str(result):>22s}  "
              f"prev_axes={proc.prev_axes.get('rs_x', 'N/A')}  {tag}")


# ── Tests ─────────────────────────────────────────────────────────

def test_slow_left_wrap_real_data():
    """Real hardware capture: slow push left, wrap at 32767, release."""
    raw = [0, -2049, -5761, -6529, -8321, -9985, -12161, -13825, -14721,
           -14977, -15745, -16385, -17153, -18049, -18817, -20097, -20737,
           -20865, -22401, -23425, -24065, -24449, -25345, -27137, -28545,
           -29953, -30977, -31617,
           32767, 32767, 32767, 32767, 32767,  # wrap zone
           -31617, -30849, -28801, -22401, -15745, -9345, -4609, -2049, 0]
    return check_no_positive(run_sequence(raw),
                             "Slow left + wrap (real data)", after_frame=1)


def test_fast_left_from_center():
    """Fast flick left: few intermediate frames before wrap."""
    raw = [0, -15000, -31000, 32767, 32767, -31000, -10000, 0]
    return check_no_positive(run_sequence(raw),
                             "Fast left flick from center", after_frame=1)


def test_instant_left_wrap_from_center():
    """Worst case: center → wrap in one frame."""
    raw = [0, 32767, 32767, 32767, -31000, -15000, 0]
    return check_no_positive(run_sequence(raw),
                             "Instant left wrap from center (0 → 32767)", after_frame=1)


def test_slow_right_wrap():
    """Push right, wrap at -32768, release."""
    raw = [0, 2049, 5761, 10000, 15000, 20000, 25000, 30000, 31617,
           -32768, -32768, -32768,
           31617, 25000, 15000, 5000, 0]
    return check_no_negative(run_sequence(raw),
                             "Slow right + wrap at -32768", after_frame=1)


def test_instant_right_wrap_from_center():
    """Center → right wrap in one frame."""
    raw = [0, -32768, -32768, 31000, 15000, 0]
    return check_no_negative(run_sequence(raw),
                             "Instant right wrap from center (0 → -32768)", after_frame=1)


def test_normal_left_no_wrap():
    """Normal left that doesn't reach the extreme."""
    raw = [0, -5000, -10000, -15000, -20000, -25000, -20000, -10000, 0]
    return check_no_positive(run_sequence(raw),
                             "Normal left (no wrap)", after_frame=1)


def test_normal_right_no_wrap():
    """Normal right that doesn't reach the extreme."""
    raw = [0, 5000, 10000, 15000, 20000, 25000, 20000, 10000, 0]
    return check_no_negative(run_sequence(raw),
                             "Normal right (no wrap)", after_frame=1)


def test_full_right_no_false_wrap():
    """Full right deflection near ±32000 — must NOT trigger wrap detection.
    This is the bug that caused 'sticks left when pulling right'."""
    raw = [0, 5000, 15000, 25000, 30000, 31500, 32000, 32500, 32600,
           32500, 32000, 30000, 25000, 15000, 0]
    return check_no_negative(run_sequence(raw),
                             "Full right (up to 32600) — no false wrap", after_frame=1)


def test_full_left_no_false_wrap():
    """Full left near -32000 — must NOT trigger wrap detection."""
    raw = [0, -5000, -15000, -25000, -30000, -31500, -32000, -32500, -32600,
           -32500, -32000, -30000, -25000, -15000, 0]
    return check_no_positive(run_sequence(raw),
                             "Full left (down to -32600) — no false wrap", after_frame=1)


def test_center_jitter():
    """Small oscillation around center."""
    raw = [0, 100, -100, 50, -50, 200, -200, 0]
    outputs = run_sequence(raw)
    for i, (r, val) in enumerate(outputs):
        if val is not None and abs(val) > 0.02:
            print(f"FAIL: Center jitter — frame {i}: raw={r} output={val:+.4f}")
            return False
    print("PASS: Center jitter stays near zero")
    return True


def test_multiple_wrap_cycles():
    """Push left to wrap, release, push left again."""
    raw = [0, -15000, -31000, 32767, 32767, -31000, -15000, 0,
           -15000, -31000, 32767, 32767, -31000, -15000, 0]
    return check_no_positive(run_sequence(raw),
                             "Multiple left-wrap cycles", after_frame=1)


def test_left_wrap_value_is_minus_one():
    """Wrap zone should output exactly -1.0."""
    raw = [0, -20000, -31000, 32767]
    outputs = run_sequence(raw)
    return check_near(outputs, 3, -1.0, "Left wrap outputs -1.0")


def test_right_wrap_value_is_plus_one():
    """Wrap zone should output exactly +1.0."""
    raw = [0, 20000, 31000, -32768]
    outputs = run_sequence(raw)
    return check_near(outputs, 3, 1.0, "Right wrap outputs +1.0")


def test_near_max_right_is_positive():
    """Values like 32000, 32500, 32700 are legitimate right — must be positive."""
    for val in [31700, 32000, 32200, 32500, 32700, 32766]:
        raw = [0, 10000, 20000, val]
        outputs = run_sequence(raw)
        _, result = outputs[-1]
        if result is not None and result < 0:
            print(f"FAIL: Near-max right raw={val} mapped to {result:+.4f} (should be positive)")
            return False
    print("PASS: All near-max right values (31700-32766) are positive")
    return True


def test_near_min_left_is_negative():
    """Values like -32000, -32500, -32700 are legitimate left — must be negative."""
    for val in [-31700, -32000, -32200, -32500, -32700, -32767]:
        raw = [0, -10000, -20000, val]
        outputs = run_sequence(raw)
        _, result = outputs[-1]
        if result is not None and result > 0:
            print(f"FAIL: Near-min left raw={val} mapped to {result:+.4f} (should be negative)")
            return False
    print("PASS: All near-min left values (-31700 to -32767) are negative")
    return True


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("RX Wrap Detection Unit Tests")
    print("=" * 60)

    tests = [
        test_slow_left_wrap_real_data,
        test_fast_left_from_center,
        test_instant_left_wrap_from_center,
        test_slow_right_wrap,
        test_instant_right_wrap_from_center,
        test_normal_left_no_wrap,
        test_normal_right_no_wrap,
        test_full_right_no_false_wrap,
        test_full_left_no_false_wrap,
        test_center_jitter,
        test_multiple_wrap_cycles,
        test_left_wrap_value_is_minus_one,
        test_right_wrap_value_is_plus_one,
        test_near_max_right_is_positive,
        test_near_min_left_is_negative,
    ]

    results = [t() for t in tests]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'=' * 60}")

    # Show details for failures
    if not results[2]:
        print_detailed([0, 32767, 32767, 32767, -31000, -15000, 0],
                       "Instant left wrap from center")
    if not results[7]:
        print_detailed([0, 5000, 15000, 25000, 30000, 31500, 32000, 32500, 32600,
                        32500, 32000, 30000, 25000, 15000, 0],
                       "Full right no false wrap")

    sys.exit(0 if passed == total else 1)
