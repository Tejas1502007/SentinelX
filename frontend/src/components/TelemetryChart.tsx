import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis
} from "recharts";
import type { HistoryRecord } from "../types";

type Props = { history: HistoryRecord[]; theme: "dark" | "light" };

export default function TelemetryChart({ history, theme }: Props) {
  const data = history.map((r) => ({
    time: new Date(r.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    temperature: r.temperature,
    humidity: r.humidity,
    gas: r.gas_adc
  }));
  const grid = theme === "dark" ? "#283142" : "#e6eaf0";
  const text = theme === "dark" ? "#8f9bb0" : "#697586";

  return (
    <div className="chart-wrap">
      {data.length === 0 ? (
        <div className="empty-state">Waiting for historical readings from the backend…</div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid stroke={grid} strokeDasharray="3 4" vertical={false} />
            <XAxis dataKey="time" tick={{ fill: text, fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={32} />
            <YAxis tick={{ fill: text, fontSize: 11 }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ background: theme === "dark" ? "#171e2a" : "#fff", border: `1px solid ${grid}`, borderRadius: 10 }} />
            <Line type="monotone" dataKey="temperature" name="Temperature °C" stroke="#54a8ff" dot={false} strokeWidth={2} connectNulls />
            <Line type="monotone" dataKey="humidity" name="Humidity %" stroke="#43d6ae" dot={false} strokeWidth={2} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
