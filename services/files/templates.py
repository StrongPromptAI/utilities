"""HTML templates for oxp.files. CSS lifted verbatim from
services/oidc-otp/main.py so the file UI matches the OTP sign-in skin.

Public helpers:
- _page(title, body_html)              full HTML doc
- login_redirect_html()                shown briefly during /oidc/login redirect
- file_browser_html(email, files)      main file-list + upload UI
- error_html(title, message)           generic error frame
"""

from __future__ import annotations
from datetime import datetime
from html import escape
from urllib.parse import quote


_LOGO_DATA_URI = "data:image/png;base64,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"  # noqa: E501


_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300;9..144,500;9..144,700&family=Manrope:wght@300;400;500;700&family=JetBrains+Mono:wght@500;700&display=swap');

:root {
  --brand-red: #c71a2f;
  --brand-warm: #fee7b5;
  --brand-green: #3fb950;
  --base-bg: #0a0b0d;
  --panel-bg: rgba(20, 21, 24, 0.72);
  --panel-border: rgba(255, 255, 255, 0.06);
  --text-primary: #f5f1e8;
  --text-muted: #8a8a8a;
  --input-line: #2a2b2f;
  --row-hover: rgba(255, 255, 255, 0.02);
  --menu-bg: rgba(16, 17, 20, 0.96);
  --menu-border: rgba(255, 255, 255, 0.08);
  --menu-hover: rgba(255, 255, 255, 0.05);
  --noise-opacity: 0.05;
  --noise-blend: overlay;
  --body-gradient-from: #0a0b0d;
  --body-gradient-to: #131418;
  --body-glow: rgba(199, 26, 47, 0.18);
  --foot-color: rgba(245, 241, 232, 0.18);
}

html[data-theme="light"] {
  --brand-red: #c71a2f;
  --brand-warm: #b8870f;
  --brand-green: #2e7d3a;
  --base-bg: #f4f1ea;
  --panel-bg: rgba(255, 252, 245, 0.92);
  --panel-border: rgba(20, 21, 24, 0.08);
  --text-primary: #16181c;
  --text-muted: #5b5e66;
  --input-line: #d8d2c4;
  --row-hover: rgba(0, 0, 0, 0.03);
  --menu-bg: #ffffff;
  --menu-border: rgba(20, 21, 24, 0.1);
  --menu-hover: rgba(0, 0, 0, 0.04);
  --noise-opacity: 0.03;
  --noise-blend: multiply;
  --body-gradient-from: #f4f1ea;
  --body-gradient-to: #ece7da;
  --body-glow: rgba(199, 26, 47, 0.08);
  --foot-color: rgba(20, 21, 24, 0.4);
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  min-height: 100vh;
  font-family: 'Manrope', sans-serif;
  font-weight: 400;
  color: var(--text-primary);
  background: var(--base-bg);
  -webkit-font-smoothing: antialiased;
}

body {
  background:
    radial-gradient(ellipse at 50% 35%, var(--body-glow) 0%, transparent 55%),
    linear-gradient(180deg, var(--body-gradient-from) 0%, var(--body-gradient-to) 100%);
  position: relative;
  overflow-x: hidden;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 40px 20px;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-repeat: no-repeat;
  background-position: 110% 95%;
  background-size: 60vmin auto;
  filter: brightness(0) invert(1);
  opacity: 0.04;
  pointer-events: none;
  z-index: 0;
}

body::after {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' /><feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.45 0' /></filter><rect width='100%' height='100%' filter='url(%23n)' /></svg>");
  opacity: var(--noise-opacity);
  pointer-events: none;
  mix-blend-mode: var(--noise-blend);
  z-index: 0;
}

main {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 420px;
  padding: 56px 44px 48px;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 2px;
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.04) inset,
    0 30px 80px -20px rgba(0, 0, 0, 0.5);
}

main.wide {
  max-width: 920px;
  padding: 48px 48px 40px;
}

.title {
  font-family: 'Fraunces', serif;
  font-weight: 500;
  font-size: clamp(28px, 5vw, 38px);
  letter-spacing: -0.01em;
  line-height: 1.05;
  margin: 0 0 14px;
  color: var(--text-primary);
}

.bar {
  width: 48px;
  height: 2px;
  background: var(--brand-red);
  border: 0;
  margin: 0 0 22px;
}

.subtitle {
  font-size: 14px;
  font-weight: 400;
  line-height: 1.55;
  color: var(--text-muted);
  margin: 0 0 30px;
  letter-spacing: 0.005em;
}

.err {
  font-size: 13px;
  color: var(--brand-red);
  background: rgba(199, 26, 47, 0.08);
  border-left: 2px solid var(--brand-red);
  padding: 10px 14px;
  margin: 0 0 22px;
  font-weight: 500;
}

