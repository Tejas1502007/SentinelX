# SENTINEL-X — Phase 1 Implementation Specification
## Derived Parameters, Controlled IMU Sampling, and Sensor Data Validation

### 1. Project context

SENTINEL-X is an IoT worker-safety prototype using:

- ESP32-WROOM-32 development board
- MPU6050-compatible GY-521 IMU
- DHT22 temperature and humidity sensor
- MQ-135 analog gas-sensor module
- Emergency SOS push button
- Arduino firmware
- Python Flask backend
- `serial_reader.py` for reading ESP32 JSON telemetry and storing readings in SQLite
- React + TypeScript + Vite frontend

The current system is operational. The React frontend receives telemetry from Flask, and the charts, history, and dashboard have been tested.

**The goal of Phase 1 is to improve sensor sampling, calculate useful motion features, add timestamps and rolling windows, and validate readings without breaking the existing application.**

### 2. Mandatory safety and compatibility constraints

Before editing any files:

1. Inspect the existing ESP32 `.ino` firmware, `app.py`, `serial_reader.py`, frontend API client, and relevant database schema.
2. Identify the current serial JSON format and all fields consumed by the backend and frontend.
3. Create a backup or Git commit of the known-working version.
4. Make a short implementation plan and identify every file that needs to change.
5. Preserve existing functionality unless a specific change is required and approved.

Do not rewrite the entire application or replace the working firmware with a generic example.

Do not change the existing API routes or remove/rename existing telemetry fields. Prefer additive, backward-compatible changes.

Do not modify the SQLite schema unless necessary. If a schema change is proposed, explain why and provide a migration and rollback plan first.

Do not implement fall detection, new alert behavior, Supabase integration, or automatic buzzer activation in this phase.

This is an educational prototype, not a certified life-safety system. Derived features and data-validation flags must not be presented as proof that a worker is safe.

---

### 3. Requirement A — Controlled MPU6050 sampling at 50 Hz

#### Target

Establish a nominal IMU sampling interval of 20 milliseconds (50 samples per second).

Use a non-blocking or deadline-based scheduler based on `micros()` or another appropriate monotonic timer. Do not use a long `delay(20)` in the main loop because the SOS button and other sensor processing must remain responsive.

#### Implementation requirements

- Separate IMU acquisition from telemetry transmission.
- Schedule each IMU sample against a fixed deadline.
- Record the acquisition timestamp using the ESP32 monotonic clock.
- Avoid performing repeated I2C reads for the same sample.
- Avoid uncontrolled catch-up loops when the ESP32 misses a deadline.
- If the loop is late, record the timing delay or missed-sample counter and resume on the next appropriate deadline.
- Keep the emergency button polling responsive.
- Preserve DHT22's practical minimum reading interval. Continue reading it approximately every two seconds unless the existing code or sensor requirements indicate otherwise.
- Sample the MQ-135 analog input at a separate, slower rate, initially around once per second. Do not interpret the ADC value as calibrated ppm.
- Keep serial output rate separately configurable. The 50 Hz internal IMU sampling rate does not require sending 50 complete JSON records per second.
- Avoid dynamic memory allocation in high-frequency sampling paths where practical.

#### Timing metrics

Maintain the following diagnostic values:

- `imu_sample_count`: total successful IMU samples since boot, with defined rollover behavior.
- `imu_sample_interval_us`: interval between consecutive successful IMU samples.
- `imu_missed_deadlines`: number of sampling deadlines missed or skipped.
- `imu_last_sample_ms`: timestamp of the most recent successful sample in the monotonic boot-time clock.

Define the precise meaning of missed deadlines and keep it consistent.

A nominal 50 Hz target is not proof of exact 50 Hz sampling. Validate actual intervals and report timing quality.

---

### 4. Requirement B — Calculate derived motion parameters

Use calibrated or documented sensor conversion factors appropriate to the existing MPU6050 register configuration.

