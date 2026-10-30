# SENTINEL-X React frontend

React + TypeScript + Vite frontend designed to preserve the existing Flask API.

## Requirements
- Node.js LTS
- Existing Flask backend running at `http://127.0.0.1:5000`
- ESP32 firmware sending newline-delimited JSON at 115200 baud

## Run
Open a terminal in this `frontend` folder:

```bash
npm install
npm run dev
```

Open the Vite URL printed in the terminal (normally `http://127.0.0.1:5173`).

The Vite development server proxies `/api/*` to the Flask backend at `http://127.0.0.1:5000`. Keep Flask running in a separate terminal. The proxy is for local development only.

## Existing Flask APIs used
- `GET /api/telemetry`
- `GET /api/history?limit=120`
- `GET /api/ports`
- `POST /api/config`
- `GET/POST /api/gas/config`
- `POST/DELETE /api/gas/baseline`

## Important implementation notes
- The alert list in this starter is browser-session-only. A persistent alert-events API and database table must be added to Flask/SQLite before alerts are durable.
- Derived acceleration magnitude and angular-velocity magnitude are calculated for display. A validated fall-detection state machine and thresholds are not implemented here.
- The MQ-135 dashboard shows relative response indicators using a manually captured, persistent ADC baseline. It does not display calibrated ppm or declare that the air is safe.
- Theme selection is saved in browser local storage.
- Supabase is not connected in this frontend scaffold. Integrate it through the Python backend, with server-side credentials.
- The ESP32's local SOS and future buzzer control must not depend on the browser, Flask, or cloud.
- If Flask is unavailable, the dashboard displays an API error banner rather than inventing readings.

## Build
```bash
npm run build
```

## Troubleshooting
- If telemetry says backend unavailable, start Flask on port 5000.
- Close Arduino IDE Serial Monitor before starting the Flask serial reader, because COM5 is normally exclusive.
- If Flask is running on another port, update `vite.config.ts` proxy target.
