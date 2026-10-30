export type Vector3 = { x: number | null; y: number | null; z: number | null };

export type Telemetry = {
  connected: boolean;
  status: string;
  port: string;
  baudrate: number;
  last_update: number | null;
  uptime_ms: number | null;
  dht_status: string;
  mpu_status: string;
  temperature: number | null;
  humidity: number | null;
  accel: Vector3 | null;
  gyro: Vector3 | null;
  gas_adc: number | null;
  gas_voltage_est: number | null;
  emergency_button: string;
  raw_log?: string[];
};

export type HistoryRecord = {
  id?: number;
  timestamp: number;
  uptime_ms?: number | null;
  temperature?: number | null;
  humidity?: number | null;
  accel_x?: number | null;
  accel_y?: number | null;
  accel_z?: number | null;
  gyro_x?: number | null;
  gyro_y?: number | null;
  gyro_z?: number | null;
  gas_adc?: number | null;
  gas_voltage?: number | null;
  emergency_button?: string | null;
  dht_status?: string | null;
  mpu_status?: string | null;
};

export type PortInfo = { device: string; description: string; hwid?: string };

export type AlertEvent = {
  id: string;
  time: string;
  type: string;
  severity: "Emergency" | "Warning" | "Info";
  message: string;
  acknowledged: boolean;
};
