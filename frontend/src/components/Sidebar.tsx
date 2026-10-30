import {
  Activity, BellRing, ChartNoAxesCombined, History, LayoutDashboard,
  Settings, Shield, Sun, Moon, PanelLeftClose, PanelLeftOpen
} from "lucide-react";

export type Page = "Overview" | "Analytics" | "Alerts" | "History" | "Settings";

const items = [
  { name: "Overview" as const, icon: LayoutDashboard },
  { name: "Analytics" as const, icon: ChartNoAxesCombined },
  { name: "Alerts" as const, icon: BellRing },
  { name: "History" as const, icon: History },
  { name: "Settings" as const, icon: Settings }
];

type Props = {
  page: Page;
  setPage: (page: Page) => void;
  theme: "dark" | "light";
  toggleTheme: () => void;
  collapsed: boolean;
  toggleCollapsed: () => void;
};

export default function Sidebar({ page, setPage, theme, toggleTheme, collapsed, toggleCollapsed }: Props) {
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="brand">
        <div className="brand-mark"><Shield size={22} /></div>
        {!collapsed && <div><strong>SENTINEL-X</strong><span>WORKER SAFETY SYSTEM</span></div>}
        <button className="icon-button collapse-button" onClick={toggleCollapsed} aria-label="Toggle sidebar">
          {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>
      <div className="nav-label">{!collapsed && "MONITORING"}</div>
      <nav>
        {items.map(({ name, icon: Icon }) => (
          <button key={name} className={`nav-item ${page === name ? "active" : ""}`} onClick={() => setPage(name)} title={collapsed ? name : undefined}>
            <Icon size={19} />
            {!collapsed && <span>{name}</span>}
            {!collapsed && name === "Alerts" && <span className="nav-count">!</span>}
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="nav-label">{!collapsed && "PREFERENCES"}</div>
        <button className="nav-item" onClick={toggleTheme} title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
          {theme === "dark" ? <Sun size={19} /> : <Moon size={19} />}
          {!collapsed && <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>}
        </button>
        {!collapsed && <div className="sidebar-foot"><Activity size={15} /> Prototype build</div>}
      </div>
    </aside>
  );
}
