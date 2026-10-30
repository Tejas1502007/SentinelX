import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis
} from "recharts";
import type { HistoryRecord } from "../types";

type Props = { history: HistoryRecord[]; theme: "dark" | "light" };

export default function MotionChart({ history, theme }: Props) {
  const data = history.map((r) => {
    const ax = r.accel_x ?? 0;
    const ay = r.accel_y ?? 0;
    const az = r.accel_z ?? 0;
    const hasAccel = r.accel_x != null && r.accel_y != null && r.accel_z != null;
    const mag = hasAccel ? Math.sqrt(ax * ax + ay * ay + az * az) : null;

    const gx = r.gyro_x ?? 0;
    const gy = r.gyro_y ?? 0;
    const gz = r.gyro_z ?? 0;
    const hasGyro = r.gyro_x != null && r.gyro_y != null && r.gyro_z != null;
    const gyroMag = hasGyro ? Math.sqrt(gx * gx + gy * gy + gz * gz) : null;

    // Gated static tilt: only calculate when dynamic acceleration is near 1g (9.81 m/s^2)
    let tilt: number | null = null;
    if (mag !== null && Math.abs(mag - 9.80665) <= 1.8 && (gyroMag ?? 0) <= 0.8) {
      const cosZ = Math.min(1.0, Math.max(-1.0, Math.abs(az) / (mag || 1)));
      tilt = Number((Math.acos(cosZ) * (180 / Math.PI)).toFixed(1));
    }

    return {
      time: new Date(r.timestamp * 1000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      }),
      magnitude: mag !== null ? Number(mag.toFixed(2)) : null,
      gyro: gyroMag !== null ? Number(gyroMag.toFixed(3)) : null,
      tilt
    };
  });

  const grid = theme === "dark" ? "#283142" : "#e6eaf0";
  const text = theme === "dark" ? "#8f9bb0" : "#697586";

  return (
    <div className="chart-wrap">
      {data.length === 0 ? (
        <div className="empty-state">Waiting for historical motion readings…</div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid stroke={grid} strokeDasharray="3 4" vertical={false} />
            <XAxis dataKey="time" tick={{ fill: text, fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={32} />
            <YAxis tick={{ fill: text, fontSize: 11 }} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={{
                background: theme === "dark" ? "#171e2a" : "#fff",
                border: `1px solid ${grid}`,
                borderRadius: 10
              }}
            />
            <Line type="monotone" dataKey="magnitude" name="Accel Mag (m/s²)" stroke="#7952f5" dot={false} strokeWidth={2} connectNulls />
            <Line type="monotone" dataKey="tilt" name="Gated Tilt (°)" stroke="#ffab00" dot={false} strokeWidth={1.5} strokeDasharray="3 3" connectNulls />
            <Line type="monotone" dataKey="gyro" name="Gyro Mag (rad/s)" stroke="#43d6ae" dot={false} strokeWidth={1.5} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
