#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Export RPM/Python/Collections from EE images to XML (and remove images after).

Features:
  - Registry login with --username/--password (password prompt if omitted)
  - '--tags all' discovers every tag via Pyxis by invoking `curl`
  - Robust Ansible Collections detection:
      1) ansible-galaxy collection list --format json
      2) Filesystem scan of ansible_collections roots (reads MANIFEST.json / galaxy.yml)
      3) RPM mapping for ansible-collection-* packages
     Merges the three sources (galaxy > filesystem > rpm) and writes to XML.
"""

import argparse
import getpass
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_REPO = "registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9"
DEFAULT_REGISTRY = "registry.redhat.io"
PYXIS_BASE = "https://catalog.redhat.com/api/containers/v1"

# ----------------------- Shell helpers -----------------------

def run(cmd, check=True, capture=True, input_text=None, env=None):
    kw = {}
    if capture:
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.PIPE
        kw["text"] = True
    if input_text is not None:
        kw["input"] = input_text
        kw["text"] = True
    p = subprocess.run(cmd, **kw, env=env)
    if check and p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, cmd, p.stdout, p.stderr)
    return p

def have(cmd_name: str) -> bool:
    return shutil.which(cmd_name) is not None

# ----------------------- Podman helpers -----------------------

def podman_login(registry, username, password):
    if not (registry and username and password is not None):
        return
    print(f"==> Logging in to {registry} as {username} …")
    try:
        run(["podman", "login", registry, "--username", username, "--password-stdin"],
            input_text=password + "\n")
        print("  ✓ login successful")
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or "").strip()
        print(f"  ! login failed: {msg}", file=sys.stderr)
        sys.exit(1)

def podman_run(image, shell_script):
    return run(["podman", "run", "--rm", image, "bash", "-lc", shell_script]).stdout

def podman_pull(image):
    return run(["podman", "pull", image]).stdout

def podman_rmi(image):
    run(["podman", "rmi", "--force", image], check=False)

def podman_inspect(image):
    p = run(["podman", "image", "inspect", "--format", "{{json .}}", image])
    try:
        return json.loads(p.stdout.strip())
    except Exception:
        return {}

# ----------------------- Misc helpers -----------------------

def sanitize_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)

def image_display_name_and_tag(image_ref: str):
    tag = None
    digest = None
    name = image_ref
    if "@" in image_ref:
        name, digest = image_ref.split("@", 1)
    else:
        last_seg = image_ref.rsplit("/", 1)[-1]
        if ":" in last_seg:
            base, tag = last_seg.split(":", 1)
            name = image_ref[: -(len(last_seg))] + base
    base = name.split("/")[-1]
    if tag:
        stem = f"{base}__{tag}"
    elif digest:
        stem = f"{base}__{sanitize_filename(digest)}"
    else:
        stem = sanitize_filename(image_ref)
    return stem

def parse_rpm_lines(text):
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) != 5:
            continue
        name, epoch, version, release, arch = [p.strip() for p in parts]
        epoch = epoch if epoch and epoch != "(none)" else ""
        items.append({"name": name, "epoch": epoch, "version": version, "release": release, "arch": arch})
    return items

def parse_pip_json(text):
    try:
        arr = json.loads(text)
        items = []
        for o in arr:
            n = o.get("name"); v = o.get("version")
            if n and v:
                items.append({"name": n, "version": v})
        return items
    except Exception:
        pkgs = []
        for line in text.splitlines():
            s = line.strip()
            if "==" in s:
                n, v = s.split("==", 1)
                pkgs.append({"name": n.strip(), "version": v.strip()})
        return pkgs

# ---- New: parse & merge multiple collection JSON sources ----

def _colls_from_obj(obj):
    """
    Normalize various shapes into {name: version} mapping.
    Accepts:
      - {"collections": {"ns.coll": {"version": "x"}}}
      - {"ns.coll": {"version": "x"}, ...}
      - [{"namespace":"ns","name":"coll","version":"x"}, ...]
      - {}
    """
    mapping = {}
    try:
        if isinstance(obj, dict) and "collections" in obj and isinstance(obj["collections"], dict):
            obj = obj["collections"]
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, dict) and "version" in v and "." in k:
                    mapping[k] = str(v["version"])
        elif isinstance(obj, list):
            for e in obj:
                if isinstance(e, dict) and all(x in e for x in ("namespace","name","version")):
                    mapping[f"{e['namespace']}.{e['name']}"] = str(e["version"])
    except Exception:
        pass
    return mapping

def parse_collections_merged(text):
    """
    Expect between ===COLL START=== and ===COLL END=== three JSON blobs separated by ===COLL SEP===:
      [0] ansible-galaxy output (preferred)
      [1] filesystem scan output
      [2] rpm mapping output
    Merge with precedence: galaxy > filesystem > rpm.
    Returns: list of {"name": "...", "version": "..."}
    """
    chunks = [c.strip() for c in text.split("===COLL SEP===")]
    # sanity clip to at most 3
    if len(chunks) > 3:
        chunks = chunks[:3]
    maps = []
    for ch in chunks:
        try:
            obj = json.loads(ch or "{}")
        except Exception:
            obj = {}
        maps.append(_colls_from_obj(obj))

    # Ensure list has 3 entries
    while len(maps) < 3:
        maps.append({})

    rpm_map, fs_map, galaxy_map = {}, {}, {}
    if len(maps) == 3:
        galaxy_map, fs_map, rpm_map = maps[0], maps[1], maps[2]
    elif len(maps) == 2:
        galaxy_map, fs_map = maps[0], maps[1]
    elif len(maps) == 1:
        galaxy_map = maps[0]

    merged = {}
    # lowest precedence first, highest last
    for source in (rpm_map, fs_map, galaxy_map):
        for name, ver in source.items():
            merged[name] = ver

    items = [{"name": n, "version": v} for n, v in merged.items()]
    # sort by name for stable output
    items.sort(key=lambda x: x["name"].lower())
    return items

def make_xml(image_ref, meta, rpms, pips, cols):
    root = ET.Element("image")
    root.set("reference", image_ref)
    created = meta.get("Created") or meta.get("created")
    digest = meta.get("Digest") or meta.get("digest")
    repo_digests = meta.get("RepoDigests") or []
    repo_tags = meta.get("RepoTags") or []
    if created:
        root.set("created", str(created))
    if digest:
        root.set("digest", str(digest))
    if repo_digests:
        rd = ET.SubElement(root, "repoDigests")
        for d in repo_digests:
            ET.SubElement(rd, "digest").text = d
    if repo_tags:
        rt = ET.SubElement(root, "repoTags")
        for t in repo_tags:
            ET.SubElement(rt, "tag").text = t

    rpms_el = ET.SubElement(root, "rpms")
    for r in sorted(rpms, key=lambda x: (x["name"], x["arch"], x["version"], x["release"])):
        el = ET.SubElement(rpms_el, "rpm")
        el.set("name", r["name"])
        if r["epoch"]:
            el.set("epoch", r["epoch"])
        el.set("version", r["version"])
        el.set("release", r["release"])
        el.set("arch", r["arch"])

    py_el = ET.SubElement(root, "python")
    for p in sorted(pips, key=lambda x: x["name"].lower()):
        el = ET.SubElement(py_el, "package")
        el.set("name", p["name"])
        el.set("version", p["version"])

    col_el = ET.SubElement(root, "collections")
    for c in sorted(cols, key=lambda x: x["name"].lower()):
        el = ET.SubElement(col_el, "collection")
        el.set("name", c["name"])
        el.set("version", c["version"])

    return ET.ElementTree(root)

# --------- Inside-container script with 3-source collection detection ---------

def build_inside_script():
    return r"""