label {
  display: block;
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 8px;
  font-weight: 500;
}

button {
  padding: 14px 28px;
  background: var(--brand-red);
  color: #fff;
  border: 0;
  border-radius: 0;
  font-family: 'Manrope', sans-serif;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  cursor: pointer;
  transition: transform 120ms ease, box-shadow 200ms ease, background 200ms ease;
}

button:hover {
  background: #d62540;
  box-shadow: 0 6px 24px -8px rgba(199, 26, 47, 0.55);
  transform: translateY(-1px);
}

button:active { transform: translateY(0); }

button.ghost {
  background: transparent;
  color: var(--text-muted);
  padding: 10px 14px;
  border: 1px solid var(--input-line);
  font-size: 10px;
}

button.ghost:hover {
  color: var(--text-primary);
  border-color: var(--brand-warm);
  background: transparent;
  box-shadow: none;
}

.brand-foot {
  margin: 36px 0 0;
  text-align: center;
  font-size: 9px;
  letter-spacing: 0.32em;
  text-transform: uppercase;
  color: var(--foot-color);
  font-weight: 500;
}

/* File browser */

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 28px;
  font-size: 12px;
  color: var(--text-muted);
  letter-spacing: 0.06em;
}

.toolbar .who {
  font-family: 'JetBrains Mono', monospace;
  color: var(--brand-warm);
  font-size: 11px;
  letter-spacing: 0.04em;
}

.upload-zone {
  border: 1px dashed var(--input-line);
  padding: 28px 24px;
  text-align: center;
  margin-bottom: 32px;
  transition: border-color 200ms ease, background 200ms ease;
}

.upload-zone.drag {
  border-color: var(--brand-red);
  background: rgba(199, 26, 47, 0.04);
}

.upload-zone p {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--text-muted);
  letter-spacing: 0.02em;
}

.upload-zone input[type="file"] { display: none; }

.upload-zone .pick {
  display: inline-block;
  padding: 12px 24px;
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--brand-warm);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  cursor: pointer;
}

.upload-zone .pick:hover { background: rgba(254, 231, 181, 0.08); }

.progress {
  margin-top: 14px;
  height: 2px;
  background: var(--input-line);
  display: none;
}

.progress.active { display: block; }

.progress > div {
  height: 100%;
  background: var(--brand-red);
  width: 0;
  transition: width 200ms ease;
}

table.files {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

table.files thead th {
  text-align: left;
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--text-muted);
  font-weight: 500;
  padding: 0 14px 12px;
  border-bottom: 1px solid var(--input-line);
}

table.files tbody td {
  padding: 14px;
  border-bottom: 1px solid var(--input-line);
  vertical-align: middle;
}

table.files tbody tr:hover td {
  background: var(--row-hover);
}

table.files .name {
  font-weight: 500;
  color: var(--text-primary);
}

table.files .meta {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.02em;
  text-align: right;
  white-space: nowrap;
}

table.files .actions {
  text-align: right;
  white-space: nowrap;
}

table.files .actions a {
  color: var(--brand-warm);
  text-decoration: none;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-weight: 700;
  margin-left: 18px;
  border-bottom: 1px solid rgba(254, 231, 181, 0.2);
  padding-bottom: 1px;
}

table.files .actions a:hover { border-bottom-color: var(--brand-warm); }

table.files .actions a.danger { color: var(--brand-red); border-bottom-color: rgba(199, 26, 47, 0.3); }
table.files .actions a.danger:hover { border-bottom-color: var(--brand-red); }

table.files .actions a.play { color: var(--brand-green); }

table.files .actions a.icon {
  border-bottom: none;
  padding-bottom: 0;
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  opacity: 0.75;
  transition: opacity 150ms ease, color 150ms ease;
}

table.files .actions a.icon:hover {
  opacity: 1;
  border-bottom: none;
}

table.files .actions a.icon svg {
  display: block;
}

.empty {
  text-align: center;
  padding: 60px 0;
  color: var(--text-muted);
  font-size: 13px;
  letter-spacing: 0.04em;
}

.actions-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 6px 0 18px;
  gap: 14px;
  flex-wrap: wrap;
}

.crumbs {
  font-size: 12px;
  color: var(--text-muted);
  letter-spacing: 0.04em;
}

.crumbs a {
  color: var(--brand-warm);
  text-decoration: none;
  border-bottom: 1px solid rgba(254, 231, 181, 0.2);
  padding-bottom: 1px;
}

.crumbs a:hover { border-bottom-color: var(--brand-warm); }

.crumbs .sep { margin: 0 10px; opacity: 0.45; }

