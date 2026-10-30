import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { HistoryRecord, Telemetry } from "../types";

const initialTelemetry: Telemetry = {
  connected: false,
  status: "Waiting for backend",
  port: "COM5",
  baudrate: 115200,
  last_update: null,
  uptime_ms: null,
  dht_status: "unknown",
  mpu_status: "unknown",
  temperature: null,
  humidity: null,
  accel: null,
  gyro: null,
  gas_adc: null,
  gas_voltage_est: null,
  emergency_button: "UNKNOWN"
};

export function useTelemetry() {
  const [telemetry, setTelemetry] = useState<Telemetry>(initialTelemetry);
  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastPoll, setLastPoll] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [current, past] = await Promise.all([api.telemetry(), api.history(120)]);
      setTelemetry(current);
      setHistory(past.records ?? []);
      setError(null);
      setLastPoll(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to reach Flask API");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { telemetry, history, error, lastPoll, refresh };
}
