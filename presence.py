#!/usr/bin/env python3
"""
Presence detection for AnthroAlert.
Only sends alerts when user is at the computer.
"""

import subprocess
import sys

# Thresholds
IDLE_THRESHOLD_SECONDS = 900  # 15 minutes
IDLE_CAMERA_FALLBACK_SECONDS = 3600  # 1 hour - use camera if idle 15-60 min


def get_idle_time() -> int:
    """Get seconds since last keyboard/mouse input."""
    try:
        result = subprocess.run(
            ["/usr/sbin/ioreg", "-c", "IOHIDSystem"],
            capture_output=True,
            text=True,
            timeout=5
        )
        for line in result.stdout.split("\n"):
            if "HIDIdleTime" in line:
                # Extract nanoseconds and convert to seconds
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "=" and i + 1 < len(parts):
                        ns = int(parts[i + 1])
                        return ns // 1_000_000_000
        return 0
    except Exception as e:
        print(f"Idle time check failed: {e}")
        return 0


def is_screen_locked() -> bool:
    """Check if screen is locked."""
    try:
        result = subprocess.run(
            [
                "python3", "-c",
                "import Quartz; print(Quartz.CGSessionCopyCurrentDictionary().get('CGSSessionScreenIsLocked', 0))"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip() == "1"
    except Exception as e:
        print(f"Screen lock check failed: {e}")
        return False


def is_face_detected() -> bool:
    """Use FaceCheck.app to detect if a person is present."""
    import os
    from pathlib import Path
    
    # Path to the FaceCheck app
    script_dir = Path(__file__).parent
    app_path = script_dir / "FaceCheck.app"
    result_path = Path("/tmp/facecheck_result.txt")
    
    if not app_path.exists():
        print("FaceCheck.app not found")
        return False
    
    try:
        # Remove old result
        if result_path.exists():
            result_path.unlink()
        
        # Run FaceCheck app (must use 'open' to get camera permission)
        result = subprocess.run(
            ["open", "-W", str(app_path)],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Read result
        if result_path.exists():
            content = result_path.read_text().strip()
            detected = content == "PRESENT"
            print(f"Face detection (FaceCheck.app): {content}")
            return detected
        else:
            print("FaceCheck.app did not produce result")
            return False
            
    except subprocess.TimeoutExpired:
        print("FaceCheck.app timed out")
        return False
    except Exception as e:
        print(f"FaceCheck.app failed: {e}")
        return False


def is_user_present() -> bool:
    """
    Check if user is present at the computer.
    
    Logic:
    1. If screen locked → not present
    2. If idle < 15 min → present (active use)
    3. If idle 15-60 min → check camera (might be reading)
    4. If idle > 60 min → not present
    """
    # Check screen lock first
    if is_screen_locked():
        print("Screen is locked - user not present")
        return False
    
    # Check idle time
    idle_seconds = get_idle_time()
    print(f"Idle time: {idle_seconds}s ({idle_seconds // 60}m)")
    
    if idle_seconds < IDLE_THRESHOLD_SECONDS:
        print("User active (recent input)")
        return True
    
    if idle_seconds > IDLE_CAMERA_FALLBACK_SECONDS:
        print("User away (idle > 1 hour)")
        return False
    
    # Idle 15-60 min - try camera fallback, but don't require it
    print("Checking camera (idle 15-60 min)...")
    try:
        result = is_face_detected()
        if result:
            return True
        # Camera failed or no face - be conservative, assume away
        print("Camera check inconclusive, assuming away")
        return False
    except Exception:
        # Camera not available - fall back to assuming away if idle > 15 min
        print("Camera unavailable, assuming away (idle > 15 min)")
        return False


if __name__ == "__main__":
    present = is_user_present()
    print(f"\nResult: User {'PRESENT' if present else 'AWAY'}")
    sys.exit(0 if present else 1)