set -e
echo "===RPM START==="
rpm -qa --qf '%{NAME}|%{EPOCHNUM}|%{VERSION}|%{RELEASE}|%{ARCH}\n' | sort
echo "===RPM END==="

echo "===PIP START==="
( python3 -m pip list --format=json 2>/dev/null ) || ( python3 -m pip freeze 2>/dev/null || true )
echo "===PIP END==="

echo "===COLL START==="
# 1) ansible-galaxy JSON (preferred)
( ansible-galaxy collection list --format json 2>/dev/null ) || echo "{}"
echo "===COLL SEP==="

# 2) filesystem scan
python3 - <<'PY'
import json, os, glob
roots = [
  "/usr/share/ansible/collections/ansible_collections",
  "/usr/local/share/ansible/collections/ansible_collections",
]
out = {}
for root in roots:
  if not os.path.isdir(root): continue
  for ns in glob.glob(os.path.join(root, "*")):
    if not os.path.isdir(ns): continue
    for coll in glob.glob(os.path.join(ns, "*")):
      if not os.path.isdir(coll): continue
      name = f"{os.path.basename(ns)}.{os.path.basename(coll)}"
      ver = None
      mpath = os.path.join(coll, "MANIFEST.json")
      if os.path.exists(mpath):
        try:
          with open(mpath, "r", encoding="utf-8") as f:
            meta = json.load(f)
          ver = meta.get("collection_info", {}).get("version")
        except Exception:
          pass
      if not ver:
        ypath = os.path.join(coll, "galaxy.yml")
        if os.path.exists(ypath):
          try:
            import yaml  # may not exist
            with open(ypath, "r", encoding="utf-8") as f:
              y = yaml.safe_load(f)
            ver = (y or {}).get("version")
          except Exception:
            pass
      if ver:
        out[name] = {"version": str(ver)}
