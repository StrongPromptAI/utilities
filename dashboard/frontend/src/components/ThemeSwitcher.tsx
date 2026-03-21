import { useEffect, useState } from "react";

const THEMES = [
  { key: "", label: "Midnight" },
  { key: "daylight", label: "Daylight" },
] as const;

export function ThemeSwitcher() {
  const [theme, setTheme] = useState(() => localStorage.getItem("kb-theme") || "");

  useEffect(() => {
    const root = document.documentElement;
    if (theme) {
      root.setAttribute("data-theme", theme);
      localStorage.setItem("kb-theme", theme);
    } else {
      root.removeAttribute("data-theme");
      localStorage.removeItem("kb-theme");
    }
  }, [theme]);

  return (
    <select
      value={theme}
      onChange={(e) => setTheme(e.target.value)}
      className="bg-surface text-body border border-border-input rounded px-2 py-1 text-sm cursor-pointer"
    >
      {THEMES.map((t) => (
        <option key={t.key} value={t.key}>
          {t.label}
        </option>
      ))}
    </select>
  );
}