.crumbs .here {
  color: var(--text-primary);
  font-weight: 500;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}

button.new-folder {
  padding: 8px 14px;
  background: transparent;
  color: var(--brand-warm);
  border: 1px solid var(--brand-warm);
  border-radius: 0;
  font-family: 'Manrope', sans-serif;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  cursor: pointer;
  transition: background 200ms ease, color 200ms ease;
}

button.new-folder:hover {
  background: rgba(254, 231, 181, 0.08);
  box-shadow: none;
  transform: none;
}

tr.section-row td {
  padding: 14px 14px 6px;
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--text-muted);
  background: transparent !important;
  border-bottom: 1px solid var(--input-line);
  font-weight: 500;
}

tr.folder-row .name { color: var(--brand-warm); }
tr.folder-row:hover td { background: rgba(254, 231, 181, 0.03); }

.folder-link {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: inherit;
  text-decoration: none;
}

.folder-link:hover { color: var(--text-primary); }
.folder-link svg { flex-shrink: 0; opacity: 0.9; }

.upload-zone .target {
  display: inline-block;
  margin-left: 6px;
  color: var(--brand-warm);
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  letter-spacing: 0.04em;
}

/* Account button + dropdown */

.account {
  position: relative;
  display: inline-block;
}

.account-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--brand-red);
  color: #fff;
  font-family: 'Manrope', sans-serif;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  border: 1px solid rgba(255, 255, 255, 0.1);
  cursor: pointer;
  padding: 0;
  text-transform: uppercase;
  transition: transform 120ms ease, box-shadow 200ms ease;
}

.account-btn:hover {
  background: var(--brand-red);
  box-shadow: 0 4px 16px -4px rgba(199, 26, 47, 0.45);
  transform: translateY(-1px);
}

.account-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 220px;
  background: var(--menu-bg);
  border: 1px solid var(--menu-border);
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  box-shadow: 0 18px 48px -12px rgba(0, 0, 0, 0.4);
  display: none;
  flex-direction: column;
  z-index: 20;
  padding: 6px 0;
}

.account-menu.open { display: flex; }

.account-menu-email {
  padding: 10px 16px 12px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.02em;
  border-bottom: 1px solid var(--menu-border);
  word-break: break-all;
}

.account-menu a,
.account-menu button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 16px;
  background: transparent;
  border: 0;
  color: var(--text-primary);
  font-family: 'Manrope', sans-serif;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.02em;
  text-transform: none;
  text-decoration: none;
  text-align: left;
  cursor: pointer;
  transition: background 120ms ease;
}

.account-menu a:hover,
.account-menu button:hover {
  background: var(--menu-hover);
  box-shadow: none;
  transform: none;
}

.account-menu a svg,
.account-menu button svg {
  flex-shrink: 0;
  opacity: 0.7;
}

.account-menu .divider {
  height: 1px;
  background: var(--menu-border);
  margin: 4px 0;
}

/* Activity log table */

.activity-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 6px 0 18px;
  gap: 14px;
  flex-wrap: wrap;
}

.activity-toolbar .day-picker {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  letter-spacing: 0.04em;
  color: var(--text-muted);
}

.activity-toolbar select {
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--input-line);
  padding: 6px 10px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  letter-spacing: 0.02em;
  border-radius: 0;
}

table.activity {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

table.activity thead th {
  text-align: left;
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--text-muted);
  font-weight: 500;
  padding: 0 14px 12px;
  border-bottom: 1px solid var(--input-line);
}

table.activity tbody td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--input-line);
  vertical-align: top;
}

table.activity tbody tr:hover td { background: var(--row-hover); }

table.activity .ts {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
}

table.activity .user {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--brand-warm);
  word-break: break-all;
}

table.activity .action {
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--text-primary);
}

table.activity .action.upload { color: var(--brand-green); }
table.activity .action.delete { color: var(--brand-red); }

table.activity .file {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-primary);
  word-break: break-all;
}

table.activity .folder {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-muted);
}

table.activity .ip {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--text-muted);
  white-space: nowrap;
}

