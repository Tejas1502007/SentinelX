import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, BellRing,
  CheckCircle2, Clock3, Cloud, Droplets, Gauge, RefreshCw, Thermometer,
  Wifi, WifiOff, Wind, Zap, Volume2, VolumeX, Compass, ShieldCheck,
  Scale, Timer, Sparkles, TrendingUp
} from "lucide-react";
import Sidebar, { type Page } from "./components/Sidebar";
import MetricCard from "./components/MetricCard";
import TelemetryChart from "./components/TelemetryChart";
import MotionChart from "./components/MotionChart";
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
  const [sourceMode, setSourceMode] = useState<string>("auto");
  const [ports, setPorts] = useState<PortInfo[]>([]);
  const [configMessage, setConfigMessage] = useState("");
  const [configBusy, setConfigBusy] = useState(false);
  const [gasConfig, setGasConfig] = useState({
    warn_threshold_adc: 400,
    clear_warn_adc: 350,
    crit_threshold_adc: 1000,
    clear_crit_adc: 850,
    trend_window_seconds: 10,
    trend_deadband_adc: 5,
    baseline_max_spread_adc: 15,
  });
  const [gasConfigBusy, setGasConfigBusy] = useState(false);
  const [gasConfigMessage, setGasConfigMessage] = useState("");
  const [gasOperatorIdentity, setGasOperatorIdentity] = useState("");
  const [baselineBusy, setBaselineBusy] = useState(false);
  const [baselineMessage, setBaselineMessage] = useState("");
  const { telemetry, history, error, lastPoll, refresh } = useTelemetry();

  useEffect(() => {
    if (telemetry.source_mode) {
      setSourceMode(telemetry.source_mode);
    }
  }, [telemetry.source_mode]);

  useEffect(() => {
    let active = true;
    void api.gasConfig().then((config) => {
      if (active) {
        setGasConfig({
          warn_threshold_adc: Number(config.warn_threshold_adc ?? 400),
          clear_warn_adc: Number(config.clear_warn_adc ?? 350),
          crit_threshold_adc: Number(config.crit_threshold_adc ?? 1000),
          clear_crit_adc: Number(config.clear_crit_adc ?? 850),
          trend_window_seconds: Number(config.trend_window_seconds ?? 10),
          trend_deadband_adc: Number(config.trend_deadband_adc ?? 5),
          baseline_max_spread_adc: Number(config.baseline_max_spread_adc ?? 15),
        });
      }
    }).catch(() => {
      if (active) setGasConfigMessage("Could not load gas response settings.");
    });
    return () => { active = false; };
  }, []);

  const [events, setEvents] = useState<AlertEvent[]>([]);

  const loadAlerts = useCallback(async () => {
    try {
      const res = await api.alerts(50);
      setEvents(res.alerts ?? []);
    } catch {
      // Ignore background network blips
    }
  }, []);

  useEffect(() => {
    void loadAlerts();
    const timer = window.setInterval(() => void loadAlerts(), 2500);
    return () => window.clearInterval(timer);
  }, [loadAlerts]);

  async function handleAcknowledge(id: number | string) {
    try {
      await api.acknowledgeAlert(id);
      setEvents((old) => old.map((e) => (String(e.id) === String(id) ? { ...e, acknowledged: true } : e)));
    } catch (err) {
      console.error("Failed to acknowledge alert:", err);
    }
  }

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
      const result = await api.configurePort(port, baudrate, sourceMode);
      setConfigMessage(result.message ?? "Connection configuration updated.");
      window.setTimeout(() => void refresh(), 500);
    } catch (e) {
      setConfigMessage(e instanceof Error ? e.message : "Configuration update failed.");
    } finally {
      setConfigBusy(false);
    }
  }

  async function saveGasResponseSettings() {
    setGasConfigBusy(true);
    setGasConfigMessage("");
    try {
      const result = await api.updateGasConfig(gasConfig);
      if (result.config) {
        setGasConfig({
          warn_threshold_adc: Number(result.config.warn_threshold_adc),
          clear_warn_adc: Number(result.config.clear_warn_adc),
          crit_threshold_adc: Number(result.config.crit_threshold_adc),
          clear_crit_adc: Number(result.config.clear_crit_adc),
          trend_window_seconds: Number(result.config.trend_window_seconds),
          trend_deadband_adc: Number(result.config.trend_deadband_adc),
          baseline_max_spread_adc: Number(result.config.baseline_max_spread_adc),
        });
      }
      setGasConfigMessage(result.message ?? "Gas response settings saved.");
      await refresh();
    } catch (e) {
      setGasConfigMessage(e instanceof Error ? e.message : "Could not save gas response settings.");
    } finally {
      setGasConfigBusy(false);
    }
  }

  async function captureGasBaseline() {
    setBaselineBusy(true);
    setBaselineMessage("");
    try {
      const result = await api.captureGasBaseline(gasOperatorIdentity.trim());
      setBaselineMessage(`Baseline captured at ${result.baseline.baseline_adc} ADC.`);
      await refresh();
    } catch (e) {
      setBaselineMessage(e instanceof Error ? e.message : "Baseline capture failed.");
    } finally {
      setBaselineBusy(false);
    }
  }

  async function resetGasBaseline() {
    setBaselineBusy(true);
    setBaselineMessage("");
    try {
      await api.resetGasBaseline(gasOperatorIdentity.trim());
      setBaselineMessage("Baseline reset. No baseline is configured.");
      await refresh();
    } catch (e) {
      setBaselineMessage(e instanceof Error ? e.message : "Baseline reset failed.");
    } finally {
      setBaselineBusy(false);
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
              {online ? (telemetry.telemetry_source === "wifi" ? "WI-FI CONNECTED" : "DEVICE CONNECTED") : "DEVICE DISCONNECTED"}
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
          {emergencyPressed && <div className="notice emergency"><BellRing size={20}/><div><strong>EMERGENCY — SOS ACTIVATED</strong><span>Emergency button is currently pressed. Local buzzer hardware is sounding on the node.</span></div></div>}

          {/* Critical Gas Alert Banner (Urgent priority over temperature) */}
          {telemetry.gas_alert?.active && telemetry.gas_alert?.level === "CRITICAL" && (
            <div className="notice emergency" style={{ borderColor: "#ff4d5a", marginBottom: "1rem" }}>
              <Wind size={20} />
              <div style={{ flex: 1 }}>
                <strong>CRITICAL RELATIVE GAS SENSOR RESPONSE — ADC {telemetry.gas_adc ?? "—"} ({fmt(telemetry.gas_response?.sensor_voltage_v ?? telemetry.gas_voltage_est, 2)} V)</strong>
                <span>
                  Experimental critical response threshold exceeded (≥{telemetry.gas_alert.crit_threshold_adc ?? 1000} ADC · ~{fmt(telemetry.gas_alert.crit_threshold_voltage, 2)} V) · Clear condition: &lt;{telemetry.gas_alert.clear_crit_adc ?? 850} ADC (hysteresis active)
                  {lastPoll ? ` · Evaluated: ${lastPoll.toLocaleTimeString()}` : ""}
                </span>
              </div>
              <span className="severity emergency-severity">CRITICAL</span>
            </div>
          )}

          {/* Warning Gas Alert Banner */}
          {telemetry.gas_alert?.active && telemetry.gas_alert?.level === "WARNING" && (
            <div className="notice warning" style={{ borderColor: "rgba(234,176,91,.4)", marginBottom: "1rem" }}>
              <Wind size={20} />
              <div style={{ flex: 1 }}>
                <strong>RELATIVE GAS SENSOR RESPONSE WARNING — ADC {telemetry.gas_adc ?? "—"} ({fmt(telemetry.gas_response?.sensor_voltage_v ?? telemetry.gas_voltage_est, 2)} V)</strong>
                <span>
                  Experimental response warning threshold exceeded (≥{telemetry.gas_alert.warn_threshold_adc ?? 400} ADC · ~{fmt(telemetry.gas_alert.warn_threshold_voltage, 2)} V) · Clear condition: &lt;{telemetry.gas_alert.clear_warn_adc ?? 350} ADC (hysteresis active)
                  {lastPoll ? ` · Evaluated: ${lastPoll.toLocaleTimeString()}` : ""}
                </span>
              </div>
              <span className="severity warning-severity">WARNING</span>
            </div>
          )}

          {/* Gas Sensor Fault / Stale Notice */}
          {telemetry.gas_alert?.status === "SENSOR_FAULT" && (
            <div className="notice warning" style={{ marginBottom: "1rem" }}>
              <AlertTriangle size={18} />
              <div>
                <strong>MQ-135 SENSOR FAULT / DISCONNECTED</strong>
                <span>Sensor response is unavailable. This prototype does not determine a safe atmosphere.</span>
              </div>
            </div>
          )}

          {/* Temperature Alert Banner (Threshold > 31°C / Clear <= 30°C) */}
          {telemetry.temperature_alert?.active && (
            <div className="notice emergency" style={{ borderColor: "var(--accent-red)", marginBottom: "1rem" }}>
              <AlertTriangle size={20} />
              <div style={{ flex: 1 }}>
                <strong>CRITICAL TEMPERATURE ALERT — {fmt(telemetry.temperature, 1)}°C</strong>
                <span>
                  Threshold exceeded (&gt;{telemetry.temperature_alert.high_threshold ?? 31.0}°C) · Clear condition: ≤{telemetry.temperature_alert.low_threshold ?? 30.0}°C (Hysteresis active) · Status: {telemetry.temperature_alert.status}
                  {lastPoll ? ` · Evaluated: ${lastPoll.toLocaleTimeString()}` : ""}
                </span>
              </div>
              <span className="severity emergency-severity">TRIGGERED</span>
            </div>
          )}

          {/* Stale / Unavailable Notice during Active Alert */}
          {telemetry.temperature_alert?.status === "ALERT" && telemetry.temperature_alert?.sensor_status !== "ok" && (
            <div className="notice warning" style={{ marginBottom: "1rem" }}>
              <AlertTriangle size={18} />
              <div>
                <strong>TEMPERATURE SENSOR {telemetry.temperature_alert.sensor_status?.toUpperCase() ?? "UNAVAILABLE"}</strong>
                <span>Temperature reading is currently unavailable or stale. Active alert remains latched until a valid temperature ≤30.0°C is received.</span>
              </div>
            </div>
          )}

          {page === "Overview" && <>
            <div className="metric-grid">
              <MetricCard
                label="TEMPERATURE"
                value={fmt(telemetry.temperature)}
                unit="°C"
                note={
                  telemetry.temperature_alert?.active
                    ? `ALERT (>31°C) · Clear: ≤30°C`
                    : telemetry.dht_status === "ok"
                    ? `Normal · Alert threshold: >31°C`
                    : `DHT22 · ${telemetry.dht_status}`
                }
                icon={<Thermometer size={20}/>}
                tone={telemetry.temperature_alert?.active ? "red" : "amber"}
              />
              <MetricCard label="HUMIDITY" value={fmt(telemetry.humidity)} unit="%" note={telemetry.dht_status === "ok" ? "DHT22 · Relative humidity" : "Reading unavailable"} icon={<Droplets size={20}/>} tone="green"/>
              <MetricCard label="MOTION MAGNITUDE" value={fmt(motionMagnitude ?? telemetry.motion?.accel_magnitude_mps2, 2)} unit="m/s²" note={telemetry.mpu_status === "ok" ? `X:${fmt(telemetry.accel?.x, 1)} Y:${fmt(telemetry.accel?.y, 1)} Z:${fmt(telemetry.accel?.z, 1)}` : `MPU6050 · ${telemetry.mpu_status}`} icon={<Activity size={20}/>} tone="blue"/>
              <MetricCard label="EMERGENCY SOS" value={telemetry.emergency_button} note="Pushbutton · GPIO 27" icon={<BellRing size={20}/>} tone={emergencyPressed ? "red" : "green"}/>
              <MetricCard label="LOCAL BUZZER" value={telemetry.buzzer ?? (emergencyPressed ? "ACTIVE" : "STANDBY")} note={`Audible: ${telemetry.buzzer_mode ?? (emergencyPressed ? "SOS" : "STANDBY")}`} icon={emergencyPressed || telemetry.buzzer === "ACTIVE" ? <Volume2 size={20}/> : <VolumeX size={20}/>} tone={emergencyPressed || telemetry.gas_alert?.level === "CRITICAL" ? "red" : telemetry.gas_alert?.level === "WARNING" ? "amber" : "blue"}/>
              <MetricCard
                label="MOVEMENT INTENSITY"
                value={telemetry.motion_analysis?.movement_intensity?.level ?? "UNKNOWN"}
                note={telemetry.motion_analysis?.movement_intensity?.value_mps2 != null ? `${telemetry.motion_analysis.movement_intensity.value_mps2} m/s² std dev · Rule-based` : "Evaluating rolling window..."}
                icon={<TrendingUp size={20}/>}
                tone={telemetry.motion_analysis?.movement_intensity?.level === "HIGH" ? "red" : telemetry.motion_analysis?.movement_intensity?.level === "MODERATE" ? "amber" : "green"}
              />
              <MetricCard
                label="STATIC TILT ESTIMATE"
                value={telemetry.motion_analysis?.orientation_estimate?.tilt_from_vertical_deg != null ? `${telemetry.motion_analysis.orientation_estimate.tilt_from_vertical_deg}°` : "—"}
                note={telemetry.motion_analysis?.orientation_estimate?.dynamic_gated ? "Dynamic Locked (in motion)" : `Pitch:${fmt(telemetry.motion_analysis?.orientation_estimate?.pitch_deg, 0)}° Roll:${fmt(telemetry.motion_analysis?.orientation_estimate?.roll_deg, 0)}°`}
                icon={<Compass size={20}/>}
                tone="violet"
              />
              <MetricCard
                label="MOTION DATA QUALITY"
                value={telemetry.motion_analysis?.data_quality?.score_pct != null ? `${telemetry.motion_analysis.data_quality.score_pct}%` : "—"}
                note={telemetry.motion_analysis?.data_quality?.status ? `Status: ${telemetry.motion_analysis.data_quality.status} · ${telemetry.motion_analysis.data_quality.valid_samples}/50 smp` : "Awaiting IMU stream"}
                icon={<ShieldCheck size={20}/>}
                tone={telemetry.motion_analysis?.data_quality?.status === "GOOD" ? "green" : "amber"}
              />
            </div>

            <section className="panel gas-response-panel">
              <div className="panel-heading">
                <div>
                  <h2>Relative Gas Sensor Response</h2>
                  <p>MQ-135 raw ADC response and relative indicators; not a calibrated gas measurement.</p>
                </div>
                <div className="gas-response-badges">
                  <span className={`gas-status-badge ${telemetry.gas_response?.status === "VALID" ? "valid" : telemetry.gas_response?.status === "WARMING UP" ? "warming" : "fault"}`}>
                    {telemetry.gas_response?.status ?? "SENSOR FAULT"}
                  </span>
                  <span className={`gas-status-badge level-${(telemetry.gas_response?.response_level ?? "NORMAL").toLowerCase()}`}>
                    {telemetry.gas_response?.response_level ?? "NORMAL"}
                  </span>
                </div>
              </div>
              <div className="gas-response-grid">
                <GasResponseValue label="RAW ADC" value={telemetry.gas_adc == null ? "—" : String(telemetry.gas_adc)} detail="ESP32 · 0–4095" />
                <GasResponseValue label="SENSOR VOLTAGE" value={fmt(telemetry.gas_response?.sensor_voltage_v ?? telemetry.gas_voltage_est, 3)} detail="V · ADC × 3.3 / 4095" />
                <GasResponseValue
                  label="RELATIVE CHANGE"
                  value={telemetry.gas_response?.relative_change_percent == null ? (telemetry.gas_response?.baseline_adc == null ? "Baseline not configured" : "N/A") : `${telemetry.gas_response.relative_change_percent > 0 ? "+" : ""}${fmt(telemetry.gas_response.relative_change_percent, 1)}%`}
                  detail={telemetry.gas_response?.baseline_adc === 0 ? "Zero baseline; percentage unavailable" : "Change from stored baseline"}
                />
                <GasResponseValue label="RESPONSE TREND" value={telemetry.gas_response?.trend ?? "STABLE"} detail={`Rolling ${telemetry.gas_response?.trend_window_seconds ?? gasConfig.trend_window_seconds}s · ±${telemetry.gas_response?.trend_deadband_adc ?? gasConfig.trend_deadband_adc} ADC deadband`} />
                <GasResponseValue label="PEAK ADC · 60 SEC" value={telemetry.gas_response?.peak_adc_60s == null ? "—" : String(telemetry.gas_response.peak_adc_60s)} detail="Highest valid recent reading" />
                <GasResponseValue label="RESPONSE LEVEL" value={telemetry.gas_response?.response_level ?? "NORMAL"} detail={`Configured ADC thresholds · Warn ${telemetry.gas_alert?.warn_threshold_adc ?? 400} · Critical ${telemetry.gas_alert?.crit_threshold_adc ?? 1000}`} />
              </div>
              <div className="gas-baseline-controls">
                <div className="gas-baseline-copy">
                  <strong>{telemetry.gas_response?.baseline_adc == null ? "Baseline not configured" : `Baseline: ${telemetry.gas_response.baseline_adc} ADC`}</strong>
                  <span>
                    {telemetry.gas_response?.baseline_adc == null
                      ? telemetry.gas_response?.baseline_capture_reason ?? "Capture is manual and requires stable, valid readings after warm-up."
                      : `Captured ${telemetry.gas_response.baseline_captured_at ? new Date(telemetry.gas_response.baseline_captured_at * 1000).toLocaleString() : ""}${telemetry.gas_response.baseline_operator_identity ? ` by ${telemetry.gas_response.baseline_operator_identity}` : ""}`}
                  </span>
                </div>
                <label className="operator-field">
                  <span>Operator identity (optional)</span>
                  <input value={gasOperatorIdentity} onChange={(event) => setGasOperatorIdentity(event.target.value)} maxLength={120} placeholder="Name or shift ID" />
                </label>
                <div className="baseline-actions">
                  <button className="primary-button" disabled={baselineBusy || !telemetry.gas_response?.baseline_capture_ready} onClick={() => void captureGasBaseline()}>
                    {baselineBusy ? "Working..." : "Capture baseline"}
                  </button>
                  <button className="small-button" disabled={baselineBusy || telemetry.gas_response?.baseline_adc == null} onClick={() => void resetGasBaseline()}>
                    Reset baseline
                  </button>
                </div>
              </div>
              <p className="footnote">Capture only in controlled, repeatable ambient conditions after warm-up and stable readings. A baseline is never inferred automatically. MQ-135 response values are experimental indicators, not CO2 ppm, CO ppm, or gas concentration.</p>
              {baselineMessage && <p className="settings-message" role="status">{baselineMessage}</p>}
            </section>

            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>MPU6050 Motion & Angular Velocity</h2>
                  <p>Real-time tri-axial acceleration (m/s²) and gyroscope (rad/s)</p>
                </div>
                <span className="subtle-tag">{telemetry.mpu_status === "ok" ? "MPU ACTIVE (50 Hz)" : `MPU ${telemetry.mpu_status.toUpperCase()}`}</span>
              </div>
              <div className="axis-grid">
                {(["x", "y", "z"] as const).map((axis) => (
                  <div className="axis-card" key={axis}>
                    <span>{axis.toUpperCase()} AXIS</span>
                    <strong>{fmt(telemetry.accel?.[axis], 3)}</strong>
                    <small>Acceleration (m/s²)</small>
                    <strong>{fmt(telemetry.gyro?.[axis], 4)}</strong>
                    <small>Gyroscope (rad/s)</small>
                  </div>
                ))}
              </div>
            </section>

            <div className="content-grid">
              <section className="panel chart-panel">
                <div className="panel-heading"><div><h2>Environmental trends</h2><p>Recent stored readings</p></div><span className="subtle-tag">LOCAL HISTORY</span></div>
                <TelemetryChart history={history} theme={theme}/>
                <div className="chart-legend"><span><i className="legend-blue"/>Temperature</span><span><i className="legend-green"/>Humidity</span></div>
              </section>
              <section className="panel status-panel">
                <div className="panel-heading"><div><h2>Device health</h2><p>Integrated sensor status</p></div><Activity size={18}/></div>
                <StatusRow label={telemetry.telemetry_source === "wifi" ? "Wi-Fi telemetry" : "Device connection"} status={telemetry.status} good={online}/>
                <StatusRow label="DHT22 (Temp & Humidity)" status={telemetry.dht_status} good={telemetry.dht_status === "ok"}/>
                <StatusRow
                  label="Temperature Threshold (>31°C / ≤30°C)"
                  status={
                    telemetry.temperature_alert?.active
                      ? `ALERT (${fmt(telemetry.temperature, 1)}°C > 31°C)`
                      : telemetry.temperature_alert?.sensor_status !== "ok" && telemetry.temperature_alert?.sensor_status !== "disconnected"
                      ? `SENSOR ${telemetry.temperature_alert?.sensor_status?.toUpperCase() ?? "UNAVAILABLE"}`
                      : `NORMAL (Clear ≤30°C)`
                  }
                  good={!telemetry.temperature_alert?.active && telemetry.temperature_alert?.sensor_status === "ok"}
                />
                <StatusRow label="MPU6050 (Accel & Gyro)" status={telemetry.mpu_status} good={telemetry.mpu_status === "ok"}/>
                <StatusRow
                  label="MQ-135 response sensor"
                  status={`${telemetry.gas_response?.status ?? "SENSOR FAULT"} · ${telemetry.gas_response?.response_level ?? "NORMAL"}`}
                  good={telemetry.gas_response?.status === "VALID" && telemetry.gas_response?.response_level === "NORMAL"}
                />
                <StatusRow label="Emergency SOS Button" status={telemetry.emergency_button} good={!emergencyPressed}/>
                <StatusRow label="Local Buzzer Output" status={telemetry.buzzer ?? (emergencyPressed ? "ACTIVE" : "STANDBY")} good={!emergencyPressed && !telemetry.gas_alert?.active}/>
                <div className="status-footer">
                  {telemetry.telemetry_source === "wifi" ? (
                    <>
                      <span>Source: Wi-Fi Hotspot</span>
                      <span>{telemetry.client_ip ? `IP: ${telemetry.client_ip}` : "Port 5000"}</span>
                    </>
                  ) : (
                    <>
                      <span>Port {telemetry.port}</span>
                      <span>{telemetry.baudrate} baud</span>
                    </>
                  )}
                </div>
              </section>
            </div>
            <section className="panel table-panel">
              <div className="panel-heading"><div><h2>Latest readings</h2><p>Most recent locally stored telemetry</p></div><button className="text-button" onClick={() => setPage("History")}>View history →</button></div>
              <TelemetryTable rows={recentRows}/>
            </section>
          </>}

          {page === "Analytics" && <>
            <div className="experimental-banner">
              <Sparkles size={16} />
              <span>
                <strong>EXPERIMENTAL RULE-BASED MOTION ANALYSIS:</strong> Derived using statistical variance over 50 Hz rolling windows and accelerometer tilt gating. Indicators are strictly experimental observations and not calibrated for certified life-safety alarms.
              </span>
            </div>

            <div className="metric-grid compact">
              <MetricCard
                label="MOVEMENT INTENSITY"
                value={fmt(telemetry.motion_analysis?.movement_intensity?.value_mps2 ?? telemetry.motion?.accel_mag_std_mps2, 3)}
                unit="m/s²"
                note={
                  telemetry.motion_analysis?.movement_intensity?.level
                    ? `Level: ${telemetry.motion_analysis.movement_intensity.level} (${telemetry.motion_analysis.movement_intensity.rationale})`
                    : "Rolling 1s standard deviation"
                }
                icon={<TrendingUp size={20}/>}
                tone={telemetry.motion_analysis?.movement_intensity?.level === "HIGH" ? "red" : telemetry.motion_analysis?.movement_intensity?.level === "MODERATE" ? "amber" : "blue"}
              />
              <MetricCard
                label="IMPACT-LIKE EVENT MONITOR"
                value={telemetry.motion_analysis?.impact_event?.last_impact ? `${telemetry.motion_analysis.impact_event.last_impact.peak_mps2}` : "NONE"}
                unit={telemetry.motion_analysis?.impact_event?.last_impact ? `m/s² (~${telemetry.motion_analysis.impact_event.last_impact.peak_g} g)` : ""}
                note={
                  telemetry.motion_analysis?.impact_event?.cooldown_active
                    ? `Cooldown active: ${telemetry.motion_analysis.impact_event.cooldown_remaining_s}s remaining`
                    : `Threshold: ≥${telemetry.motion_analysis?.impact_event?.threshold_peak_mps2 ?? 24.5} m/s² · Cooldown deduplication`
                }
                icon={<Activity size={20}/>}
                tone={telemetry.motion_analysis?.impact_event?.detected_now ? "red" : "amber"}
              />
              <MetricCard
                label="GATED STATIC TILT"
                value={telemetry.motion_analysis?.orientation_estimate?.tilt_from_vertical_deg != null ? `${telemetry.motion_analysis.orientation_estimate.tilt_from_vertical_deg}°` : "—"}
                note={
                  telemetry.motion_analysis?.orientation_estimate?.dynamic_gated
                    ? "Dynamic Locked (holding last static angle)"
                    : `Pitch: ${fmt(telemetry.motion_analysis?.orientation_estimate?.pitch_deg, 1)}° · Roll: ${fmt(telemetry.motion_analysis?.orientation_estimate?.roll_deg, 1)}°`
                }
                icon={<Compass size={20}/>}
                tone="violet"
              />
              <MetricCard
                label="PROLONGED LOW MOVEMENT"
                value={`${telemetry.motion_analysis?.inactivity_observation?.elapsed_seconds ?? 0}s`}
                unit={`/ ${telemetry.motion_analysis?.inactivity_observation?.threshold_seconds ?? 15}s`}
                note={`Observation: ${telemetry.motion_analysis?.inactivity_observation?.status ?? "MONITORING"} (std ≤ ${telemetry.motion_analysis?.inactivity_observation?.intensity_std_max ?? 0.35} m/s²)`}
                icon={<Timer size={20}/>}
                tone={telemetry.motion_analysis?.inactivity_observation?.is_prolonged ? "amber" : "green"}
              />
              <MetricCard
                label="MOTION DATA QUALITY"
                value={telemetry.motion_analysis?.data_quality?.score_pct != null ? `${telemetry.motion_analysis.data_quality.score_pct}%` : "—"}
                note={`${telemetry.motion_analysis?.data_quality?.valid_samples ?? telemetry.motion?.window_samples ?? 0}/50 samples/s · Status: ${telemetry.motion_analysis?.data_quality?.status ?? telemetry.mpu_status}`}
                icon={<ShieldCheck size={20}/>}
                tone={telemetry.motion_analysis?.data_quality?.status === "GOOD" ? "green" : "amber"}
              />
              <MetricCard
                label="ANGULAR ROTATION"
                value={fmt(telemetry.motion?.gyro_magnitude_rads ?? (telemetry.gyro ? Math.sqrt((telemetry.gyro.x ?? 0)**2 + (telemetry.gyro.y ?? 0)**2 + (telemetry.gyro.z ?? 0)**2) : null), 3)}
                unit="rad/s"
                note="Combined 3-axis gyroscope magnitude"
                icon={<Gauge size={20}/>}
                tone="violet"
              />
            </div>

            <section className="panel chart-panel">
              <div className="panel-heading">
                <div>
                  <h2>Motion & Orientation Dynamics</h2>
                  <p>Acceleration magnitude (m/s²), gated static tilt (°), and gyroscope velocity (rad/s)</p>
                </div>
                <span className="subtle-tag">50 Hz WINDOWED HISTORY</span>
              </div>
              <MotionChart history={history} theme={theme}/>
              <div className="chart-legend">
                <span><i className="legend-purple"/>Acceleration Magnitude (m/s²)</span>
                <span><i className="legend-amber"/>Gated Tilt (°)</span>
                <span><i className="legend-green"/>Gyroscope (rad/s)</span>
              </div>
            </section>

            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>Phase 1 Rolling Window Features (1-Second Duration)</h2>
                  <p>Calculated at 50 Hz on edge · {telemetry.motion?.window_complete ? "Window Complete (≥ 80% valid samples)" : "Window Incomplete / Buffering"}</p>
                </div>
                <span className="subtle-tag">{telemetry.motion?.window_samples ?? 0} SAMPLES</span>
              </div>
              <div className="axis-grid">
                <div className="axis-card"><span>MEAN ACCEL</span><strong>{fmt(telemetry.motion?.accel_mag_mean_mps2, 2)}</strong><small>m/s² magnitude</small></div>
                <div className="axis-card"><span>PEAK ACCEL</span><strong>{fmt(telemetry.motion?.accel_mag_peak_mps2, 2)}</strong><small>m/s² magnitude</small></div>
                <div className="axis-card"><span>ACCEL STD DEV</span><strong>{fmt(telemetry.motion?.accel_mag_std_mps2, 3)}</strong><small>m/s² spread (Movement Intensity)</small></div>
                <div className="axis-card"><span>PEAK GYRO</span><strong>{fmt(telemetry.motion?.gyro_mag_peak_rads, 3)}</strong><small>rad/s magnitude</small></div>
              </div>
            </section>

            <section className="panel chart-panel">
              <div className="panel-heading"><div><h2>Environmental trends</h2><p>Temperature and humidity over recent samples</p></div></div>
              <TelemetryChart history={history} theme={theme}/>
            </section>

            <section className="panel">
              <div className="panel-heading"><div><h2>Latest IMU axes</h2><p>Acceleration in m/s² · Gyroscope in rad/s</p></div></div>
              <div className="axis-grid">
                {(["x","y","z"] as const).map((axis) => (
                  <div className="axis-card" key={axis}>
                    <span>{axis.toUpperCase()} AXIS</span>
                    <strong>{fmt(telemetry.accel?.[axis], 3)}</strong>
                    <small>Accel m/s²</small>
                    <strong>{fmt(telemetry.gyro?.[axis], 4)}</strong>
                    <small>Gyro rad/s</small>
                  </div>
                ))}
              </div>
            </section>
          </>}

          {page === "Alerts" && <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>Alert events</h2>
                <p>Persistent SQLite event log · Temperature thresholds and emergency SOS</p>
              </div>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <span className="subtle-tag">{alertCount} UNACKNOWLEDGED</span>
                <button className="small-button" onClick={() => void loadAlerts()}><RefreshCw size={14}/> Refresh</button>
              </div>
            </div>
            {events.length === 0 ? (
              <div className="empty-state tall">
                <CheckCircle2 size={28}/>
                <strong>No alert events recorded</strong>
                <span>Temperature threshold crossings (&gt;31°C trigger, ≤30°C clear) and SOS button presses are logged here.</span>
              </div>
            ) : (
              <div className="event-list">
                {events.map((event) => (
                  <div className="event-row" key={event.id}>
                    <span className="event-icon">
                      {event.event_type === "CLEARED" ? <CheckCircle2 size={18} style={{ color: "var(--accent-green)" }} /> : <AlertTriangle size={18} />}
                    </span>
                    <div className="event-copy">
                      <strong>{event.message}</strong>
                      <span>
                        {event.alert_type} · {event.event_type ?? "EVENT"} · {event.time ?? new Date(event.timestamp * 1000).toLocaleString()}
                        {event.sensor_value != null ? ` · Reading: ${event.sensor_value}°C` : ""}
                        {event.threshold_value != null ? ` · Threshold: ${event.threshold_value}°C` : ""}
                      </span>
                    </div>
                    <span className={`severity ${event.event_type === "CLEARED" ? "info-severity" : event.severity === "Emergency" ? "emergency-severity" : "warning-severity"}`}>
                      {event.event_type ?? event.severity}
                    </span>
                    <button
                      className="small-button"
                      disabled={event.acknowledged}
                      onClick={() => void handleAcknowledge(event.id)}
                    >
                      {event.acknowledged ? "Acknowledged" : "Acknowledge"}
                    </button>
                  </div>
                ))}
              </div>
            )}
            <p className="footnote">
              Temperature alerts trigger strictly above 31.0°C and clear at or below 30.0°C (hysteresis band 30.0°C–31.0°C). Missing or stale readings never clear an alert.
            </p>
          </section>}

          {page === "History" && <section className="panel table-panel"><div className="panel-heading"><div><h2>Telemetry history</h2><p>{history.length} recent records returned by SQLite</p></div><button className="small-button" onClick={() => void refresh()}><RefreshCw size={14}/> Refresh</button></div><TelemetryTable rows={[...history].reverse()}/></section>}

          {page === "Settings" && <div className="settings-grid">
            <section className="panel"><div className="panel-heading"><div><h2>Connection settings</h2><p>Configure telemetry ingestion source and hardware serial port</p></div><Wifi size={18}/></div>
              <label className="field-label">Telemetry source</label>
              <select value={sourceMode} onChange={(e) => setSourceMode(e.target.value)}>
                <option value="auto">Auto (Wi-Fi priority with fallback)</option>
                <option value="wifi">Wi-Fi only (/api/ingest over phone hotspot/LAN)</option>
                <option value="serial">Serial only (USB COM port)</option>
              </select>
              <label className="field-label" style={{ marginTop: "1rem" }}>Serial port</label>
              <div className="field-row"><select value={port} onChange={(e) => setPort(e.target.value)}><option value="COM5">COM5</option>{ports.filter((p) => p.device !== "COM5").map((p) => <option key={p.device} value={p.device}>{p.device} — {p.description}</option>)}</select><button className="small-button" onClick={() => void loadPorts()}>Refresh ports</button></div>
              <label className="field-label">Baud rate</label><select value={baudrate} onChange={(e) => setBaudrate(Number(e.target.value))}><option value={115200}>115200</option><option value={9600}>9600</option></select>
              <button className="primary-button" disabled={configBusy} onClick={() => void savePort()}>{configBusy ? "Applying…" : "Apply connection settings"}</button>
              {configMessage && <p className="settings-message">{configMessage}</p>}
            </section>
            <section className="panel"><div className="panel-heading"><div><h2>MQ-135 Response Configuration</h2><p>Experimental raw ADC thresholds, trend window, and stability controls</p></div><Wind size={18}/></div>
              <div className="setting-row"><div><strong>Stored response baseline</strong><span>Manually captured; never assumed from startup readings</span></div><b>{telemetry.gas_response?.baseline_adc == null ? "Not configured" : `${telemetry.gas_response.baseline_adc} ADC`}</b></div>
              <div className="setting-row"><div><strong>Local alert priority</strong><span>SOS &gt; Critical response &gt; Warning response &gt; Temperature</span></div><b>Response thresholds</b></div>
              <div className="gas-config-grid">
                <label>Warning trigger ADC<input type="number" min={100} max={3500} value={gasConfig.warn_threshold_adc} onChange={(event) => setGasConfig((value) => ({ ...value, warn_threshold_adc: Number(event.target.value) }))}/></label>
                <label>Warning clear ADC<input type="number" min={50} max={3499} value={gasConfig.clear_warn_adc} onChange={(event) => setGasConfig((value) => ({ ...value, clear_warn_adc: Number(event.target.value) }))}/></label>
                <label>Critical trigger ADC<input type="number" min={101} max={4095} value={gasConfig.crit_threshold_adc} onChange={(event) => setGasConfig((value) => ({ ...value, crit_threshold_adc: Number(event.target.value) }))}/></label>
                <label>Critical clear ADC<input type="number" min={100} max={4094} value={gasConfig.clear_crit_adc} onChange={(event) => setGasConfig((value) => ({ ...value, clear_crit_adc: Number(event.target.value) }))}/></label>
                <label>Trend window (seconds)<input type="number" min={5} max={120} value={gasConfig.trend_window_seconds} onChange={(event) => setGasConfig((value) => ({ ...value, trend_window_seconds: Number(event.target.value) }))}/></label>
                <label>Trend deadband (ADC)<input type="number" min={0} max={250} value={gasConfig.trend_deadband_adc} onChange={(event) => setGasConfig((value) => ({ ...value, trend_deadband_adc: Number(event.target.value) }))}/></label>
                <label>Baseline stability spread (ADC)<input type="number" min={1} max={250} value={gasConfig.baseline_max_spread_adc} onChange={(event) => setGasConfig((value) => ({ ...value, baseline_max_spread_adc: Number(event.target.value) }))}/></label>
              </div>
              <button className="primary-button" disabled={gasConfigBusy} onClick={() => void saveGasResponseSettings()}>{gasConfigBusy ? "Saving..." : "Save response settings"}</button>
              {gasConfigMessage && <p className="settings-message" role="status">{gasConfigMessage}</p>}
              <div className="notice warning compact-notice"><AlertTriangle size={16}/><span>MQ-135 response thresholds are experimental and not calibrated for gas concentration, ppm, or life-safety decisions.</span></div>
            </section>
            <section className="panel"><div className="panel-heading"><div><h2>Sampling & thresholds</h2><p>Display-only starting targets from the design specification</p></div><Gauge size={18}/></div>
              <div className="setting-row"><div><strong>IMU target rate</strong><span>Acquisition target</span></div><b>50 Hz / 20 ms</b></div>
              <div className="setting-row"><div><strong>MQ-135 target rate</strong><span>Relative response</span></div><b>1 Hz / 1000 ms</b></div>
              <div className="setting-row"><div><strong>DHT22 target rate</strong><span>Minimum 2 seconds between reads</span></div><b>0.5 Hz</b></div>
              <div className="setting-row"><div><strong>Local Buzzer Timeout</strong><span>Audible silence timeout for non-critical alerts</span></div><b>60 seconds</b></div>
            </section>
            <section className="panel"><div className="panel-heading"><div><h2>Experimental Motion Thresholds</h2><p>Rule-based detection parameters · Observational only</p></div><Scale size={18}/></div>
              <div className="setting-row"><div><strong>Impact Acceleration Peak</strong><span>Abrupt deceleration threshold (cooldown deduplicated)</span></div><b>24.5 m/s² (2.5 g)</b></div>
              <div className="setting-row"><div><strong>Impact Cooldown</strong><span>Suppresses secondary bounce duplicates</span></div><b>3.0 seconds</b></div>
              <div className="setting-row"><div><strong>Inactivity Observation Duration</strong><span>Continuous low-movement trigger</span></div><b>15.0 seconds</b></div>
              <div className="setting-row"><div><strong>Dynamic Gravity Gate</strong><span>Tolerance around static 1g before locking tilt</span></div><b>±1.50 m/s²</b></div>
              <div className="setting-row"><div><strong>Movement Intensity Boundaries</strong><span>Low &lt; 0.50 m/s² · Moderate &lt; 2.50 m/s²</span></div><b>Rolling σ</b></div>
              <div className="notice warning compact-notice"><AlertTriangle size={16}/><span>Notice: These are experimental monitoring thresholds. They do NOT activate buzzer alarms or emergency dispatch.</span></div>
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

function GasResponseValue({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="gas-response-value"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function TelemetryTable({ rows }: { rows: Array<{ timestamp: number; temperature?: number | null; humidity?: number | null; accel_x?: number | null; accel_y?: number | null; accel_z?: number | null; gas_adc?: number | null; emergency_button?: string | null; dht_status?: string | null; mpu_status?: string | null }> }) {
  if (rows.length === 0) return <div className="empty-state">No stored readings yet.</div>;
  return <div className="table-scroll"><table><thead><tr><th>Timestamp</th><th>Temp °C</th><th>Humidity %</th><th>Accel X</th><th>Accel Y</th><th>Accel Z</th><th>Gas ADC</th><th>SOS</th></tr></thead><tbody>
    {rows.map((r, i) => <tr key={`${r.timestamp}-${i}`}><td>{new Date(r.timestamp * 1000).toLocaleString()}</td><td>{fmt(r.temperature)}</td><td>{fmt(r.humidity)}</td><td>{fmt(r.accel_x, 3)}</td><td>{fmt(r.accel_y, 3)}</td><td>{fmt(r.accel_z, 3)}</td><td>{r.gas_adc ?? "—"}</td><td><span className={`table-state ${r.emergency_button === "PRESSED" ? "danger-state" : ""}`}>{r.emergency_button ?? "—"}</span></td></tr>)}
  </tbody></table></div>;
}
