"""
Test Suite for ESP32 Firmware Local Buzzer Policy
Simulates the exact C++ state machine in firmware/sentinel_x_esp32/sentinel_x_esp32.ino
Validates Rules 1-9 without requiring physical hardware.
"""

import unittest

class ESP32BuzzerStateMachine:
    def __init__(self):
        self.TEMP_ALERT_HIGH_C = 31.0
        self.TEMP_ALERT_LOW_C = 30.0
        self.TEMP_BUZZER_DURATION_MS = 60000

        # Distinct States (Rule 9)
        self.emergency_pressed = False         # SOS state
        self.temp_alert_active = False         # Alert state
        self.temp_alert_status = "NORMAL"
        self.temp_buzzer_sounding = False      # Alert sound state
        self.temp_buzzer_start_ms = 0

        # Physical pin output
        self.buzzer_hardware_active = False
        self.buzzer_active_source = "OFF"
        self.current_time_ms = 0

    def sample_emergency_button(self, pin_state_low: bool):
        """Active-LOW input with internal pull-up (GPIO27)."""
        self.emergency_pressed = pin_state_low

    def sample_dht(self, temp: float or None, valid: bool = True):
        """Simulates sampleDHT() in firmware."""
        if valid and temp is not None:
            if temp > self.TEMP_ALERT_HIGH_C:
                if not self.temp_alert_active:
                    # Rule 2: When a NEW valid temperature alert starts above 31°C, sound buzzer
                    self.temp_alert_active = True
                    self.temp_alert_status = "ALERT"
                    self.temp_buzzer_sounding = True
                    self.temp_buzzer_start_ms = self.current_time_ms
                else:
                    # Rule 4: Do not restart 60s timer repeatedly while same alert remains active
                    self.temp_alert_status = "ALERT"
            elif temp <= self.TEMP_ALERT_LOW_C:
                # Rule 5: Clear temperature alert at or below 30°C
                if self.temp_alert_active:
                    self.temp_alert_active = False
                    self.temp_alert_status = "NORMAL"
                    self.temp_buzzer_sounding = False
                else:
                    self.temp_alert_status = "NORMAL"
            # Hysteresis band (30.0 < temp <= 31.0): maintain state
        else:
            # Rule 3 (from prior req): Missing/invalid data never clears alert
            if self.temp_alert_active:
                self.temp_alert_status = "ALERT"
            else:
                self.temp_alert_status = "UNAVAILABLE"

    def update_local_alerts(self, now_ms: int):
        """Simulates updateLocalAlerts() in firmware."""
        self.current_time_ms = now_ms

        # Rule 3: After 60 seconds, silence temperature buzzer while keeping temp alert active
        if self.temp_buzzer_sounding:
            if now_ms - self.temp_buzzer_start_ms >= self.TEMP_BUZZER_DURATION_MS:
                self.temp_buzzer_sounding = False

        # Priority Arbitration
        if self.emergency_pressed:
            # Rule 1: SOS takes absolute priority over everything
            self.buzzer_hardware_active = True
            self.buzzer_active_source = "SOS"
        elif self.temp_buzzer_sounding:
            # Rule 2: Non-blocking pulse pattern (250ms ON / 250ms OFF)
            pulse = (((now_ms - self.temp_buzzer_start_ms) // 250) % 2 == 0)
            self.buzzer_hardware_active = pulse
            self.buzzer_active_source = "TEMP_ALERT"
        else:
            self.buzzer_hardware_active = False
            self.buzzer_active_source = "OFF"


class TestBuzzerPolicy(unittest.TestCase):
    def setUp(self):
        self.esp = ESP32BuzzerStateMachine()

    def test_rule_1_sos_immediate_activation_and_priority(self):
        """Pressing SOS must activate local buzzer immediately and override any other alert."""
        # Baseline: normal conditions
        self.esp.sample_dht(25.0, valid=True)
        self.esp.update_local_alerts(1000)
        self.assertFalse(self.esp.buzzer_hardware_active)

        # Press SOS
        self.esp.sample_emergency_button(pin_state_low=True)
        self.esp.update_local_alerts(1050)
        self.assertTrue(self.esp.buzzer_hardware_active)
        self.assertEqual(self.esp.buzzer_active_source, "SOS")

    def test_rule_2_and_3_temp_trigger_and_60s_timeout(self):
        """New temp alert > 31°C sounds buzzer up to 60s, then silences while alert stays active."""
        # t = 1000 ms: Temp rises to 32.0°C (Trigger)
        self.esp.current_time_ms = 1000
        self.esp.sample_dht(32.0, valid=True)
        self.esp.update_local_alerts(1000)

        self.assertTrue(self.esp.temp_alert_active)
        self.assertTrue(self.esp.temp_buzzer_sounding)
        self.assertEqual(self.esp.buzzer_active_source, "TEMP_ALERT")

        # t = 30000 ms (30s in): Still sounding
        self.esp.update_local_alerts(30000)
        self.assertTrue(self.esp.temp_buzzer_sounding)

        # t = 60999 ms (59.999s elapsed): Still sounding
        self.esp.update_local_alerts(60999)
        self.assertTrue(self.esp.temp_buzzer_sounding)

        # t = 61001 ms (60.001s elapsed): Timed out!
        self.esp.update_local_alerts(61001)
        self.assertFalse(self.esp.temp_buzzer_sounding, "Buzzer must silence after 60 seconds")
        self.assertFalse(self.esp.buzzer_hardware_active, "Hardware buzzer pin must turn off")
        self.assertEqual(self.esp.buzzer_active_source, "OFF")
        # Rule 3: Keep temperature alert active on the dashboard
        self.assertTrue(self.esp.temp_alert_active, "Temperature alert MUST remain active on dashboard")
        self.assertEqual(self.esp.temp_alert_status, "ALERT")

    def test_rule_4_no_repeated_timer_restart(self):
        """Do not restart 60s timer repeatedly while the same temperature alert remains active."""
        # t = 1000 ms: Trigger temp alert
        self.esp.current_time_ms = 1000
        self.esp.sample_dht(32.0, valid=True)
        self.assertEqual(self.esp.temp_buzzer_start_ms, 1000)

        # t = 10000 ms: Another reading arrives at 33.5°C
        self.esp.current_time_ms = 10000
        self.esp.sample_dht(33.5, valid=True)
        # Start time must remain 1000 ms!
        self.assertEqual(self.esp.temp_buzzer_start_ms, 1000, "Timer must NOT restart on subsequent readings")

        # t = 20000 ms: Reading in hysteresis band (30.5°C)
        self.esp.current_time_ms = 20000
        self.esp.sample_dht(30.5, valid=True)
        self.assertEqual(self.esp.temp_buzzer_start_ms, 1000)

        # At t = 61000 ms, buzzer must silence based on original t=1000 start
        self.esp.update_local_alerts(61000)
        self.assertFalse(self.esp.temp_buzzer_sounding)

    def test_rule_5_clear_at_or_below_30(self):
        """Clear temperature alert at or below 30.0°C when valid."""
        # Trigger
        self.esp.current_time_ms = 1000
        self.esp.sample_dht(32.0, valid=True)
        self.assertTrue(self.esp.temp_alert_active)

        # Drop to 30.0°C (Boundary)
        self.esp.current_time_ms = 2000
        self.esp.sample_dht(30.0, valid=True)
        self.assertFalse(self.esp.temp_alert_active)
        self.assertEqual(self.esp.temp_alert_status, "NORMAL")
        self.assertFalse(self.esp.temp_buzzer_sounding)

    def test_rule_6_releasing_sos_behavior(self):
        """Releasing SOS must stop SOS buzzer unless temperature buzzer requires it."""
        # Scenario A: SOS pressed during active 60s temp alert window
        self.esp.current_time_ms = 1000
        self.esp.sample_dht(32.0, valid=True)
        self.esp.update_local_alerts(1000)

        # Press SOS at t=5000
        self.esp.sample_emergency_button(pin_state_low=True)
        self.esp.update_local_alerts(5000)
        self.assertEqual(self.esp.buzzer_active_source, "SOS")

        # Release SOS at t=10000 (still within 60s temp alert window)
        self.esp.sample_emergency_button(pin_state_low=False)
        self.esp.update_local_alerts(10000)
        # Buzzer must resume TEMP_ALERT pattern
        self.assertEqual(self.esp.buzzer_active_source, "TEMP_ALERT")
        self.assertTrue(self.esp.temp_buzzer_sounding)

        # Scenario B: SOS pressed after temp buzzer 60s timeout
        # Advance time to t=70000 ms (temp buzzer already timed out)
        self.esp.update_local_alerts(70000)
        self.assertFalse(self.esp.temp_buzzer_sounding)
        self.assertEqual(self.esp.buzzer_active_source, "OFF")

        # Press SOS at t=75000
        self.esp.sample_emergency_button(pin_state_low=True)
        self.esp.update_local_alerts(75000)
        self.assertEqual(self.esp.buzzer_active_source, "SOS")

        # Release SOS at t=80000
        self.esp.sample_emergency_button(pin_state_low=False)
        self.esp.update_local_alerts(80000)
        # Must turn completely OFF because temp buzzer already timed out
        self.assertEqual(self.esp.buzzer_active_source, "OFF")
        self.assertFalse(self.esp.buzzer_hardware_active)
        # Dashboard alert is still active
        self.assertTrue(self.esp.temp_alert_active)

    def test_rule_9_state_separation(self):
        """Ensure alert state, alert sound state, and SOS state are distinct."""
        # State 1: Alert active, sound active, SOS inactive
        self.esp.current_time_ms = 1000
        self.esp.sample_dht(32.0, valid=True)
        self.esp.update_local_alerts(1000)
        self.assertTrue(self.esp.temp_alert_active)
        self.assertTrue(self.esp.temp_buzzer_sounding)
        self.assertFalse(self.esp.emergency_pressed)

        # State 2: Alert active, sound inactive (timed out), SOS inactive
        self.esp.update_local_alerts(62000)
        self.assertTrue(self.esp.temp_alert_active)
        self.assertFalse(self.esp.temp_buzzer_sounding)
        self.assertFalse(self.esp.emergency_pressed)

        # State 3: Alert active, sound inactive, SOS active
        self.esp.sample_emergency_button(pin_state_low=True)
        self.esp.update_local_alerts(63000)
        self.assertTrue(self.esp.temp_alert_active)
        self.assertFalse(self.esp.temp_buzzer_sounding)
        self.assertTrue(self.esp.emergency_pressed)


if __name__ == "__main__":
    unittest.main()