print(json.dumps({"collections": out}))
PY
echo "===COLL SEP==="

# 3) rpm mapping
python3 - <<'PY'
import json, subprocess
out={}
try:
  q = subprocess.run(["rpm","-qa","--qf","%{NAME}|%{VERSION}\n"], text=True, capture_output=True, check=True)
  for line in q.stdout.splitlines():
    if not line or "|" not in line: continue
    n,v = line.split("|",1)
    if n.startswith("ansible-collection-"):
      parts = n.split("-", 3)
      if len(parts) >= 4:
        fqcn = f"{parts[2]}.{parts[3]}"
        out[fqcn] = {"version": str(v)}
except Exception:
  pass
print(json.dumps({"collections": out}))
PY
echo "===COLL END==="
"""

def split_sections(raw):
    def extract(block, start, end):
        s = raw.split(start, 1)
        if len(s) < 2:
            return ""
        t = s[1].split(end, 1)
        return t[0]
    rpm_txt = extract(raw, "===RPM START===\n", "===RPM END===")
    pip_txt = extract(raw, "===PIP START===\n", "===PIP END===")
    col_txt = extract(raw, "===COLL START===\n", "===COLL END===")
    return rpm_txt.strip(), pip_txt.strip(), col_txt.strip()

# ----------------------- Tag discovery via curl -----------------------

def split_repo_into_registry_and_path(registry_cli: str, repo_cli: str):
    """
    For Pyxis lookups, Red Hat images want 'registry.access.redhat.com' even if you pull
    from 'registry.redhat.io'. Return (pyxis_registry, repo_path_without_registry).
    """
    pyxis_registry = "registry.access.redhat.com" if registry_cli.endswith("redhat.io") else registry_cli
    repo_path = repo_cli
    if repo_path.startswith(registry_cli + "/"):
        repo_path = repo_path[len(registry_cli) + 1 :]
    if repo_path.startswith(pyxis_registry + "/"):
        repo_path = repo_path[len(pyxis_registry) + 1 :]
    return pyxis_registry, repo_path

def get_all_tags_via_curl_pyxis(registry_cli: str, repo_cli: str):
    if not have("curl"):
        raise RuntimeError("curl not found on PATH")
    pyxis_registry, repo_path = split_repo_into_registry_and_path(registry_cli, repo_cli)
    url = (f"{PYXIS_BASE}/repositories/registry/{pyxis_registry}/"
           f"repository/{repo_path}/images?page_size=500")
    resp = run(["curl", "-s", url])
    try:
        data = json.loads(resp.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from Pyxis curl: {e}")
    tags = set()
    for im in (data.get("data") or []):
        for r in (im.get("repositories") or []):
            for t in (r.get("tags") or []):
                name = t.get("name")
                if name:
                    tags.add(name)
    return sorted(tags)

# ----------------------- CLI assembly -----------------------

def iter_image_refs(args):
    refs = []

    if args.images:
        refs.extend([s.strip() for s in args.images.split(",") if s.strip()])

    if args.tags:
        if args.tags.strip().lower() == "all":
            print(f"==> Discovering all tags (via curl/Pyxis) for {args.repo} …")
            try:
                discovered = get_all_tags_via_curl_pyxis(args.registry, args.repo)
            except Exception as e:
                print(f"  ! could not fetch tags via curl/Pyxis: {e}", file=sys.stderr)
                sys.exit(2)
            if not discovered:
                print("  ! no tags returned by Pyxis for this repo.", file=sys.stderr)
                sys.exit(2)
            print(f"  ✓ found {len(discovered)} tags")
            refs.extend([f"{args.repo}:{t}" for t in discovered])
        else:
            for t in [s.strip() for s in args.tags.split(",") if s.strip()]:
                refs.append(f"{args.repo}:{t}")

    if args.tags_file:
        for line in Path(args.tags_file).read_text().splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            if "/" in t or ":" in t or "@" in t:
                refs.append(t)
            else:
                refs.append(f"{args.repo}:{t}")

    # de-dupe preserving order
    seen, uniq = set(), []
    for r in refs:
        if r not in seen:
            uniq.append(r); seen.add(r)
    return uniq

def main():
    ap = argparse.ArgumentParser(description="Export RPM/Python/Collections from EE images to XML (and remove images after).")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="Repository with registry, e.g. registry.redhat.io/namespace/name")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY, help="Registry host to login to (default: %(default)s)")
    ap.add_argument("--username", help="Registry username (if provided, a login will be attempted)")
    ap.add_argument("--password", help="Registry password; if omitted but username is provided, you will be prompted")
    ap.add_argument("--tags", help="Comma-separated list of tags OR 'all' to fetch every tag via curl/Pyxis")
    ap.add_argument("--tags-file", help="File with one tag (or full image ref) per line")
    ap.add_argument("--images", help="Comma-separated full image refs (can include tags or digests)")
    ap.add_argument("--out", default="./xml-out", help="Output directory for XML files (default: %(default)s)")
    ap.add_argument("--no-rmi", action="store_true", help="Do not remove image after processing (debug)")
    args = ap.parse_args()

    if args.username:
        password = args.password if args.password is not None else getpass.getpass(f"Password for {args.username}@{args.registry}: ")
        podman_login(args.registry, args.username, password)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    images = iter_image_refs(args)
    if not images:
        print("No images specified. Use --images and/or --tags/--tags-file (or --tags all).", file=sys.stderr)
        sys.exit(2)

    shell_payload = build_inside_script()

    for image in images:
        print(f"\n==> Processing {image}")
        try:
            podman_pull(image)
        except subprocess.CalledProcessError as e:
            print(f"  ! pull failed: {(e.stderr or e.stdout or '').strip()}", file=sys.stderr)
            continue

        try:
            meta = podman_inspect(image)
        except Exception:
            meta = {}

        try:
            raw = podman_run(image, shell_payload)
        except subprocess.CalledProcessError as e:
            print(f"  ! inventory failed: {(e.stderr or e.stdout or '').strip()}", file=sys.stderr)
            if not args.no_rmi:
                podman_rmi(image)
            continue

        rpm_txt, pip_txt, col_txt = split_sections(raw)

        rpms = parse_rpm_lines(rpm_txt)
        pips = parse_pip_json(pip_txt)
        cols = parse_collections_merged(col_txt)

        tree = make_xml(image, meta if isinstance(meta, dict) else {}, rpms, pips, cols)

        stem = image_display_name_and_tag(image)
        xml_path = outdir / f"{sanitize_filename(stem)}.xml"
        tree.write(xml_path, encoding="utf-8", xml_declaration=True)
        print(f"  ✓ wrote {xml_path}")

        if not args.no_rmi:
            podman_rmi(image)
            print(f"  ✓ removed image {image}")

    print("\nAll done.")

if __name__ == "__main__":
    main()