The current firmware configures accelerometer and gyroscope range registers to their default ±2 g and ±250 degrees/second settings. Preserve these ranges unless there is a documented reason to change them.

#### Acceleration magnitude

Given acceleration components in metres per second squared:

`accel_x_mps2`, `accel_y_mps2`, `accel_z_mps2`

Calculate:

`accel_magnitude_mps2 = sqrt(x*x + y*y + z*z)`

Convert to gravitational units:

`accel_magnitude_g = accel_magnitude_mps2 / 9.80665`

Important: the magnitude normally includes gravity. A stationary, level sensor should have a magnitude near 9.81 m/s², or approximately 1 g. Do not subtract gravity implicitly or label this value as linear acceleration.

#### Gyroscope magnitude

Given angular-velocity components in radians per second:

`gyro_x_rads`, `gyro_y_rads`, `gyro_z_rads`

Calculate:

`gyro_magnitude_rads = sqrt(gx*gx + gy*gy + gz*gz)`

If the current firmware exposes gyroscope values in another unit, preserve the existing fields and make the unit conversion explicit. Never mix degrees per second and radians per second.

#### Numerical validation

- Check for non-finite values before publishing derived results.
- Avoid integer overflow by converting raw values to floating-point values before multiplication.
- Preserve the original axis measurements.
- Calculate magnitude only when all three corresponding axis values are valid.
- Mark derived values unavailable when their source sample is invalid.
- Do not silently replace invalid values with zero.

#### Expected static test

With the IMU resting on a stable surface:

- Acceleration magnitude should be near 9.81 m/s².
- Acceleration magnitude should be near 1 g.
- Gyroscope magnitude should be relatively low, although noise and sensor bias are expected.

Use this as a sanity check, not a calibrated accuracy guarantee.

---

### 5. Requirement C — Timestamps and rolling time windows

Use two distinct concepts of time.

#### Monotonic timestamp

The ESP32 should timestamp each IMU sample using a monotonic clock such as `micros()`.

Use this for:

- Sampling intervals
- Elapsed time
- Rolling-window membership
- Timing diagnostics

The timestamp must not be treated as a Unix timestamp or calendar date.

#### Wall-clock timestamp

If the host computer or backend supplies a UTC timestamp, keep it separate from the device's monotonic timestamp.

Do not assume the ESP32 has a valid UTC clock unless time synchronisation has been implemented.

#### Rolling windows

Maintain a bounded, fixed-size or otherwise memory-bounded buffer of recent valid IMU samples.

For the initial implementation:

- Sampling target: 50 Hz
- Rolling window duration: 1 second
- Expected samples in a full window: approximately 50
- Use timestamps rather than assuming every sample arrived exactly 20 ms apart.
- Exclude invalid samples from feature calculations while retaining diagnostics about their occurrence.
- Track the number of valid samples and the time span covered by each window.
- Mark a window incomplete when there are too few valid samples or excessive gaps.
- Do not calculate a feature from a stale or insufficient window without identifying that limitation.

Calculate at least:

- Window sample count
- Window duration
- Mean acceleration magnitude
- Peak acceleration magnitude
- Mean gyroscope magnitude
- Peak gyroscope magnitude

If appropriate, also calculate the standard deviation of acceleration magnitude. Define whether it is population or sample standard deviation and test the implementation.

Use the same units as the corresponding instantaneous values.

Do not implement fall classification in this phase. These are motion features for later evaluation, not validated fall indicators.

---

### 6. Requirement D — Validate missing, stale, and implausible readings

Implement explicit validation states instead of relying on a single numeric value.

#### Suggested status categories

- `OK`: latest sample is valid and fresh.
- `STALE`: no successful sample has arrived within the configured freshness limit.
- `INVALID`: the sample contains invalid, non-finite, or out-of-range values.
- `INITIALIZING`: insufficient samples have arrived to assess health.
- `ERROR`: a sensor communication or acquisition error has been detected.

