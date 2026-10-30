import { useEffect, useState } from "react";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, BellRing,
  CheckCircle2, Clock3, Cloud, Droplets, Gauge, RefreshCw, Thermometer,
  Wifi, WifiOff, Wind, Zap
} from "lucide-react";
import Sidebar, { type Page } from "./components/Sidebar";
import MetricCard from "./components/MetricCard";
import TelemetryChart from "./components/TelemetryChart";
import { useTelemetry } from "./hooks/useTelemetry";
import { api } from "./api/client";
import type { AlertEvent, PortInfo } from "./types";

function fmt(value: number | null | undefined, digits = 1) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function deriveMotion(accel: { x: number | null; y: number | null; z: number | null } | null) {
  if (!accel || accel.x == null || accel.y == null || accel.z == null) return null;
  return Math.sqrt(accel.x ** 2 + accel.y ** 2 + accel.z ** 2);
}

export default function App() {
  const [page, setPage] = useState<Page>("Overview");
  const [theme, setTheme] = useState<"dark" | "light">(() => (localStorage.getItem("sentinel-theme") as "dark" | "light") || "dark");
  const [collapsed, setCollapsed] = useState(false);
  const [port, setPort] = useState("COM5");
  const [baudrate, setBaudrate] = useState(115200);
  const [ports, setPorts] = useState<PortInfo[]>([]);
  const [configMessage, setConfigMessage] = useState("");
  const [configBusy, setConfigBusy] = useState(false);
  const { telemetry, history, error, lastPoll, refresh } = useTelemetry();

  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [lastButtonState, setLastButtonState] = useState("UNKNOWN");

  // UI-only event history is a scaffold until persistent alert-event API is added.
  useEffect(() => {
    if (telemetry.emergency_button === "PRESSED" && lastButtonState !== "PRESSED") {
      setEvents((old) => [{
        id: `${Date.now()}`, time: new Date().toLocaleString(), type: "SOS_BUTTON",
        severity: "Emergency", message: "Emergency button pressed", acknowledged: false
      }, ...old]);
    }
    setLastButtonState(telemetry.emergency_button);
  }, [telemetry.emergency_button, lastButtonState]);

  const motionMagnitude = deriveMotion(telemetry.accel);
  const online = telemetry.connected && !error;
  const emergencyPressed = telemetry.emergency_button === "PRESSED";

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("sentinel-theme", next);
  };

  async function loadPorts() {
    try {
      const result = await api.ports();
      setPorts(result.ports ?? []);
      setConfigMessage(`Found ${result.ports?.length ?? 0} serial port(s).`);
    } catch (e) {
      setConfigMessage(e instanceof Error ? e.message : "Could not load serial ports.");
    }
  }

  async function savePort() {
    setConfigBusy(true);
    setConfigMessage("");
    try {
      const result = await api.configurePort(port, baudrate);
      setConfigMessage(result.message ?? "Serial configuration updated.");
      window.setTimeout(() => void refresh(), 500);
    } catch (e) {
      setConfigMessage(e instanceof Error ? e.message : "Port update failed.");
    } finally {
      setConfigBusy(false);
    }
  }

  const pageTitle = {
    Overview: "Worker safety overview",
    Analytics: "Sensor analytics",
    Alerts: "Alert management",
    History: "Telemetry history",
    Settings: "Device settings"
  }[page];

  const recentRows = [...history].slice(-8).reverse();
  const alertCount = events.filter((e) => !e.acknowledged).length;
  const uptimeText = telemetry.uptime_ms == null ? "—" : `${Math.floor(telemetry.uptime_ms / 1000)}s`;

  return (
    <div className="app-shell" data-theme={theme}>
      <Sidebar page={page} setPage={setPage} theme={theme} toggleTheme={toggleTheme} collapsed={collapsed} toggleCollapsed={() => setCollapsed((v) => !v)} />
      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><span>SENTINEL-X</span><span className="crumb-sep">/</span><strong>{page}</strong></div>
          <div className="topbar-actions">
            <div className={`connection-pill ${online ? "connected" : "disconnected"}`}>
              {online ? <Wifi size={15}/> : <WifiOff size={15}/>}
              {online ? "DEVICE CONNECTED" : "DEVICE DISCONNECTED"}
            </div>
            <button className="icon-button" onClick={() => void refresh()} title="Refresh data"><RefreshCw size={17}/></button>
            <div className="avatar">SX</div>
          </div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">LIVE MONITORING <span className="live-dot" /></div>
              <h1>{pageTitle}</h1>
              <p>Monitor sensor telemetry, device health, and emergency activity.</p>
            </div>
            <div className="heading-meta">
              <div><Clock3 size={15}/><span>Last poll: {lastPoll ? lastPoll.toLocaleTimeString() : "waiting"}</span></div>
              <div><Activity size={15}/><span>Uptime: {uptimeText}</span></div>
            </div>
          </div>

          {error && <div className="notice warning"><AlertTriangle size={18}/><div><strong>Backend unavailable</strong><span>{error}. Start Flask on port 5000 and check the serial connection.</span></div></div>}
          {emergencyPressed && <div className="notice emergency"><BellRing size={20}/><div><strong>EMERGENCY — SOS ACTIVATED</strong><span>Emergency button is currently pressed. Local buzzer hardware is not integrated yet.</span></div></div>}

          {page === "Overview" && <>
            <div className="metric-grid">
              <MetricCard label="TEMPERATURE" value={fmt(telemetry.temperature)} unit="°C" note={telemetry.dht_status === "ok" ? "DHT22 · Sensor responding" : `DHT22 · ${telemetry.dht_status}`} icon={<Thermometer size={20}/>} tone="amber"/>
              <MetricCard label="HUMIDITY" value={fmt(telemetry.humidity)} unit="%" note={telemetry.dht_status === "ok" ? "Relative humidity" : "Reading unavailable"} icon={<Droplets size={20}/>} tone="green"/>
              <MetricCard label="ACCELERATION MAGNITUDE" value={fmt(motionMagnitude, 2)} unit="m/s²" note="Derived from X, Y and Z" icon={<Activity size={20}/>} tone="blue"/>
              <MetricCard label="GAS ADC" value={telemetry.gas_adc == null ? "—" : String(telemetry.gas_adc)} note="Raw response · Not ppm" icon={<Wind size={20}/>} tone="violet"/>
            </div>
            <div className="content-grid">
              <section className="panel chart-panel">
                <div className="panel-heading"><div><h2>Environmental trends</h2><p>Recent stored readings</p></div><span className="subtle-tag">LOCAL HISTORY</span></div>
                <TelemetryChart history={history} theme={theme}/>
                <div className="chart-legend"><span><i className="legend-blue"/>Temperature</span><span><i className="legend-green"/>Humidity</span></div>
              </section>
              <section className="panel status-panel">
                <div className="panel-heading"><div><h2>Device health</h2><p>Current sensor status</p></div><Activity size={18}/></div>
                <StatusRow label="Serial connection" status={telemetry.status} good={online}/>
                <StatusRow label="DHT22" status={telemetry.dht_status} good={telemetry.dht_status === "ok"}/>
                <StatusRow label="MPU compatible module" status={telemetry.mpu_status} good={telemetry.mpu_status === "ok"}/>
                <StatusRow label="Emergency button" status={telemetry.emergency_button} good={!emergencyPressed}/>
                <StatusRow label="MQ-135" status={telemetry.gas_adc == null ? "Unknown" : "Reading"} good={telemetry.gas_adc != null}/>
                <div className="status-footer"><span>Port {telemetry.port}</span><span>{telemetry.baudrate} baud</span></div>
              </section>
            </div>
            <section className="panel table-panel">
              <div className="panel-heading"><div><h2>Latest readings</h2><p>Most recent locally stored telemetry</p></div><button className="text-button" onClick={() => setPage("History")}>View history →</button></div>
              <TelemetryTable rows={recentRows}/>
            </section>
          </>}

          {page === "Analytics" && <>
            <div className="metric-grid compact">
              <MetricCard label="ACCELERATION MAGNITUDE" value={fmt(motionMagnitude, 2)} unit="m/s²" note="Includes gravity" icon={<Activity size={20}/>} />
              <MetricCard label="ANGULAR VELOCITY" value={fmt(telemetry.gyro ? Math.sqrt((telemetry.gyro.x ?? 0)**2 + (telemetry.gyro.y ?? 0)**2 + (telemetry.gyro.z ?? 0)**2) : null, 3)} unit="rad/s" note="Combined gyroscope axes" icon={<Gauge size={20}/>} tone="violet"/>
              <MetricCard label="GAS ADC" value={telemetry.gas_adc == null ? "—" : String(telemetry.gas_adc)} note={`Estimated ADC voltage: ${fmt(telemetry.gas_voltage_est, 3)} V`} icon={<Wind size={20}/>} tone="amber"/>
              <MetricCard label="IMU STATUS" value={telemetry.mpu_status.toUpperCase()} note="Raw values remain available" icon={<Zap size={20}/>} tone="green"/>
            </div>
            <section className="panel chart-panel"><div className="panel-heading"><div><h2>Sensor trends</h2><p>Temperature and humidity over recent samples</p></div></div><TelemetryChart history={history} theme={theme}/></section>
            <section className="panel"><div className="panel-heading"><div><h2>Latest IMU axes</h2><p>Acceleration in m/s² · Gyroscope in rad/s</p></div></div><div className="axis-grid">{(["x","y","z"] as const).map((axis) => <div className="axis-card" key={axis}><span>{axis.toUpperCase()} AXIS</span><strong>{fmt(telemetry.accel?.[axis], 3)}</strong><small>Accel m/s²</small><strong>{fmt(telemetry.gyro?.[axis], 4)}</strong><small>Gyro rad/s</small></div>)}</div></section>
          </>}

          {page === "Alerts" && <section className="panel">
            <div className="panel-heading"><div><h2>Alert events</h2><p>Session event list · persistent event API is a later step</p></div><span className="subtle-tag">{alertCount} UNACKNOWLEDGED</span></div>
            {events.length === 0 ? <div className="empty-state tall"><CheckCircle2 size={28}/><strong>No SOS events detected in this browser session</strong><span>Press the physical emergency button to test the software alert path.</span></div> :
              <div className="event-list">{events.map((event) => <div className="event-row" key={event.id}><span className="event-icon"><AlertTriangle size={18}/></span><div className="event-copy"><strong>{event.message}</strong><span>{event.type} · {event.time}</span></div><span className="severity emergency-severity">{event.severity}</span><button className="small-button" onClick={() => setEvents((old) => old.map((e) => e.id === event.id ? {...e, acknowledged: true} : e))}>{event.acknowledged ? "Acknowledged" : "Acknowledge"}</button></div>)}</div>}
            <p className="footnote">Acknowledgement here is a UI demonstration only; it does not clear an actual hazard or trigger a hardware response.</p>
          </section>}

          {page === "History" && <section className="panel table-panel"><div className="panel-heading"><div><h2>Telemetry history</h2><p>{history.length} recent records returned by SQLite</p></div><button className="small-button" onClick={() => void refresh()}><RefreshCw size={14}/> Refresh</button></div><TelemetryTable rows={[...history].reverse()}/></section>}

          {page === "Settings" && <div className="settings-grid">
            <section className="panel"><div className="panel-heading"><div><h2>Serial connection</h2><p>Configure the existing Flask serial reader</p></div><Wifi size={18}/></div>
              <label className="field-label">Serial port</label>
              <div className="field-row"><select value={port} onChange={(e) => setPort(e.target.value)}><option value="COM5">COM5</option>{ports.filter((p) => p.device !== "COM5").map((p) => <option key={p.device} value={p.device}>{p.device} — {p.description}</option>)}</select><button className="small-button" onClick={() => void loadPorts()}>Refresh ports</button></div>
              <label className="field-label">Baud rate</label><select value={baudrate} onChange={(e) => setBaudrate(Number(e.target.value))}><option value={115200}>115200</option><option value={9600}>9600</option></select>
              <button className="primary-button" disabled={configBusy} onClick={() => void savePort()}>{configBusy ? "Applying…" : "Apply connection settings"}</button>
              {configMessage && <p className="settings-message">{configMessage}</p>}
            </section>
            <section className="panel"><div className="panel-heading"><div><h2>Sampling & thresholds</h2><p>Display-only starting targets from the design specification</p></div><Gauge size={18}/></div>
              <div className="setting-row"><div><strong>IMU target rate</strong><span>Acquisition target</span></div><b>50 Hz / 20 ms</b></div>
              <div className="setting-row"><div><strong>MQ-135 target rate</strong><span>Relative response</span></div><b>1 Hz / 1000 ms</b></div>
              <div className="setting-row"><div><strong>DHT22 target rate</strong><span>Minimum 2 seconds between reads</span></div><b>0.5 Hz</b></div>
              <div className="notice warning compact-notice"><AlertTriangle size={16}/><span>Fall and gas thresholds are not configured or validated in this frontend. These values do not activate hardware alerts.</span></div>
            </section>
          </div>}

          <footer className="app-footer"><span><span className="live-dot"/> Local prototype</span><span><Cloud size={14}/> Supabase integration planned</span><span>Safety prototype · Not certified for life-safety use</span></footer>
        </div>
      </main>
    </div>
  );
}

