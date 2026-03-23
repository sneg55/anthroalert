#!/usr/bin/env python3
"""
Presence detection for AnthroAlert.
Only sends alerts when user is at the computer (face detected via camera).
"""

import subprocess
import sys
from pathlib import Path


def is_user_present() -> bool:
    """Check if user is present using camera face detection."""
    script_dir = Path(__file__).parent
    app_path = script_dir / "FaceCheck.app"
    result_path = Path("/tmp/facecheck_result.txt")
    
    if not app_path.exists():
        print("FaceCheck.app not found - assuming present")
        return True
    
    try:
        # Remove old result
        if result_path.exists():
            result_path.unlink()
        
        # Run FaceCheck app
        subprocess.run(
            ["open", "-W", str(app_path)],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Read result
        if result_path.exists():
            content = result_path.read_text().strip()
            present = content == "PRESENT"
            print(f"Face check: {content}")
            return present
        else:
            print("FaceCheck.app no result - assuming present")
            return True
            
    except subprocess.TimeoutExpired:
        print("FaceCheck.app timed out - assuming present")
        return True
    except Exception as e:
        print(f"FaceCheck.app error: {e} - assuming present")
        return True


if __name__ == "__main__":
    present = is_user_present()
    print(f"Result: {'PRESENT' if present else 'AWAY'}")
    sys.exit(0 if present else 1)