If multiple conditions occur, define a consistent precedence rule and preserve diagnostic detail.

#### MPU6050 validation

Check:

- I2C communication or register-read success.
- Successful sample timestamp.
- Finite axis values and derived features.
- Expected raw or converted ranges for the configured sensor range.
- Sample interval and timing gaps.
- Latest-sample age.
- Rolling-window completeness.

Use documented sensor limits and configuration for range checks. Do not label a value implausible simply because it is unusual during movement.

A raw reading within the configured hardware range does not guarantee that the sensor is functioning correctly.

#### DHT22 validation

Check:

- Whether the reading succeeded.
- Whether temperature and humidity are finite.
- Whether values fall within documented sensor ranges.
- Whether the reading is fresh relative to the expected update interval.

Preserve the existing behavior for failed DHT22 reads, but expose an explicit status instead of presenting a stale reading as a new measurement.

#### MQ-135 validation

Check:

- ADC read availability and configured ADC range.
- Freshness of the latest reading.
- Whether the value is within the electrical measurement range.
- Whether the sensor is warming up or awaiting baseline calibration, if that state is implemented.

Do not convert raw ADC values to ppm without an appropriate calibration procedure and documented conversion model.

Do not use an uncalibrated MQ-135 reading to claim that the air is safe.

#### Validation metadata

For each sensor, expose enough metadata to determine:

- Last successful sample time
- Sample age
- Current status
- Recent error or invalid-sample count

Keep diagnostic fields separate from the measured values.

---

### 7. Requirement E — Preserve the serial JSON contract

The current firmware emits JSON telemetry that is consumed by `serial_reader.py`. The backend exposes existing Flask routes, and the React frontend depends on their current response structures.

Before implementation, capture an example of the actual current JSON message and document all existing fields.

Preserve all existing fields and meanings, including, where present:

- `uptime_ms`
- `dht_status`
- `mpu_status`
- `temperature`
- `humidity`
- `accel.x`, `accel.y`, `accel.z`
- `gyro.x`, `gyro.y`, `gyro.z`
- `gas_adc`
- `gas_voltage_est`
- `emergency_button`

Do not rename, remove, or change the units of existing fields.

Add derived values and diagnostic metadata as optional, additive fields. For example, a new `motion` object may contain magnitude and rolling-window features.

Use the existing JSON framing and one-complete-message-per-line behavior if that is what the current serial reader expects.

Requirements:

- Each JSON record must be valid and parseable.
- Do not emit debug text inside the JSON telemetry stream.
- Handle missing or optional fields gracefully in Python.
- Avoid changing Flask response contracts during Phase 1 unless absolutely necessary.
- If `serial_reader.py` needs changes, make them tolerant of both the old and new telemetry format.
- Do not silently discard valid telemetry when a new optional field is missing.
- Preserve the current database write path and its error reporting.

The exact additive schema must be based on the actual current code, not an invented replacement schema.

---

### 8. Requirement F — Efficient acquisition and transmission

The high-frequency IMU sampling loop must not be blocked by:

- Serial JSON transmission
- DHT22 reads
- Slow gas-sensor processing
- Network requests
- Database operations
- Dashboard updates

Keep sensor acquisition, feature calculation, and telemetry transmission logically separate.

If acquisition and serial transmission cannot be fully separated in the current firmware architecture, measure the effect on sample timing and document the limitation.

Avoid silently increasing serial traffic to 50 complete records per second. Use a configurable reporting interval while maintaining internal 50 Hz IMU sampling.

Do not introduce Wi-Fi or cloud communication into the ESP32 sampling loop in this phase.

---

### 9. Files and implementation boundaries

Inspect these files before editing:

1. Current ESP32 firmware `.ino`
2. `serial_reader.py`
3. `app.py` (inspect for compatibility; avoid changes unless necessary)
4. React API client and TypeScript telemetry types
5. Relevant SQLite table definitions and write code