function StatusRow({ label, status, good }: { label: string; status: string; good: boolean }) {
  return <div className="status-row"><span>{label}</span><span className={`status-value ${good ? "good" : "bad"}`}><i/>{status}</span></div>;
}

function TelemetryTable({ rows }: { rows: Array<{ timestamp: number; temperature?: number | null; humidity?: number | null; accel_x?: number | null; accel_y?: number | null; accel_z?: number | null; gas_adc?: number | null; emergency_button?: string | null; dht_status?: string | null; mpu_status?: string | null }> }) {
  if (rows.length === 0) return <div className="empty-state">No stored readings yet.</div>;
  return <div className="table-scroll"><table><thead><tr><th>Timestamp</th><th>Temp °C</th><th>Humidity %</th><th>Accel X</th><th>Accel Y</th><th>Accel Z</th><th>Gas ADC</th><th>SOS</th></tr></thead><tbody>
    {rows.map((r, i) => <tr key={`${r.timestamp}-${i}`}><td>{new Date(r.timestamp * 1000).toLocaleString()}</td><td>{fmt(r.temperature)}</td><td>{fmt(r.humidity)}</td><td>{fmt(r.accel_x, 3)}</td><td>{fmt(r.accel_y, 3)}</td><td>{fmt(r.accel_z, 3)}</td><td>{r.gas_adc ?? "—"}</td><td><span className={`table-state ${r.emergency_button === "PRESSED" ? "danger-state" : ""}`}>{r.emergency_button ?? "—"}</span></td></tr>)}
  </tbody></table></div>;
}
