
#!/usr/bin/env bash
set -euo pipefail

# Required env:
#   RH_USER / RH_PASS  (Red Hat registry credentials)
# Optional env:
#   REPO   (defaults to registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9)
#   OUT    (defaults to ./xml-out)

: "${RH_USER:?RH_USER not set}"
: "${RH_PASS:?RH_PASS not set}"
REPO="${REPO:-registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9}"
OUT="${OUT:-./xml-out}"

echo "==> Generating XML inventories from $REPO (all tags) to $OUT"
python3 ee_inventory_to_xml.py   --username "$RH_USER" --password "$RH_PASS"   --registry registry.redhat.io   --repo "$REPO"   --tags all   --out "$OUT"

echo "==> Building HTML diff report from $OUT"
python3 ee_xml_diff_report.py "$OUT" ./ee_diff_report.html

echo "Done. Open ./ee_diff_report.html in your browser."