Likely changes:

- ESP32 firmware: controlled sampling, derived values, rolling window, sensor status and timestamps.
- `serial_reader.py`: backward-compatible parsing of optional fields and preservation of existing telemetry writes, if needed.
- React frontend: only if additional diagnostic values need to be displayed. Existing pages must continue working.
- `app.py` and database schema: no changes unless a verified requirement makes them necessary.

Before editing, show the current schema and proposed schema additions, and identify which files will change.

---

### 10. Implementation and testing sequence

Implement in small, reversible stages.

#### Stage 1 — Establish baseline

- Save the existing working firmware and Python files.
- Capture several current serial JSON lines.
- Confirm Flask telemetry, history, charts, and SOS state work.
- Record the existing database row count and test that new rows continue to be stored.

#### Stage 2 — Add controlled IMU sampling

- Introduce a 20 ms sampling schedule.
- Keep SOS polling responsive.
- Add sample timestamps and timing diagnostics.
- Test for at least five minutes.
- Measure the observed sample interval distribution and missed deadlines.

#### Stage 3 — Add derived parameters

- Calculate acceleration magnitude in m/s² and g.
- Calculate gyroscope magnitude in rad/s.
- Check results on a stationary surface and during gentle movements.
- Confirm that no existing axis fields have changed units or meaning.

#### Stage 4 — Add rolling windows

- Add a bounded one-second window.
- Calculate mean and peak values.
- Verify sample counts and window duration.
- Confirm incomplete windows are clearly marked.

#### Stage 5 — Add validation and diagnostics

- Test normal readings, failed sensor reads, missing fields, stale timestamps, and invalid numeric values.
- Verify that stale data is not reported as a fresh sample.
- Verify that invalid samples do not silently become zeros.
- Confirm status transitions and diagnostic counters behave consistently.

#### Stage 6 — Verify Python and frontend compatibility

- Run the existing Flask backend with the updated firmware.
- Confirm `serial_reader.py` continues parsing telemetry.
- Confirm `/api/telemetry`, `/api/history`, `/api/ports`, and `/api/config` continue to behave as before.
- Confirm the React dashboard, charts, history, and settings still work.
- Verify that database writes continue and that no unexpected row loss or parsing errors occur.
- Compare before-and-after behavior using captured telemetry and database counts.

#### Stage 7 — Document the result

Provide:

- Files changed
- New telemetry fields and units
- Sampling and reporting intervals
- Validation rules and status meanings
- Test results and known limitations
- Rollback instructions

Do not claim the phase is complete until the tests have actually been performed.

---

### 11. Acceptance criteria

Phase 1 is complete only when all of the following are demonstrated:

1. IMU acquisition targets 50 Hz with measured interval and missed-deadline diagnostics.
2. SOS polling remains responsive while IMU sampling is active.
3. Acceleration magnitude and gyroscope magnitude agree with calculations from the axis values.
4. Units are explicit and consistent.
5. Rolling windows are bounded, timestamp-based, and identify incomplete data.
6. Missing, stale, and invalid readings are distinguishable.
7. DHT22 and MQ-135 acquisition continue at their intended slower rates.
8. The old serial fields remain compatible with the Python reader.
9. Flask endpoints and React pages still function without unexpected contract changes.
10. SQLite continues storing readings without a new parsing or persistence regression.
11. The implementation has been tested against the saved baseline and can be rolled back.

### 12. Final instruction to the coding agent

Start by inspecting the existing code and reporting the current telemetry schema, sampling behavior, and required file changes.

Then implement the smallest backward-compatible changes needed for Phase 1. Provide complete updated files or focused patches, explain where each change belongs, and give exact Windows/Arduino IDE commands for building and testing.

Do not invent test results, assume that hardware reads are valid, or proceed to Phase 2 until the Phase 1 acceptance criteria have been checked.