@media (max-width: 600px) {
  main, main.wide { padding: 32px 20px; border-radius: 0; max-width: 100%; }
  table.files .meta { display: none; }
  table.activity .ip, table.activity .folder { display: none; }
}
"""


def _page(title: str, body_html: str, theme: str = "dark") -> str:
    theme_attr = "light" if theme == "light" else "dark"
    # Inline early-paint script reads the theme cookie and applies it before
    # the rest of CSS evaluates — avoids flash when the cookie disagrees with
    # the server-rendered default (e.g. on /activity after a theme flip in a
    # sibling tab).
    inline_theme_js = (
        "(function(){try{var m=document.cookie.match(/oxp_files_theme=(light|dark)/);"
        "if(m){document.documentElement.setAttribute('data-theme',m[1]);}}catch(e){}})();"
    )
    return (
        f'<!DOCTYPE html><html lang="en" data-theme="{theme_attr}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(title)}</title>'
        f'<script>{inline_theme_js}</script>'
        f'<style>{_CSS}</style></head>'
        f'<body>{body_html}</body></html>'
    )


def login_redirect_html() -> str:
    body = """
      <main>
        <h1 class="title">OXP File Drop</h1>
        <hr class="bar">
        <p class="subtitle">Redirecting to sign in&hellip;</p>
        <p class="brand-foot">Orthokinetix &middot; OrthoXpress</p>
      </main>
    """
    return _page("Sign in — OXP File Drop", body)


def error_html(title: str, message: str) -> str:
    body = f"""
      <main>
        <h1 class="title">OXP File Drop</h1>
        <hr class="bar">
        <div class="err">{escape(message)}</div>
        <p class="brand-foot">Orthokinetix &middot; OrthoXpress</p>
      </main>
    """
    return _page(title, body)


_ICON_PLAY = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" '
    'stroke="currentColor" stroke-width="1" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<polygon points="6 4 20 12 6 20 6 4"/>'
    '</svg>'
)

_ICON_RENAME = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
    '<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>'
    '</svg>'
)

_ICON_DOWNLOAD = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
    '<polyline points="7 10 12 15 17 10"/>'
    '<line x1="12" y1="15" x2="12" y2="3"/>'
    '</svg>'
)

_ICON_TRASH = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="3 6 5 6 21 6"/>'
    '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
    '<line x1="10" y1="11" x2="10" y2="17"/>'
    '<line x1="14" y1="11" x2="14" y2="17"/>'
    '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
    '</svg>'
)

_ICON_FOLDER = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
    '</svg>'
)

_ICON_MOVE = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="5 9 2 12 5 15"/>'
    '<polyline points="9 5 12 2 15 5"/>'
    '<polyline points="15 19 12 22 9 19"/>'
    '<polyline points="19 9 22 12 19 15"/>'
    '<line x1="2" y1="12" x2="22" y2="12"/>'
    '<line x1="12" y1="2" x2="12" y2="22"/>'
    '</svg>'
)

_ICON_FOLDER_PLUS = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px;margin-right:8px">'
    '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
    '<line x1="12" y1="11" x2="12" y2="17"/>'
    '<line x1="9" y1="14" x2="15" y2="14"/>'
    '</svg>'
)

_ICON_PREVIEW = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
    '<circle cx="12" cy="12" r="3"/>'
    '</svg>'
)

_ICON_LOGOUT = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
    '<polyline points="16 17 21 12 16 7"/>'
    '<line x1="21" y1="12" x2="9" y2="12"/>'
    '</svg>'
)

_ICON_SUN = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="4"/>'
    '<line x1="12" y1="2" x2="12" y2="5"/>'
    '<line x1="12" y1="19" x2="12" y2="22"/>'
    '<line x1="2" y1="12" x2="5" y2="12"/>'
    '<line x1="19" y1="12" x2="22" y2="12"/>'
    '<line x1="4.93" y1="4.93" x2="7.05" y2="7.05"/>'
    '<line x1="16.95" y1="16.95" x2="19.07" y2="19.07"/>'
    '<line x1="4.93" y1="19.07" x2="7.05" y2="16.95"/>'
    '<line x1="16.95" y1="7.05" x2="19.07" y2="4.93"/>'
    '</svg>'
)

_ICON_MOON = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
    '</svg>'
)

_ICON_LIST = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<line x1="8" y1="6" x2="21" y2="6"/>'
    '<line x1="8" y1="12" x2="21" y2="12"/>'
    '<line x1="8" y1="18" x2="21" y2="18"/>'
    '<line x1="3" y1="6" x2="3.01" y2="6"/>'
    '<line x1="3" y1="12" x2="3.01" y2="12"/>'
    '<line x1="3" y1="18" x2="3.01" y2="18"/>'
    '</svg>'
)


def _account_menu_html(email: str) -> str:
    """Account button + dropdown menu. Used in both file browser and activity pages."""
    initial = escape((email[:1] or "?").upper())
    return f"""
      <div class="account">
        <button class="account-btn" type="button" id="account-btn"
                aria-haspopup="true" aria-expanded="false"
                aria-label="Account menu">{initial}</button>
        <div class="account-menu" id="account-menu" role="menu">
          <div class="account-menu-email">{escape(email)}</div>
          <a href="/activity" target="_blank" rel="noopener" role="menuitem">
            {_ICON_LIST}<span>View Activity Log</span>
          </a>
          <button type="button" id="theme-toggle-btn" role="menuitem">
            <span id="theme-icon">{_ICON_SUN}</span>
            <span id="theme-label">Light mode</span>
          </button>
          <div class="divider"></div>
          <a href="/logout" role="menuitem">{_ICON_LOGOUT}<span>Sign out</span></a>
        </div>
      </div>
    """


_ACCOUNT_MENU_JS = """
(function() {
  const btn = document.getElementById('account-btn');
  const menu = document.getElementById('account-menu');
  const themeBtn = document.getElementById('theme-toggle-btn');
  const themeIcon = document.getElementById('theme-icon');
  const themeLabel = document.getElementById('theme-label');
  const ICON_SUN = __ICON_SUN__;
  const ICON_MOON = __ICON_MOON__;

  if (!btn || !menu) return;

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }

  function syncThemeUI() {
    const t = currentTheme();
    if (t === 'light') {
      themeIcon.innerHTML = ICON_MOON;
      themeLabel.textContent = 'Dark mode';
    } else {
      themeIcon.innerHTML = ICON_SUN;
      themeLabel.textContent = 'Light mode';
    }
  }
  syncThemeUI();

  function close() {
    menu.classList.remove('open');
    btn.setAttribute('aria-expanded', 'false');
  }
  function open() {
    menu.classList.add('open');
    btn.setAttribute('aria-expanded', 'true');
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('open')) close(); else open();
  });
  document.addEventListener('click', (e) => {
    if (!menu.contains(e.target) && e.target !== btn) close();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
  });

  if (themeBtn) {
    themeBtn.addEventListener('click', async () => {
      const next = currentTheme() === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      syncThemeUI();
      try {
        await fetch('/api/theme', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ theme: next }),
        });
      } catch (e) {
        // Cookie write failed — preference persists for this tab only.
      }
    });
  }
})();
"""


def _account_menu_js() -> str:
    return (
        _ACCOUNT_MENU_JS
        .replace("__ICON_SUN__", repr(_ICON_SUN))
        .replace("__ICON_MOON__", repr(_ICON_MOON))
    )


_ACTION_LABELS = {
    "login": "Sign in",
    "logout": "Sign out",
    "upload": "Upload",
    "download": "Download",
    "delete": "Delete",
    "rename": "Rename",
    "move": "Move",
}


def activity_html(
    email: str,
    events: list[dict],
    current_day: str,
    available_days: list[str],
    theme: str = "dark",
) -> str:
    """Activity log page. `events` already sorted newest-first; each row is
    {ts, user, action, file, folder, ip}."""
    if current_day not in available_days:
        available_days = [current_day, *available_days]

    day_options = "".join(
        f'<option value="{escape(d)}"{" selected" if d == current_day else ""}>{escape(d)}</option>'
        for d in available_days
    )

    if events:
        rows: list[str] = []
        for ev in events:
            ts = ev.get("ts", "")
            try:
                ts_display = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%H:%M:%S")
            except ValueError:
                ts_display = ts
            action = ev.get("action", "")
            action_label = _ACTION_LABELS.get(action, action.capitalize())
            file_val = ev.get("file") or ""
            folder_val = ev.get("folder") or ""
            ip_val = ev.get("ip") or ""
            rows.append(
                f'<tr>'
                f'<td class="ts">{escape(ts_display)}</td>'
                f'<td class="user">{escape(ev.get("user", ""))}</td>'
                f'<td><span class="action {escape(action)}">{escape(action_label)}</span></td>'
                f'<td class="file">{escape(file_val)}</td>'
                f'<td class="folder">{escape(folder_val) if folder_val else "&mdash;"}</td>'
                f'<td class="ip">{escape(ip_val)}</td>'
                f'</tr>'
            )
        table_html = (
            f'<table class="activity"><thead><tr>'
            f'<th>Time (UTC)</th><th>User</th><th>Action</th><th>File</th><th>Folder</th><th>IP</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
        )
    else:
        table_html = '<div class="empty">No activity recorded for this day.</div>'

    body = f"""
      <main class="wide">
        <div class="toolbar">
          <div>
            <h1 class="title" style="font-size:24px;margin:0">Activity Log</h1>
          </div>
          <div>
            {_account_menu_html(email)}
          </div>
        </div>
        <hr class="bar">

        <div class="activity-toolbar">
          <div class="crumbs">
            <a href="/">&larr; Files</a>
          </div>
          <form class="day-picker" method="get" action="/activity">
            <label for="day-select" style="margin:0">Day:</label>
            <select id="day-select" name="day" onchange="this.form.submit()">
              {day_options}
            </select>
          </form>
        </div>

        {table_html}

        <p class="brand-foot">Orthokinetix &middot; OrthoXpress</p>
      </main>
      <script>{_account_menu_js()}</script>
    """
    return _page("Activity Log — OXP File Drop", body, theme=theme)


def _fmt_size(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.1f} KB"
    return f"{n} B"


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def file_browser_html(
    email: str,
    files: list[dict],
    folders: list[str],
    current_folder: str,
    theme: str = "dark",
) -> str:
    """`files`: [{name, size, last_modified}], `folders`: [str], `current_folder`: "" = root."""
    import json as _json

    in_folder = bool(current_folder)
    cf_escaped = escape(current_folder)
    cf_quoted = quote(current_folder, safe="")

    # Folder rows — only shown at root view
    folder_rows: list[str] = []
    if not in_folder and folders:
        folder_rows.append(
            '<tr class="section-row"><td colspan="4">Folders</td></tr>'
        )
        for folder_name in folders:
            fn = escape(folder_name)
            fn_url = quote(folder_name, safe="")
            folder_rows.append(
                f'<tr class="folder-row">'
                f'<td class="name">'
                f'<a class="folder-link" href="/?folder={fn_url}">{_ICON_FOLDER}<span>{fn}</span></a>'
                f'</td>'
                f'<td class="meta">folder</td>'
                f'<td class="meta">&mdash;</td>'
                f'<td class="actions">'
                f'<a class="icon" href="#" data-folder-rename="{fn}" title="Rename folder" aria-label="Rename folder {fn}">{_ICON_RENAME}</a>'
                f'<a class="icon danger" href="#" data-folder-delete="{fn}" title="Delete folder" aria-label="Delete folder {fn}">{_ICON_TRASH}</a>'
                f'</td>'
                f'</tr>'
            )

    # File rows
    file_rows: list[str] = []
    if files:
        if not in_folder and folders:
            file_rows.append(
                '<tr class="section-row"><td colspan="4">Files</td></tr>'
            )
        for f in files:
            raw_name = f["name"]
            name = escape(raw_name)
            size = _fmt_size(f["size"])
            modified = _fmt_time(f["last_modified"])
            lower = raw_name.lower()
            is_wav = lower.endswith(".wav")
            is_pdf = lower.endswith(".pdf")
            play_link = (
                f'<a class="icon play" href="#" data-play="{name}" title="Play" aria-label="Play {name}">{_ICON_PLAY}</a>'
                if is_wav else ''
            )
            stream_href = f'/api/files/stream/{quote(raw_name, safe="")}'
            if in_folder:
                stream_href += f'?folder={cf_quoted}'
            preview_link = (
                f'<a class="icon preview" href="{stream_href}" target="_blank" rel="noopener" '
                f'data-preview="{name}" title="Preview" aria-label="Preview {name}">{_ICON_PREVIEW}</a>'
                if is_pdf else ''
            )
            download_href = f'/api/files/download/{quote(raw_name, safe="")}'
            if in_folder:
                download_href += f'?folder={cf_quoted}'
            file_rows.append(
                f'<tr>'
                f'<td class="name">{name}</td>'
                f'<td class="meta">{size}</td>'
                f'<td class="meta">{modified}</td>'
                f'<td class="actions">'
                f'{play_link}'
                f'{preview_link}'
                f'<a class="icon" href="#" data-rename="{name}" title="Rename" aria-label="Rename {name}">{_ICON_RENAME}</a>'
                f'<a class="icon" href="#" data-move="{name}" title="Move to folder" aria-label="Move {name}">{_ICON_MOVE}</a>'
                f'<a class="icon" href="{download_href}" title="Download" aria-label="Download {name}">{_ICON_DOWNLOAD}</a>'
                f'<a class="icon danger" href="#" data-delete="{name}" title="Delete" aria-label="Delete {name}">{_ICON_TRASH}</a>'
                f'</td>'
                f'</tr>'
            )
            if is_wav:
                file_rows.append(
                    f'<tr class="player-row" data-player-for="{name}" style="display:none">'
                    f'<td colspan="4" style="padding:8px 14px;border-bottom:1px solid var(--input-line);background:rgba(255,255,255,0.02);">'
                    f'<audio controls preload="none" style="width:100%;height:32px;"></audio>'
                    f'</td>'
                    f'</tr>'
                )

    all_rows = folder_rows + file_rows

    if all_rows:
        table_or_empty = (
            f'<table class="files"><thead><tr>'
            f'<th>Name</th><th class="meta">Size</th><th class="meta">Modified</th><th></th>'
            f'</tr></thead><tbody>{"".join(all_rows)}</tbody></table>'
        )
    else:
        msg = (
            "This folder is empty. Drop a file in to get started."
            if in_folder
            else "No files or folders yet. Drag a file in, or create a folder."
        )
        table_or_empty = f'<div class="empty">{msg}</div>'

    # Breadcrumb (only meaningful inside a folder; at root it's blank)
    crumbs_html = (
        f'<div class="crumbs">'
        f'<a href="/">Root</a><span class="sep">/</span>'
        f'<span class="here">{cf_escaped}</span>'
        f'</div>'
        if in_folder
        else '<div class="crumbs"><span class="here">Root</span></div>'
    )

    # "New folder" button (root only — we only support one level)
    new_folder_btn = (
        f'<button class="new-folder" type="button" id="new-folder-btn">'
        f'{_ICON_FOLDER_PLUS}New folder'
        f'</button>'
        if not in_folder
        else ''
    )

    upload_target_html = (
        f'<span class="target">→ {cf_escaped}</span>' if in_folder else ''
    )

    folders_json = _json.dumps(folders)

    body = f"""
      <main class="wide">
        <div class="toolbar">
          <div>
            <h1 class="title" style="font-size:24px;margin:0">OXP File Drop</h1>
          </div>
          <div>
            {_account_menu_html(email)}
          </div>
        </div>
        <hr class="bar">

        <div class="actions-row">
          {crumbs_html}
          {new_folder_btn}
        </div>

        <div class="upload-zone" id="dropzone">
          <p>Drop a file to upload{upload_target_html}, or</p>
          <label class="pick" for="file-input">Choose file</label>
          <input id="file-input" type="file">
          <div class="progress" id="progress"><div></div></div>
        </div>

        {table_or_empty}

        <p class="brand-foot">Orthokinetix &middot; OrthoXpress</p>
      </main>

      <script id="folders-data" type="application/json">{folders_json}</script>
      <script>{_account_menu_js()}</script>
      <script>
      (function() {{
        const CURRENT_FOLDER = {_json.dumps(current_folder)};
        const FOLDERS = JSON.parse(document.getElementById('folders-data').textContent);

        const dz = document.getElementById('dropzone');
        const input = document.getElementById('file-input');
        const progress = document.getElementById('progress');
        const bar = progress.querySelector('div');

        function qsFolder() {{
          return CURRENT_FOLDER ? ('?folder=' + encodeURIComponent(CURRENT_FOLDER)) : '';
        }}

        async function upload(file) {{
          let presigned;
          try {{
            const r = await fetch('/api/files/upload-url', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ filename: file.name, folder: CURRENT_FOLDER || null }}),
            }});
            if (!r.ok) {{
              alert('Could not get upload URL: ' + r.status + ' ' + (await r.text()));
              return;
            }}
            presigned = await r.json();
          }} catch (e) {{
            alert('Could not get upload URL: ' + e.message);
            return;
          }}

          progress.classList.add('active');
          bar.style.width = '0%';
          const xhr = new XMLHttpRequest();
          xhr.open('PUT', presigned.url);
          if (file.type) xhr.setRequestHeader('Content-Type', file.type);
          xhr.upload.onprogress = (e) => {{
            if (e.lengthComputable) {{
              bar.style.width = ((e.loaded / e.total) * 100).toFixed(1) + '%';
            }}
          }};
          xhr.onload = async () => {{
            if (xhr.status >= 200 && xhr.status < 300) {{
              try {{
                await fetch('/api/files/uploaded', {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json' }},
                  body: JSON.stringify({{ filename: file.name, folder: CURRENT_FOLDER || null }}),
                }});
              }} catch (e) {{
                // Activity log failure shouldn't block the user — the file did upload.
              }}
              window.location.reload();
            }} else {{
              alert('Upload to storage failed: ' + xhr.status + ' ' + xhr.responseText);
              progress.classList.remove('active');
            }}
          }};
          xhr.onerror = () => {{
            alert('Upload failed (network) — try a smaller file or check your connection');
            progress.classList.remove('active');
          }};
          xhr.send(file);
        }}

        input.addEventListener('change', () => {{
          if (input.files.length) upload(input.files[0]);
        }});

        ['dragenter', 'dragover'].forEach(ev => {{
          dz.addEventListener(ev, (e) => {{ e.preventDefault(); dz.classList.add('drag'); }});
        }});
        ['dragleave', 'drop'].forEach(ev => {{
          dz.addEventListener(ev, (e) => {{ e.preventDefault(); dz.classList.remove('drag'); }});
        }});
        dz.addEventListener('drop', (e) => {{
          if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
        }});

        // ── File actions ────────────────────────────────────────────────
        document.querySelectorAll('a[data-delete]').forEach(a => {{
          a.addEventListener('click', async (e) => {{
            e.preventDefault();
            const name = a.getAttribute('data-delete');
            if (!confirm('Delete ' + name + '?\\n\\n(Recoverable for 30 days via bucket versioning.)')) return;
            const r = await fetch('/api/files/' + encodeURIComponent(name) + qsFolder(), {{ method: 'DELETE' }});
            if (r.ok) window.location.reload();
            else alert('Delete failed: ' + r.status);
          }});
        }});

        document.querySelectorAll('a[data-rename]').forEach(a => {{
          a.addEventListener('click', async (e) => {{
            e.preventDefault();
            const oldName = a.getAttribute('data-rename');
            const newName = prompt('Rename file', oldName);
            if (!newName || newName === oldName) return;
            const r = await fetch('/api/files/rename', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ old: oldName, new: newName, folder: CURRENT_FOLDER || null }}),
            }});
            if (r.ok) window.location.reload();
            else alert('Rename failed: ' + r.status + ' ' + (await r.text()));
          }});
        }});

        document.querySelectorAll('a[data-move]').forEach(a => {{
          a.addEventListener('click', async (e) => {{
            e.preventDefault();
            const filename = a.getAttribute('data-move');
            const opts = [];
            if (CURRENT_FOLDER) opts.push({{ label: '(root)', value: null }});
            for (const f of FOLDERS) {{
              if (f !== CURRENT_FOLDER) opts.push({{ label: f, value: f }});
            }}
            if (opts.length === 0) {{
              alert('No other folders available. Create one first.');
              return;
            }}
            const lines = opts.map((o, i) => (i + 1) + '. ' + o.label).join('\\n');
            const input = prompt('Move "' + filename + '" to:\\n\\n' + lines + '\\n\\nEnter number:', '1');
            if (input === null) return;
            const n = parseInt(input.trim(), 10);
            if (!n || n < 1 || n > opts.length) {{
              alert('Invalid selection.');
              return;
            }}
            const target = opts[n - 1].value;
            const r = await fetch('/api/files/move', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ filename, from_folder: CURRENT_FOLDER || null, to_folder: target }}),
            }});
            if (r.ok) window.location.reload();
            else alert('Move failed: ' + r.status + ' ' + (await r.text()));
          }});
        }});

        document.querySelectorAll('a[data-play]').forEach(a => {{
          a.addEventListener('click', (e) => {{
            e.preventDefault();
            const name = a.getAttribute('data-play');
            const sel = 'tr.player-row[data-player-for="' + CSS.escape(name) + '"]';
            const row = document.querySelector(sel);
            if (!row) return;
            const audio = row.querySelector('audio');
            if (row.style.display === 'none') {{
              if (!audio.src) audio.src = '/api/files/stream/' + encodeURIComponent(name) + qsFolder();
              row.style.display = 'table-row';
              audio.play().catch(() => {{}});
            }} else {{
              audio.pause();
              row.style.display = 'none';
            }}
          }});
        }});

        // ── Folder actions ──────────────────────────────────────────────
        const newFolderBtn = document.getElementById('new-folder-btn');
        if (newFolderBtn) {{
          newFolderBtn.addEventListener('click', async () => {{
            const name = prompt('Folder name (letters, numbers, dashes; one level only):', '');
            if (!name) return;
            const r = await fetch('/api/folders', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ name: name.trim() }}),
            }});
            if (r.ok) window.location.reload();
            else alert('Create folder failed: ' + r.status + ' ' + (await r.text()));
          }});
        }}

        document.querySelectorAll('a[data-folder-rename]').forEach(a => {{
          a.addEventListener('click', async (e) => {{
            e.preventDefault();
            const oldName = a.getAttribute('data-folder-rename');
            const newName = prompt('Rename folder', oldName);
            if (!newName || newName === oldName) return;
            const r = await fetch('/api/folders/rename', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ old: oldName, new: newName.trim() }}),
            }});
            if (r.ok) window.location.reload();
            else alert('Rename folder failed: ' + r.status + ' ' + (await r.text()));
          }});
        }});

        document.querySelectorAll('a[data-folder-delete]').forEach(a => {{
          a.addEventListener('click', async (e) => {{
            e.preventDefault();
            const name = a.getAttribute('data-folder-delete');
            if (!confirm('Delete folder "' + name + '"?\\n\\nThe folder must be empty.')) return;
            const r = await fetch('/api/folders/' + encodeURIComponent(name), {{ method: 'DELETE' }});
            if (r.ok) window.location.reload();
            else alert('Delete folder failed: ' + r.status + ' ' + (await r.text()));
          }});
        }});
      }})();
      </script>
    """
    return _page("OXP File Drop", body, theme=theme)
