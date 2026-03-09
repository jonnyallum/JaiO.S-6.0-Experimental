#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════
# Jai.OS 6.0 — GCP VM Bootstrap
# VM: 35.230.148.83 (e2-medium, 4GB RAM, europe-west2)
# Run: bash scripts/setup_vm.sh
# ════════════════════════════════════════════════════════════════════════════
set -e

VM_IP="35.230.148.83"
VM_USER="antigravity-ai"
SSH_KEY="execution/antigravity_vm_key"
REPO="https://github.com/jonnyallum/JaiO.S-6.0-Experimental.git"
INSTALL_DIR="/opt/antigravity/JaiO.S-6.0-Experimental"

echo "=== Jai.OS 6.0 — GCP VM Setup ==="
echo "Target: ${VM_USER}@${VM_IP}"
echo ""

# ── Check SSH key exists ───────────────────────────────────────────────────────
if [ ! -f "${SSH_KEY}" ]; then
    echo "ERROR: SSH key not found at ${SSH_KEY}"
    echo "Place your GCP VM private key at: ${SSH_KEY}"
    exit 1
fi
chmod 600 "${SSH_KEY}"

# ── Bootstrap VM ───────────────────────────────────────────────────────────────
echo "--- Step 1: System dependencies ---"
ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" bash << 'REMOTE'
set -e

# System packages
sudo apt-get update -y -q
sudo apt-get install -y -q python3.11 python3.11-venv python3-pip git htop sysstat curl

# Create install dir
sudo mkdir -p /opt/antigravity
sudo chown -R $(whoami):$(whoami) /opt/antigravity

echo "System packages installed."
REMOTE

echo ""
echo "--- Step 2: Clone / update repo ---"
ssh -i "${SSH_KEY}" "${VM_USER}@${VM_IP}" bash << REMOTE
set -e
if [ -d "${INSTALL_DIR}" ]; then
    echo "Repo exists — pulling latest..."
    cd "${INSTALL_DIR}" && git pull origin main
else
    echo "Cloning repo..."
    git clone ${REPO} ${INSTALL_DIR}
fi
REMOTE

echo ""
echo "--- Step 3: Python virtual environment ---"
ssh -i "${SSH_KEY}" "${VM_USER}@${VM_IP}" bash << REMOTE
set -e
cd "${INSTALL_DIR}"
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "Dependencies installed."
REMOTE

echo ""
echo "--- Step 4: Verification ---"
ssh -i "${SSH_KEY}" "${VM_USER}@${VM_IP}" bash << REMOTE
set -e
cd "${INSTALL_DIR}"
source venv/bin/activate

python -c "import langgraph; print(f'langgraph {langgraph.__version__} \u2713')"
python -c "import anthropic; print(f'anthropic \u2713')"
python -c "import github; print(f'PyGithub \u2713')"
python -c "import supabase; print(f'supabase \u2713')"
python -c "import structlog; print(f'structlog \u2713')"
python -c "import tenacity; print(f'tenacity \u2713')"
python -c "import httpx; print(f'httpx \u2713')"
echo ""
echo "All dependencies verified."
REMOTE

echo ""
echo "=== VM setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Push .env to VM:"
echo "     scp -i ${SSH_KEY} .env ${VM_USER}@${VM_IP}:${INSTALL_DIR}/.env"
echo ""
echo "  2. Create Supabase schema:"
echo "     Run scripts/create_schema.sql in Supabase SQL Editor"
echo "     (lkwydqtfbdjhxaarelaz → SQL Editor → paste → Run)"
echo ""
echo "  3. Run Phase 1 test:"
echo "     ssh -i ${SSH_KEY} ${VM_USER}@${VM_IP}"
echo "     cd ${INSTALL_DIR} && source venv/bin/activate"
echo "     python graphs/test_graph.py"
echo ""
echo "  4. Run error handling tests:"
echo "     python tests/test_error_handling.py"
