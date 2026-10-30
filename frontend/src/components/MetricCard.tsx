import type { ReactNode } from "react";

type Props = {
  label: string;
  value: string;
  unit?: string;
  note: string;
  icon: ReactNode;
  tone?: "blue" | "green" | "amber" | "violet" | "red";
};

export default function MetricCard({ label, value, unit, note, icon, tone = "blue" }: Props) {
  return (
    <section className="metric-card">
      <div className="metric-top">
        <span className="metric-label">{label}</span>
        <span className={`metric-icon ${tone}`}>{icon}</span>
      </div>
      <div className="metric-value">{value}<small>{unit}</small></div>
      <div className="metric-note">{note}</div>
    </section>
  );
}
