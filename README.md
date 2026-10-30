# SentinelX

SentinelX is an experimental sensor-monitoring project with a Flask backend, a React dashboard, and ESP32 firmware. It reads serial telemetry and exposes it to the dashboard over a local API.

> This is a prototype, not a certified safety or life-safety system. Sensor values are informational and must not be used as a substitute for certified equipment.

## Project layout

- `app.py`, `serial_reader.py`, `motion_analyzer.py` — Flask API, serial telemetry, and motion analysis.
- `frontend/` — React, TypeScript, and Vite dashboard.
- `firmware/` — ESP32 sketches.
- `static/`, `templates/` — Flask-served frontend assets.
- `scratch/` — focused backend tests.
- `edge_processing.md`, `local_alert.md`, `Phase 1.md` — project notes.

## Run the backend

Requires Python 3.10+ and a serial-connected device.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

The serial reader defaults to `COM5` at `115200` baud. Set `SENTINEL_PORT` and `SENTINEL_BAUD` before starting Flask to use another port or baud rate. Close other serial-monitor applications while the backend is connected.

## Run the dashboard

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

The Vite development server proxies API requests to the Flask backend at `http://127.0.0.1:5000`. See [frontend/README.md](frontend/README.md) for dashboard APIs, sensor-response details, and build instructions.

## Run backend checks

```powershell
python -m unittest discover -s scratch -p "test_*.py"
```

The SQLite database and frontend build output are generated locally and are intentionally excluded from version control.
