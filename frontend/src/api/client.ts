import type { AlertEvent, HistoryRecord, PortInfo, Telemetry } from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  if (!response.ok) {
    let details = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json() as { error?: string; details?: string };
      details = body.error ?? body.details ?? details;
    } catch {
      // Keep the HTTP status message when the server did not return JSON.
    }
    throw new Error(details);
  }
  return response.json() as Promise<T>;
}

export const api = {
  telemetry: () => request<Telemetry>("/api/telemetry"),
  history: (limit = 120) =>
    request<{ count: number; records: HistoryRecord[] }>(`/api/history?limit=${limit}`),
  alerts: (limit = 50) =>
    request<{ count: number; alerts: AlertEvent[] }>(`/api/alerts?limit=${limit}`),
  acknowledgeAlert: (id: number | string) =>
    request<{ success: boolean; alert_id: number | string }>(`/api/alerts/${id}/ack`, {
      method: "POST"
    }),
  ports: () => request<{ ports: PortInfo[] }>("/api/ports"),
  configurePort: (port: string, baudrate: number, source?: string) =>
    request<{ success: boolean; message?: string }>("/api/config", {
      method: "POST",
      body: JSON.stringify({ port, baudrate, source })
    }),
  motionConfig: () => request<Record<string, number>>("/api/motion/config"),
  updateMotionConfig: (updates: Record<string, number>) =>
    request<{ success: boolean; message?: string; config?: Record<string, number> }>("/api/motion/config", {
      method: "POST",
      body: JSON.stringify(updates)
    }),
  gasConfig: () => request<Record<string, any>>("/api/gas/config"),
  updateGasConfig: (updates: Record<string, any>) =>
    request<{ success: boolean; message?: string; config?: Record<string, any> }>("/api/gas/config", {
      method: "POST",
      body: JSON.stringify(updates)
    }),
  captureGasBaseline: (operatorIdentity: string) =>
    request<{ success: boolean; message: string; baseline: { baseline_adc: number; captured_at: number; operator_identity: string } }>("/api/gas/baseline", {
      method: "POST",
      body: JSON.stringify({ operator_identity: operatorIdentity })
    }),
  resetGasBaseline: (operatorIdentity: string) =>
    request<{ success: boolean; message: string }>("/api/gas/baseline", {
      method: "DELETE",
      body: JSON.stringify({ operator_identity: operatorIdentity })
    })
};
