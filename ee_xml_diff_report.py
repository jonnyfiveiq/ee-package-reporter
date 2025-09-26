#!/usr/bin/env python3
import os, re, sys, xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from html import escape

def parse_image_xml(path: Path):
    tree = ET.parse(path)
    root = tree.getroot()
    ref = root.attrib.get("reference","")
    created = root.attrib.get("created","")
    tag = None
    if ":" in ref.rsplit("/",1)[-1]:
        tag = ref.rsplit("/",1)[-1].split(":")[1]
    if not tag:
        for t in root.findall("./repoTags/tag"):
            txt = (t.text or "").strip()
            if ":" in txt:
                tag = txt.split(":")[-1]
                break
    if not tag:
        m = re.search(r"__([^./]+)\.xml$", str(path))
        if m:
            tag = m.group(1)
    created_dt = None
    if created:
        created_trim = created.split("Z")[0].split(".")[0]
        try:
            created_dt = datetime.fromisoformat(created_trim)
        except Exception:
            created_dt = None
    rpms = [ {"name": r.attrib.get("name",""), "epoch": r.attrib.get("epoch","") or "",
              "version": r.attrib.get("version",""), "release": r.attrib.get("release",""),
              "arch": r.attrib.get("arch","")} for r in root.findall("./rpms/rpm") ]
    pips = [ {"name": p.attrib.get("name",""), "version": p.attrib.get("version","")}
             for p in root.findall("./python/package") ]
    cols = [ {"name": c.attrib.get("name",""), "version": c.attrib.get("version","")}
             for c in root.findall("./collections/collection") ]
    return {"path": path, "tag": tag or path.stem, "created": created_dt or datetime.min,
            "rpms": rpms, "pips": pips, "cols": cols}

def index_by(items, keyfunc):
    d={}
    for it in items:
        d[keyfunc(it)] = it
    return d

def rpm_key_name_arch(r):
    return (r["name"], r["arch"])

def rpm_version_str(r):
    evr = f"{r['version']}-{r['release']}"
    if r.get("epoch"):
        return f"{r['epoch']}:{evr}"
    return evr

def diff_rpms(old_list, new_list):
    old = index_by(old_list, rpm_key_name_arch)
    new = index_by(new_list, rpm_key_name_arch)
    added, removed, upgraded, downgraded = [], [], [], []
    for k, nv in new.items():
        if k not in old:
            added.append(f"+ {nv['name']}[{nv['arch']}] {rpm_version_str(nv)}")
        else:
            ov = old[k]
            o = rpm_version_str(ov); n = rpm_version_str(nv)
            if o != n:
                if o < n:
                    upgraded.append(f"↑ {nv['name']}[{nv['arch']}] {o} → {n}")
                else:
                    downgraded.append(f"↓ {nv['name']}[{nv['arch']}] {o} → {n}")
    for k, ov in old.items():
        if k not in new:
            removed.append(f"− {ov['name']}[{ov['arch']}] {rpm_version_str(ov)}")
    return added, removed, upgraded, downgraded

def diff_simple_pkgs(old_list, new_list, name_key="name", ver_key="version"):
    def key(x): return x[name_key].lower()
    old = index_by(old_list, key)
    new = index_by(new_list, key)
    added, removed, upgraded, downgraded = [], [], [], []
    for k, nv in new.items():
        if k not in old:
            added.append(f"+ {nv[name_key]} {nv.get(ver_key,'')}")
        else:
            ov = old[k]
            o = ov.get(ver_key,""); n = nv.get(ver_key,"")
            if o != n:
                if o < n:
                    upgraded.append(f"↑ {nv[name_key]} {o} → {n}")
                else:
                    downgraded.append(f"↓ {nv[name_key]} {o} → {n}")
    for k, ov in old.items():
        if k not in new:
            removed.append(f"− {ov[name_key]} {ov.get(ver_key,'')}")
    return added, removed, upgraded, downgraded

