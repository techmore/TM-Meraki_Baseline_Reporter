import logging
import os
import shutil
import subprocess

from .common import REPORT_VERSION

log = logging.getLogger(__name__)

def build_html(doc_title: str, body: str) -> str:
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{doc_title}</title>
  <style>
    /* Fonts — no CDN dependency; uses locally-installed copies when available */
    @font-face {{
      font-family: "Inter";
      font-weight: 300 700;
      src: local("Inter"), local("Inter-Regular");
    }}
    @font-face {{
      font-family: "Playfair Display";
      font-weight: 600 700;
      src: local("Playfair Display"), local("PlayfairDisplay-Bold");
    }}
  </style>
  <style>
    /* Named page for cover — zero margins for full bleed */
    @page cover-page {{
      margin: 0;
    }}
    @page {{
      margin: 18mm 12mm;
      background: var(--olive-100);
    }}
    :root {{
      --bg: #eef0e6;
      --ink: #0f172a;
      --muted: #64748b;
      --line: #e2e8f0;
      --accent: #0ea5e9;
      --panel: #f7f8f4;
      --olive-50: #f7f8f4;
      --olive-100: #eef0e6;
      --olive-200: #dde1d0;
      --olive-300: #c4c9b0;
      --olive-400: #a7ae8b;
      --olive-500: #8a9269;
      --olive-600: #6e754b;
      --olive-700: #575d3d;
      --olive-800: #464a34;
      --olive-900: #3b3e2d;
      --olive-950: #1f2117;
      --stone-50: #fafaf9;
      --stone-100: #f5f5f4;
      --stone-200: #e7e5e4;
      --stone-300: #d6d3d1;
      --stone-400: #a8a29e;
      --stone-500: #78716c;
      --stone-600: #57534e;
      --stone-700: #44403c;
      --stone-800: #292524;
      --stone-900: #1c1917;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      font-family: "Inter", system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 12px;
      color: var(--ink);
      background: var(--bg);
    }}

    /* =====================================================
       COVER PAGE — full bleed, own page
       ===================================================== */
    .cover {{
      page: cover-page;
      page-break-after: always;
      height: 297mm;
      background: linear-gradient(
        150deg,
        var(--olive-950) 0%,
        var(--olive-900) 45%,
        var(--olive-800) 100%
      );
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      position: relative;
      overflow: hidden;
    }}
    .cover::before {{
      content: '';
      position: absolute;
      inset: 0;
      background-image: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='%23ffffff' fill-opacity='0.04'%3E%3Ccircle cx='30' cy='30' r='2'/%3E%3C/g%3E%3C/svg%3E");
    }}
    .cover-inner {{
      position: relative;
      z-index: 1;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      height: 100%;
      padding: 80px 60px 60px;
      width: 100%;
      max-width: 700px;
    }}
    .cover-top {{
      text-align: center;
    }}
    .cover-brand {{
      font-size: 11px;
      letter-spacing: 0.3em;
      text-transform: uppercase;
      opacity: 0.65;
      margin-bottom: 32px;
      color: var(--olive-200);
    }}
    .cover-rule {{
      width: 48px;
      height: 2px;
      background: var(--olive-400);
      margin: 0 auto 36px;
    }}
    .cover-title {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 42px;
      font-weight: 700;
      line-height: 1.15;
      color: #fff;
      margin-bottom: 20px;
    }}
    .cover-subtitle {{
      font-size: 18px;
      color: var(--olive-200);
      margin-bottom: 12px;
      opacity: 0.9;
    }}
    .cover-run-ts {{
      font-size: 11px;
      color: var(--olive-200);
      opacity: 0.6;
      margin-bottom: 48px;
      letter-spacing: 0.3px;
    }}
    .cover-meta-row {{
      display: flex;
      justify-content: center;
      gap: 32px;
      margin-bottom: 60px;
    }}
    .cover-meta-item {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
    }}
    .cover-meta-label {{
      font-size: 9px;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      opacity: 0.55;
      color: var(--olive-200);
    }}
    .cover-meta-value {{
      font-size: 16px;
      font-weight: 600;
      color: var(--olive-100);
    }}
    .cover-bottom-rule {{
      width: 100%;
      height: 1px;
      background: var(--olive-700);
      margin-bottom: 18px;
      opacity: 0.5;
    }}
    .cover-bottom-info {{
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .cover-conf {{
      font-size: 10px;
      opacity: 0.5;
      letter-spacing: 0.12em;
      color: var(--olive-300);
      text-transform: uppercase;
    }}
    .cover-ver-date {{
      font-size: 10px;
      opacity: 0.5;
      letter-spacing: 0.08em;
      color: var(--olive-300);
    }}

    /* =====================================================
       TABLE OF CONTENTS PAGE
       ===================================================== */
    .toc-page {{
      page-break-after: always;
      min-height: 241mm;
      padding: 60px 72px;
      display: flex;
      flex-direction: column;
    }}
    .toc-header {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 30px;
      font-weight: 700;
      color: var(--olive-900);
      border-bottom: 2px solid var(--olive-400);
      padding-bottom: 16px;
      margin-bottom: 40px;
    }}
    .toc-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      counter-reset: none;
    }}
    .toc-list > li {{
      display: flex;
      align-items: baseline;
      gap: 14px;
      padding: 11px 0;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
    }}
    .toc-list > li::before {{
      display: none;
    }}
    .toc-num {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 17px;
      font-weight: 700;
      color: var(--olive-400);
      min-width: 28px;
    }}
    .toc-entry {{
      color: var(--ink);
      font-weight: 500;
    }}
    .toc-sub {{
      list-style: none;
      margin: 8px 0 0 48px;
      padding: 0;
    }}
    .toc-sub-item {{
      font-size: 13px;
      color: var(--muted);
      padding: 4px 0;
      border: none;
    }}
    .toc-sub-item a {{
      color: inherit;
      text-decoration: none;
    }}
    .toc-sub-item a:hover {{
      text-decoration: underline;
    }}
    .toc-sub-item::before {{
      display: none;
    }}

    /* =====================================================
       REPORT SECTIONS
       ===================================================== */
    .report-section {{
      padding: 18px 18px 26px;
      max-width: none;
    }}
    /* Executive summary occupies its own full page */
    .exec-full-page {{
      page-break-after: always;
      min-height: 220mm;
    }}
    .exec-purpose-card {{
      border-left: 4px solid var(--olive-500);
    }}
    .building-section {{
      margin: 28px 0 40px;
      border-left: 3px solid var(--olive-400);
      padding-left: 20px;
    }}
    .building-section h3 {{
      margin-top: 0;
    }}
    .traffic-path {{
      background: var(--olive-50);
      border: 1px solid var(--olive-200);
      border-radius: 6px;
      padding: 10px 16px;
      font-size: 13px;
      margin: 8px 0 20px;
    }}
    .path-flow {{
      font-weight: 600;
      color: var(--olive-700);
    }}

    /* =====================================================
       DEVICE CARDS
       ===================================================== */
    .device-card {{
      border: 1px solid var(--line);
      border-radius: 10px;
      margin: 12px 0;
      overflow: hidden;
      background: var(--stone-50);
    }}
    .device-card-header {{
      padding: 8px 14px;
      background: var(--olive-50);
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      font-size: 12px;
    }}
    .serial {{
      font-size: 11px;
      background: var(--stone-200);
      padding: 2px 6px;
      border-radius: 4px;
      font-family: monospace;
      color: var(--stone-700);
    }}
    .device-issues {{
      padding: 8px 14px;
      font-size: 11px;
      color: var(--ink);
      border-bottom: 1px solid var(--line);
    }}
    .device-issues ul {{
      margin: 4px 0 0 0;
      padding-left: 18px;
    }}
    .device-issues li {{
      padding: 1px 0;
      font-size: 11px;
    }}
    .device-ok {{
      padding: 7px 14px;
      font-size: 11px;
      color: #2d6a4f;
      background: #d8f3dc;
    }}
    .util-breakdown {{
      padding: 6px 14px;
      font-size: 11px;
      color: var(--muted);
      border-bottom: 1px solid var(--line);
    }}
    .bottleneck-list {{
      padding: 8px 14px;
      font-size: 11px;
      background: #fff8f0;
      border-top: 1px solid #ffe0b2;
      color: #7c4700;
    }}
    .bottleneck-list ul {{
      margin: 4px 0 0 0;
      padding-left: 18px;
    }}
    .bottleneck-list li {{
      padding: 2px 0;
      font-size: 11px;
      color: #7c4700;
    }}
    .bottleneck-list li::before {{
      color: #e65100;
    }}
    /* AP-under-switch grouped table */
    .ap-under-switch {{
      margin: 6px 0 0 16px;
      border-left: 3px solid var(--line);
      padding-left: 10px;
    }}
    .ap-under-switch table.data.dense th,
    .ap-under-switch table.data.dense td {{
      font-size: 9.5px;
      padding: 3px 6px;
    }}
    @media print {{
      .ap-under-switch table.data.dense th,
      .ap-under-switch table.data.dense td {{
        font-size: 8.5px;
        padding: 2px 5px;
      }}
    }}
    .switch-detail-page {{
      page-break-before: always;
      max-width: none;
    }}
    .switch-detail-kicker {{
      margin-top: -8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .switch-detail-stats {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 18px 0;
    }}
    .switch-detail-stat {{
      border: 1px solid var(--line);
      background: var(--stone-50);
      border-radius: 10px;
      padding: 12px 14px;
    }}
    .switch-detail-stat .label {{
      display: block;
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: 0.16em;
      color: var(--muted);
      margin-bottom: 6px;
      font-weight: 600;
    }}
    .switch-detail-stat .value {{
      display: block;
      font-size: 12px;
      color: var(--ink);
      line-height: 1.45;
      word-break: break-word;
    }}
    .switch-detail-card {{
      border: 1px solid var(--line);
      background: white;
      border-radius: 12px;
      padding: 16px 18px;
      margin: 16px 0 18px;
    }}
    .switch-detail-narrative {{
      margin-bottom: 10px;
      color: var(--ink);
    }}
    .switch-port-summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      font-size: 11px;
      color: var(--muted);
      margin: 2px 0 12px;
    }}
    .switch-port-group {{
      margin-top: 12px;
    }}
    .switch-port-group-title {{
      font-size: 10px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 7px;
      font-weight: 700;
    }}
    .switch-port-group-kind {{
      margin-left: 8px;
      letter-spacing: 0.08em;
      font-weight: 600;
      opacity: 0.7;
    }}
    .switch-port-face {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f8fafc;
      padding: 10px;
    }}
    .switch-port-row {{
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: minmax(0, 1fr);
      gap: 6px;
      margin-top: 6px;
    }}
    .switch-port-row:first-child {{
      margin-top: 0;
    }}
    .switch-port-cell {{
      border-radius: 6px;
      min-height: 40px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-direction: column;
      gap: 2px;
      font-size: 10px;
      font-weight: 700;
      border: 1px solid transparent;
      color: #1f2937;
      padding: 4px 2px;
      text-align: center;
    }}
    .switch-port-num {{
      display: block;
      line-height: 1.05;
    }}
    .switch-port-meta {{
      display: block;
      font-size: 8px;
      font-weight: 600;
      opacity: 0.78;
      line-height: 1.05;
    }}
    .switch-port-cell.ok {{ background: #e5efe5; border-color: #b7d2b7; }}
    .switch-port-cell.uplink {{ background: #dbeafe; border-color: #93c5fd; }}
    .switch-port-cell.poe {{ background: #dcfce7; border-color: #86efac; }}
    .switch-port-cell.warn {{ background: #fef3c7; border-color: #fcd34d; }}
    .switch-port-cell.issue {{ background: #fee2e2; border-color: #fca5a5; }}
    .switch-port-cell.down {{ background: #e5e7eb; border-color: #cbd5e1; color: #6b7280; }}
    .switch-port-cell.sfp-port {{
      border-style: dashed;
      border-width: 2px;
    }}
    .switch-port-cell.speed-mgig {{
      box-shadow: inset 0 0 0 2px rgba(14, 165, 233, 0.22);
    }}
    .switch-port-cell.speed-uplink {{
      box-shadow: inset 0 0 0 2px rgba(234, 88, 12, 0.24);
    }}
    .switch-detail-grid-empty {{
      color: var(--muted);
      font-size: 12px;
      padding: 8px 0 2px;
    }}
    .switch-detail-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 12px;
      font-size: 11px;
      color: var(--muted);
    }}
    .switch-detail-legend span {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }}
    .switch-detail-legend .swatch {{
      width: 10px;
      height: 10px;
      border-radius: 2px;
      display: inline-block;
      border: 1px solid rgba(15, 23, 42, 0.08);
    }}
    .switch-detail-legend .swatch.ok {{ background: #e5efe5; }}
    .switch-detail-legend .swatch.uplink {{ background: #dbeafe; }}
    .switch-detail-legend .swatch.poe {{ background: #dcfce7; }}
    .switch-detail-legend .swatch.warn {{ background: #fef3c7; }}
    .switch-detail-legend .swatch.issue {{ background: #fee2e2; }}
    .switch-detail-legend .swatch.down {{ background: #e5e7eb; }}
    .switch-detail-legend .swatch.speed-mgig {{ background: #dbeafe; box-shadow: inset 0 0 0 2px rgba(14, 165, 233, 0.22); }}
    .switch-detail-legend .swatch.speed-uplink {{ background: #fed7aa; box-shadow: inset 0 0 0 2px rgba(234, 88, 12, 0.24); }}
    .switch-detail-legend .swatch.sfp {{ background: white; border-style: dashed; border-width: 2px; }}
    .switch-detail-table td {{
      vertical-align: top;
    }}
    .wan-capacity-chart {{
      margin: 16px 0 22px;
      display: grid;
      gap: 10px;
    }}
    .wan-capacity-row {{
      display: grid;
      grid-template-columns: minmax(200px, 280px) 1fr minmax(180px, 240px);
      gap: 12px;
      align-items: center;
      font-size: 11px;
    }}
    .wan-capacity-label {{
      color: var(--ink);
      font-weight: 600;
    }}
    .wan-capacity-meta {{
      color: var(--muted);
      text-align: right;
    }}
    .wan-capacity-bar {{
      height: 12px;
      background: #e5e7eb;
      border-radius: 999px;
      overflow: hidden;
    }}
    .wan-capacity-bar span {{
      display: block;
      height: 100%;
      background: linear-gradient(90deg, #94a3b8, #0ea5e9);
      border-radius: 999px;
    }}

    /* =====================================================
       BADGES
       ===================================================== */
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.02em;
      white-space: nowrap;
    }}
    .badge-ok {{
      background: #d1fae5;
      color: #065f46;
    }}
    .badge-fail {{
      background: #fee2e2;
      color: #991b1b;
    }}
    .badge-warn {{
      background: #fef3c7;
      color: #92400e;
    }}
    .badge-info {{
      background: #dbeafe;
      color: #1e40af;
    }}

    /* =====================================================
       HEALTH AT A GLANCE GRID
       ===================================================== */
    .health-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin: 16px 0 20px;
    }}
    .health-card {{
      border: 1px solid var(--line);
      background: var(--stone-50);
      border-radius: 10px;
      padding: 14px 14px 12px;
      position: relative;
      overflow: hidden;
    }}
    .health-card::before {{
      content: '';
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 3px;
    }}
    .health-card--good::before  {{ background: #22c55e; }}
    .health-card--warn::before  {{ background: #f59e0b; }}
    .health-card--crit::before  {{ background: #ef4444; }}
    .health-card--info::before  {{ background: var(--olive-400); }}
    .health-card-header {{
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 6px;
    }}
    .health-card-icon {{
      font-size: 16px;
      line-height: 1;
    }}
    .health-card-domain {{
      font-size: 8.5px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 600;
    }}
    .health-card-stat {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 20px;
      font-weight: 700;
      color: var(--ink);
      line-height: 1.1;
    }}
    .health-card--crit .health-card-stat  {{ color: #dc2626; }}
    .health-card--warn .health-card-stat  {{ color: #b45309; }}
    .health-card--good .health-card-stat  {{ color: #15803d; }}
    .health-card-detail {{
      font-size: 9px;
      color: var(--muted);
      margin-top: 3px;
    }}
    @media print {{
      .health-grid {{ grid-template-columns: repeat(4, 1fr); }}
    }}

    /* =====================================================
       KPI ROW
       ===================================================== */
    .kpi-row {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 10px;
      margin: 16px 0 24px;
    }}
    .kpi {{
      border: 1px solid var(--line);
      background: var(--stone-50);
      padding: 14px 12px;
      border-radius: 10px;
      text-align: center;
      position: relative;
      overflow: hidden;
    }}
    .kpi::before {{
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 3px;
      background: var(--olive-400);
    }}
    .kpi-label {{
      font-size: 8px;
      letter-spacing: 0.15em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 4px;
      font-weight: 600;
    }}
    .kpi-value {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 18px;
      font-weight: 700;
      color: var(--ink);
      display: block;
    }}

    /* =====================================================
       SUMMARY CARDS
       ===================================================== */
    .summary-card {{
      background: var(--stone-50);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px 24px;
      margin: 16px 0 24px;
      position: relative;
      overflow: hidden;
    }}
    .summary-card::before {{
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      height: 4px;
      width: 100%;
      background: linear-gradient(90deg, var(--olive-500), var(--olive-300));
    }}
    .summary-title {{
      font-size: 9px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
      font-weight: 600;
    }}
    .summary-body {{
      font-size: 12px;
      line-height: 1.6;
      color: var(--ink);
    }}

    /* =====================================================
       SECURITY CHECKS
       ===================================================== */
    .check-pass {{
      background-color: #28a745;
      color: white;
      padding: 2px 7px;
      border-radius: 3px;
      font-size: 11px;
      font-weight: 600;
    }}
    .check-fail {{
      background-color: #dc3545;
      color: white;
      padding: 2px 7px;
      border-radius: 3px;
      font-size: 11px;
      font-weight: 600;
    }}
    .check-warning {{
      background-color: #ffc107;
      color: #212529;
      padding: 2px 7px;
      border-radius: 3px;
      font-size: 11px;
      font-weight: 600;
    }}
    .check-unknown {{
      background-color: #6c757d;
      color: white;
      padding: 2px 7px;
      border-radius: 3px;
      font-size: 11px;
      font-weight: 600;
    }}

    /* Inline text risk coloring */
    .text-crit {{ color: #dc3545; font-weight: 600; }}
    .text-warn  {{ color: #d97706; font-weight: 600; }}
    .text-good  {{ color: #28a745; font-weight: 600; }}
    /* Backup schema compatibility warning */
    .schema-warning-banner {{
      background: #fff8e1;
      border-left: 4px solid #f59e0b;
      color: #78350f;
      padding: 10px 16px;
      font-size: 12px;
      margin: 0 0 16px;
    }}
    @media print {{ .schema-warning-banner {{ display: none; }} }}

    /* =====================================================
       CHARTS
       ===================================================== */
    .chart-grid {{
      display: grid;
      gap: 20px;
      margin: 20px 0 28px;
    }}
    .chart {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 18px;
      background: var(--stone-50);
      position: relative;
      overflow: hidden;
    }}
    .chart::before {{
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      width: 4px;
      height: 100%;
      background: var(--olive-500);
    }}
    .chart-title {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--muted);
      margin-bottom: 12px;
      font-weight: 600;
    }}
    .pie-chart {{
      position: relative;
      width: 100%;
      height: 200px;
      margin: 16px 0;
    }}
    .pie-slice {{
      position: absolute;
      width: 100%;
      height: 100%;
      border-radius: 50%;
      clip: rect(0px, 200px, 200px, 100px);
    }}
    .pie-slice::before {{
      content: '';
      position: absolute;
      width: 100%;
      height: 100%;
      border-radius: 50%;
      background: var(--olive-500);
      transform: rotate(var(--angle-start));
      transform-origin: center;
    }}
    .pie-label {{
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      text-align: center;
      font-size: 12px;
      color: var(--ink);
      font-weight: 500;
      line-height: 1.4;
    }}
    .line-chart {{
      position: relative;
      width: 100%;
      height: 200px;
      margin: 16px 0;
    }}
    .line-chart svg {{
      width: 100%;
      height: 100%;
    }}
    .chart-points {{
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
    }}
    .chart-point {{
      position: absolute;
      background: var(--olive-500);
      width: 8px;
      height: 8px;
      border-radius: 50%;
      transform: translate(-50%, -50%);
    }}
    .chart-point span {{
      display: block;
      margin-top: 4px;
      font-size: 10px;
      color: var(--muted);
    }}
    .bar-row {{
      display: grid;
      grid-template-columns: 1fr 2.5fr 0.6fr;
      gap: 10px;
      align-items: center;
      margin: 8px 0;
      font-size: 12px;
    }}
    .bar-label {{
      color: var(--ink);
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .bar-track {{
      background: var(--stone-200);
      border-radius: 8px;
      height: 10px;
      position: relative;
      overflow: hidden;
    }}
    .bar-fill {{
      background: linear-gradient(90deg, var(--olive-500), var(--olive-300));
      height: 10px;
      border-radius: 8px;
    }}
    .bar-value {{
      text-align: right;
      color: var(--muted);
      font-weight: 500;
    }}

    /* =====================================================
       TYPOGRAPHY & TABLES
       ===================================================== */
    h1 {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 26px;
      margin: 0 0 4px;
      color: var(--olive-900);
    }}
    h2 {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 22px;
      margin: 24px 0 12px;
      border-bottom: 2px solid var(--olive-300);
      padding-bottom: 8px;
      color: var(--olive-900);
    }}
    h3 {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 15px;
      margin: 18px 0 8px;
      color: var(--olive-800);
    }}
    h4 {{
      font-size: 11px;
      font-weight: 600;
      margin: 14px 0 6px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .empty-state {{
      color: #a8a29e;
      font-style: italic;
      text-align: center;
      padding: 12px 8px;
    }}
    p {{
      margin: 8px 0;
      line-height: 1.55;
      font-size: 12px;
      color: var(--ink);
    }}
    ul {{
      margin: 8px 0 12px 18px;
      padding: 0;
    }}
    ol {{
      margin: 8px 0 12px 18px;
      padding: 0;
    }}
    li {{
      margin: 4px 0;
      line-height: 1.5;
      font-size: 12px;
      color: var(--ink);
      padding-left: 2px;
    }}
    li::before {{
      color: var(--olive-600);
      font-weight: bold;
    }}
    .spacer {{
      height: 8px;
    }}
    table.data {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      margin: 10px 0 18px;
      font-size: 11px;
      background: var(--stone-50);
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--line);
    }}
    table.data th {{
      background: var(--olive-50);
      font-weight: 600;
      color: var(--olive-800);
      text-align: left;
      padding: 8px 12px;
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      border-bottom: 1px solid var(--line);
    }}
    table.data td {{
      padding: 7px 12px;
      vertical-align: top;
      color: var(--ink);
      border-bottom: 1px solid var(--line);
    }}
    table.data tr:last-child td {{
      border-bottom: none;
    }}
    table.data tr:hover td {{
      background: var(--olive-50);
    }}
    /* Dense variant — used in AP interference and other high-row sections */
    table.data.dense th {{
      padding: 5px 8px;
      font-size: 8px;
    }}
    table.data.dense td {{
      padding: 4px 8px;
      font-size: 10px;
    }}
    /* AP interference section: reduce left margin so wide tables fit in PDF */
    #ap-interference {{
      padding-left: 0;
    }}
    @media print {{
      #ap-interference {{
        margin-left: -8px;
        margin-right: -8px;
      }}
      #ap-interference table.data.dense {{
        font-size: 8.5px;
      }}
      #ap-interference table.data.dense th,
      #ap-interference table.data.dense td {{
        padding: 3px 6px;
      }}
    }}
    /* =====================================================
       NETWORK TOPOLOGY
       ===================================================== */
    .topo-site {{
      margin: 24px 0 36px;
    }}
    .topo-site h3 {{
      margin-bottom: 10px;
    }}
    .topo-no-lldp {{
      margin: 0 0 10px;
      padding: 8px 14px;
      background: #fffbeb;
      border: 1px solid #fde68a;
      border-radius: 6px;
      font-size: 11px;
      color: #92400e;
    }}
    .topo-diagram {{
      overflow-x: auto;
      margin: 0 0 8px;
    }}
    .topo-diagram svg {{
      display: block;
      max-width: 100%;
      height: auto;
    }}
    .topo-branch-title {{
      font-size: 13px;
      font-weight: 600;
      color: #57534e;
      margin: 12px 0 4px 0;
    }}
    .topo-branch-title:empty {{
      display: none;
    }}
    .topo-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      margin: 12px 0 20px;
      padding: 10px 16px;
      background: var(--stone-50);
      border: 1px solid var(--line);
      border-radius: 8px;
      font-size: 11px;
      color: var(--muted);
    }}
    .topo-legend-item {{
      display: flex;
      align-items: center;
      gap: 4px;
      white-space: nowrap;
    }}

    @media print {{
      body {{
        background: var(--bg);
        color: black;
      }}
    }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def write_pdf(html_path: str, pdf_path: str) -> bool:
    # Try weasyprint first
    try:
        import weasyprint  # type: ignore

        log.info("Using WeasyPrint for PDF generation: %s", pdf_path)
        weasyprint.HTML(filename=html_path).write_pdf(pdf_path)
        return True
    except Exception as e:
        log.warning("WeasyPrint failed: %s", e)
    # Fallback to wkhtmltopdf
    wk = shutil.which("wkhtmltopdf")
    if wk:
        log.info("Using wkhtmltopdf for PDF generation: %s", pdf_path)
        result = subprocess.run(
            [wk, html_path, pdf_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log.warning("wkhtmltopdf exited %d: %s", result.returncode, result.stderr.strip())
        return os.path.exists(pdf_path)
    log.warning("No PDF generator available (install weasyprint or wkhtmltopdf)")
    return False
