from __future__ import annotations

import time
from typing import Any, Dict, Optional

try:
    from adafruit_servokit import ServoKit  # type: ignore
    HAS_BONNET = True
except Exception:
    ServoKit = None  # type: ignore
    HAS_BONNET = False


class ServoRig:
    """
    Controls servos through the Adafruit Servo Bonnet (PCA9685 via ServoKit).
    """

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.initialized = False
        self.kit: Optional["ServoKit"] = None

        # Track current resting angle of the space-bar servo (REST or PRESS)
        self.space_current_angle: Optional[float] = None

    def setup(self) -> None:
        if not HAS_BONNET:
            raise RuntimeError("adafruit_servokit is not available. Install it and run on a Pi with the bonnet connected.")

        # Initialize the bonnet (16 channels)
        self.kit = ServoKit(channels=16)

        ch = self.cfg["channels"]
        ang = self.cfg["angles"]
        t = self.cfg["timing"]

        # Rest positions for BLUE, CORRECTION, RETURN.
        # Keep SPACEBAR untouched here (matches your current behavior and avoids accidental triggering at start).
        self._move(ch["BLUE"], float(ang["BLUE_REST_ANGLE"]), delay=float(t["SERVO_REST_MOVE_DELAY"]), release=True)
        self._move(ch["CORRECTION"], float(ang["CORR_REST_ANGLE"]), delay=float(t["SERVO_REST_MOVE_DELAY"]), release=True)
        self._move(ch["RETURN"], float(ang["RETURN_REST_ANGLE"]), delay=float(t["SERVO_REST_MOVE_DELAY"]), release=True)

        # Initialize space state without moving servo
        self.space_current_angle = float(ang["SPACE_REST_ANGLE"])

        self.initialized = True

    def cleanup(self) -> None:
        if not self.kit:
            return
        # Release pulses to reduce jitter
        for key in ("SPACEBAR", "BLUE", "CORRECTION", "RETURN"):
            try:
                channel = int(self.cfg["channels"][key])
                self.kit.servo[channel].angle = None
            except Exception:
                pass
        self.initialized = False

    def _move(self, channel: int, angle: float, delay: float, release: bool = True) -> None:
        if not self.kit:
            raise RuntimeError("ServoRig not initialized.")
        servo = self.kit.servo[int(channel)]
        servo.angle = float(angle)
        time.sleep(max(0.0, float(delay)))
        if release:
            servo.angle = None

    def _hold(self, channel: int, angle: float) -> None:
        if not self.kit:
            raise RuntimeError("ServoRig not initialized.")
        self.kit.servo[int(channel)].angle = float(angle)

    # Actions
    def press_blue_key(self) -> None:
        ch = self.cfg["channels"]
        ang = self.cfg["angles"]
        t = self.cfg["timing"]

        self._move(ch["BLUE"], float(ang["BLUE_PRESS_ANGLE"]), delay=float(t["PRESS_TIME"]), release=True)
        self._move(ch["BLUE"], float(ang["BLUE_REST_ANGLE"]), delay=float(t["PRESS_TIME"]), release=True)
        time.sleep(float(t["POST_BLUE_JITTER_DELAY"]))
    """

    # FUNCTION BELOW WILL SWEEP SPACEBAR FROM ONE ANGLE TO ANOTHER EACH TIME IT IS CALLED- DOES NOT RETURN TO REST POSITION
    # REQUIRES GREATER REST TIMES TO ACCOUNT FOR WIDE CHANGE IN ANGLE

    def press_spacebar(self) -> None:
        ch = self.cfg["channels"]
        ang = self.cfg["angles"]
        t = self.cfg["timing"]

        if self.space_current_angle is None:
            self.space_current_angle = float(ang["SPACE_REST_ANGLE"])

        rest_angle = float(ang["SPACE_REST_ANGLE"])
        other_angle = float(ang["SPACE_PRESS_ANGLE"])

        new_angle = other_angle if self.space_current_angle == rest_angle else rest_angle

        self._move(ch["SPACEBAR"], new_angle, delay=float(t["SPACE_TOGGLE_DELAY"]), release=True)
        self.space_current_angle = new_angle
    """
        
        
    def press_spacebar(self) -> None:
        ch = self.cfg["channels"]
        ang = self.cfg["angles"]
        t = self.cfg["timing"]

        press_angle = float(ang["SPACE_PRESS_ANGLE"])
        rest_angle = float(ang["SPACE_REST_ANGLE"])
        press_time = float(t["PRESS_TIME"])
        press_time_spacebar_down = press_time + 0.25 

        # Press then return, like BLUE/RETURN
        self._move(ch["SPACEBAR"], press_angle, delay=press_time_spacebar_down, release=True)
        self._move(ch["SPACEBAR"], rest_angle, delay=press_time, release=True)

        # Keep state consistent for any other code that checks it
        self.space_current_angle = rest_angle


    def press_return(self) -> None:
        ch = self.cfg["channels"]
        ang = self.cfg["angles"]
        t = self.cfg["timing"]

        self._move(ch["RETURN"], float(ang["RETURN_PRESS_ANGLE"]), delay=float(t["RETURN_PRESS_HOLD"]), release=True)
        self._move(ch["RETURN"], float(ang["RETURN_REST_ANGLE"]), delay=float(t["PRESS_TIME"]), release=True)
        time.sleep(float(t["NEW_LINE_DELAY"]))

    def engage_correction(self) -> None:
        ch = self.cfg["channels"]
        ang = self.cfg["angles"]
        t = self.cfg["timing"]

        self._hold(ch["CORRECTION"], float(ang["CORR_HOLD_ANGLE"]))
        time.sleep(float(t["CORR_ENGAGE_DELAY"]))

    def release_correction(self) -> None:
        ch = self.cfg["channels"]
        ang = self.cfg["angles"]
        t = self.cfg["timing"]

        self._move(ch["CORRECTION"], float(ang["CORR_REST_ANGLE"]), delay=float(t["CORR_RELEASE_MOVE_DELAY"]), release=True)
        time.sleep(float(t["CORR_RELEASE_PAUSE"]))

    def move_spacebar_to_rest(self) -> None:
        ch = self.cfg["channels"]
        ang = self.cfg["angles"]
        t = self.cfg["timing"]

        rest_angle = float(ang["SPACE_REST_ANGLE"])
        if self.space_current_angle is None:
            self.space_current_angle = rest_angle

        if self.space_current_angle != rest_angle:
            self._move(ch["SPACEBAR"], rest_angle, delay=float(t["SPACE_REST_MOVE_DELAY"]), release=True)
            self.space_current_angle = rest_angle