def cell_html(diff_tuple, show_limit=30):
    added, removed, upgraded, downgraded = diff_tuple
    total = len(added)+len(removed)+len(upgraded)+len(downgraded)
    if total == 0:
        return "<div class='empty'>No changes</div>"
    lines = []
    lines.append(f"<div class='counts'>+{len(added)} / ↑{len(upgraded)} / ↓{len(downgraded)} / −{len(removed)}</div>")
    sample = added[:show_limit//2] + upgraded[:show_limit//2]
    if sample:
        lines.append("<ul>" + "".join(f"<li>{escape(s)}</li>" for s in sample) + "</ul>")
    remaining = total - len(sample)
    if remaining > 0:
        def to_list(title, entries):
            if not entries: return ""
            return f"<h5>{title}</h5><ul>" + "".join(f"<li>{escape(s)}</li>" for s in entries) + "</ul>"
        details_html = "".join([
            to_list("Added", added),
            to_list("Upgraded", upgraded),
            to_list("Downgraded", downgraded),
            to_list("Removed", removed),
        ])
        lines.append(f"<details><summary>Show all ({total})</summary>{details_html}</details>")
    return "".join(lines)

def build_report(entries):
    entries = sorted(entries, key=lambda e: (e['created'], e['tag']))
    tags = [e['tag'] for e in entries]
    rpm_cells=[]; pip_cells=[]; col_cells=[]
    for idx, e in enumerate(entries):
        if idx==0:
            rpm_cells.append(""); pip_cells.append(""); col_cells.append("")
        else:
            prev = entries[idx-1]
            rpm_cells.append(cell_html(diff_rpms(prev['rpms'], e['rpms'])))
            pip_cells.append(cell_html(diff_simple_pkgs(prev['pips'], e['pips'])))
            col_cells.append(cell_html(diff_simple_pkgs(prev['cols'], e['cols'])))
    css = """
    <style>
    :root { --label-col-width: 200px; --col-min-width: 440px; }
    body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif; padding: 16px; margin: 0; }
    .table-wrap { width: 100%; overflow-x: auto; }
    table { border-collapse: separate; border-spacing: 0; width: max-content; min-width: 100%; }
    th, td { border: 1px solid #ddd; vertical-align: top; padding: 10px; word-break: break-word; overflow-wrap: anywhere; }
    thead th { background: #fafafa; position: sticky; top: 0; z-index: 2; }
    .rowlbl-hdr, .rowlbl { width: var(--label-col-width); min-width: var(--label-col-width); max-width: var(--label-col-width); background: #fff; position: sticky; left: 0; z-index: 3; }
    .rowlbl { font-weight: 700; }
    .taghdr, td.datacell { min-width: var(--col-min-width); }
    td .counts { font-weight: 600; margin-bottom: 6px; }
    td ul { margin: 4px 0 8px 16px; padding: 0; }
    td h5 { margin: 6px 0 4px; font-size: 12px; color: #555; }
    details { margin-top: 6px; }
    .empty { color: #666; font-style: italic; }
    </style>
    """
    html = ["<html><head><meta charset='utf-8'>", css, "</head><body>"]
    html.append("<h1>EE Image Package Diffs</h1>")
    html.append("<p>Columns are widened for readability (min width ~440px). Scroll horizontally to view all tags and vertically for full details.</p>")
    html.append("<div class='table-wrap'>")
    html.append("<table>")
    html.append("<thead><tr><th class='rowlbl-hdr'>Type</th>" + "".join(f"<th class='taghdr'>{escape(t)}</th>" for t in tags) + "</tr></thead>")
    html.append("<tbody>")
    html.append("<tr><th class='rowlbl'>RPMs</th>" + "".join(f"<td class='datacell'>{c}</td>" for c in rpm_cells) + "</tr>")
    html.append("<tr><th class='rowlbl'>Python Packages</th>" + "".join(f"<td class='datacell'>{c}</td>" for c in pip_cells) + "</tr>")
    html.append("<tr><th class='rowlbl'>Ansible Collections</th>" + "".join(f"<td class='datacell'>{c}</td>" for c in col_cells) + "</tr>")
    html.append("</tbody></table></div>")
    html.append("</body></html>")
    return "".join(html)

def main():
    in_dir = Path(sys.argv[1]) if len(sys.argv)>1 else Path("./xml-out")
    if not in_dir.exists():
        print(f"Input directory not found: {in_dir}", file=sys.stderr); sys.exit(2)
    files = sorted(in_dir.glob("*.xml"))
    if not files:
        print(f"No XML files found in {in_dir}", file=sys.stderr); sys.exit(2)
    entries = [parse_image_xml(p) for p in files]
    html = build_report(entries)
    out = Path(sys.argv[2]) if len(sys.argv)>2 else Path("./ee_diff_report.html")
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out}")

if __name__ == "__main__":
    main()
