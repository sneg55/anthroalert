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
    """Use camera to detect if a person is present."""
    # First try direct OpenCV access
    try:
        import cv2
        
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            
            if ret and frame is not None:
                face_cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
                )
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.1, 4)
                
                detected = len(faces) > 0
                print(f"Face detection: {'detected' if detected else 'not detected'}")
                return detected
        
        # If OpenCV fails, try via osascript/imagesnap
        print("OpenCV camera access denied, trying imagesnap fallback...")
        return _face_detect_via_imagesnap()
        
    except ImportError:
        print("OpenCV not installed, trying imagesnap fallback...")
        return _face_detect_via_imagesnap()
    except Exception as e:
        print(f"Face detection failed: {e}")
        return False


def _face_detect_via_imagesnap() -> bool:
    """Fallback face detection using imagesnap CLI (if installed)."""
    import tempfile
    import os
    
    try:
        # Check if imagesnap is available
        result = subprocess.run(["which", "imagesnap"], capture_output=True, text=True)
        if result.returncode != 0:
            print("imagesnap not installed (brew install imagesnap)")
            return False
        
        # Capture image
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            tmp_path = f.name
        
        result = subprocess.run(
            ["imagesnap", "-q", tmp_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0 or not os.path.exists(tmp_path):
            print("imagesnap capture failed")
            return False
        
        # Detect face in captured image
        import cv2
        frame = cv2.imread(tmp_path)
        os.unlink(tmp_path)
        
        if frame is None:
            return False
        
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        
        detected = len(faces) > 0
        print(f"Face detection (imagesnap): {'detected' if detected else 'not detected'}")
        return detected
        
    except Exception as e:
        print(f"imagesnap fallback failed: {e}")
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
