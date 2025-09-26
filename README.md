
# EE Package Reporter

Create per-image inventories (RPMs, Python packages, Ansible collections) from **Ansible Execution Environment** images and render an HTML **diff report** across tags.

## What this does

1. Pulls each image (one-by-one) from a repo you specify.
2. Extracts:
   - **RPMs** (`rpm -qa`)
   - **Python packages** (`pip list --format=json` with fallback to `pip freeze`)
   - **Ansible collections** from **three sources**, merged with precedence **galaxy > filesystem > rpm**:
     1) `ansible-galaxy collection list --format json`
     2) Filesystem scan of `ansible_collections` roots (`MANIFEST.json` or `galaxy.yml`)
     3) RPMs named `ansible-collection-*`
3. Writes one **XML** per image into `xml-out/` (by default).
4. Immediately **removes** each image to conserve disk space (override with `--no-rmi`).
5. Builds a **single HTML report** that shows, per tag, the **diff vs the previous tag** for RPMs, Python packages, and Ansible collections.

> Note: `ee-minimal-rhel9` is designed to ship **no Galaxy collections** (only `ansible-core`). Target a richer EE like `ee-ansible-rhel9` or `ee-supported-rhel9` to see collection version churn.

## Requirements

- Python 3.8+
- `podman` on PATH (tested on macOS, Linux)
- `curl` on PATH (used to enumerate tags via Red Hat Pyxis)
- Red Hat registry credentials (for `registry.redhat.io` pull)

## Quick start

```bash
git clone https://github.com/jonnyfiveiq/ee-package-reporter.git
cd ee-package-reporter

# One-command run (uses env vars for credentials)
export RH_USER='you@example.com'
export RH_PASS='••••••••'

# Choose the repo (examples)
export REPO=registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9
# or to see collection churn:
# export REPO=registry.redhat.io/ansible-automation-platform-25/ee-ansible-rhel9

./run_all.sh
# -> XML files in ./xml-out and report at ./ee_diff_report.html
```

## Detailed usage

### Inventory (XML generation)

```bash
python3 ee_inventory_to_xml.py   --username "$RH_USER" --password "$RH_PASS"   --registry registry.redhat.io   --repo registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9   --tags all   --out ./xml-out
```

Options:
- `--tags all` — enumerate **all tags** via the Red Hat Catalog (Pyxis) API.
- `--tags 1.0.0-968,latest` — explicit list.
- `--tags-file ./tags.txt` — one tag or full ref per line.
- `--images <full,comma,separated,refs>` — mix in specific refs (including digests).
- `--no-rmi` — keep images after processing (debug).

### Report

```bash
python3 ee_xml_diff_report.py ./xml-out ./ee_diff_report.html
open ./ee_diff_report.html  # macOS helper
```

The report shows three **labeled rows** (RPMs, Python packages, Ansible collections). The **first column** (earliest image) is empty; each subsequent column shows the **diff vs the previous image**. Columns are widened for readability; scroll horizontally and vertically to see everything.

## Tips

- If `--tags all` returns nothing, you likely used `registry.redhat.io` with Pyxis. The tool automatically queries Pyxis with the expected `registry.access.redhat.com` value for Red Hat–published images.
- For >500 images, adjust the `page_size` or iterate pages (the current repo sizes fit in one call).
- Minimal images like `ee-minimal-rhel9` contain no Galaxy collections; this is expected.

