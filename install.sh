#!/usr/bin/env bash
# =============================================================================
# NetWatch Installation Script
# Installs NetWatch as a systemd service on Raspberry Pi OS / Debian Linux
# =============================================================================
set -euo pipefail

# --- Configuration -----------------------------------------------------------
INSTALL_DIR="/opt/netwatch"
SERVICE_USER="netwatch"
SERVICE_GROUP="netwatch"
SERVICE_NAME="netwatch"
PYTHON_MIN_VERSION="3.11"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Colors ------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_section() { echo -e "\n${BLUE}=== $* ===${NC}"; }

die() {
    log_error "$*"
    exit 1
}

# --- Preflight checks --------------------------------------------------------
check_root() {
    log_section "Preflight Checks"
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root. Use: sudo ./install.sh"
    fi
    log_ok "Running as root"
}

check_os() {
    if [[ ! -f /etc/debian_version ]]; then
        die "This script requires Debian/Raspberry Pi OS"
    fi
    local distro
    distro=$(grep '^ID=' /etc/os-release | cut -d= -f2 | tr -d '"')
    log_ok "Detected OS: ${distro}"
}

check_python() {
    local python_cmd=""
    for cmd in python3.12 python3.11 python3; do
        if command -v "$cmd" &>/dev/null; then
            python_cmd="$cmd"
            break
        fi
    done

    if [[ -z "$python_cmd" ]]; then
        die "Python 3.11+ not found. Install with: sudo apt install python3"
    fi

    local version
    version=$("$python_cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if [[ "$major" -lt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -lt 11 ]]; }; then
        die "Python ${PYTHON_MIN_VERSION}+ required, found ${version}"
    fi

    PYTHON_BIN="$python_cmd"
    log_ok "Python ${version} found at $(which "$python_cmd")"
}

check_network_tools() {
    local missing=()
    for tool in ping traceroute; do
        if ! command -v "$tool" &>/dev/null; then
            missing+=("$tool")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_warn "Missing optional tools: ${missing[*]}"
        log_warn "Install with: sudo apt install ${missing[*]}"
    else
        log_ok "Network tools available: ping, traceroute"
    fi

    if command -v mtr &>/dev/null; then
        log_ok "mtr available"
    else
        log_warn "mtr not found — install with: sudo apt install mtr-tiny"
    fi
}

# --- System setup ------------------------------------------------------------
install_system_packages() {
    log_section "Installing System Packages"
    apt-get update -qq

    local packages=(
        python3-venv
        python3-pip
        iputils-ping
        traceroute
        mtr-tiny
        net-tools
        iproute2
        dnsutils
        curl
        ca-certificates
    )

    log_info "Installing: ${packages[*]}"
    apt-get install -y --no-install-recommends "${packages[@]}" \
        2>&1 | grep -E "(installed|upgraded|already)" || true

    log_ok "System packages installed"
}

create_user() {
    log_section "Creating Service User"
    if id "$SERVICE_USER" &>/dev/null; then
        log_ok "User '${SERVICE_USER}' already exists"
    else
        useradd \
            --system \
            --no-create-home \
            --shell /usr/sbin/nologin \
            --comment "NetWatch Monitoring Service" \
            "$SERVICE_USER"
        log_ok "Created system user '${SERVICE_USER}'"
    fi
}

create_directories() {
    log_section "Creating Directory Structure"
    local dirs=(
        "${INSTALL_DIR}"
        "${INSTALL_DIR}/config"
        "${INSTALL_DIR}/logs"
        "${INSTALL_DIR}/data"
        "${INSTALL_DIR}/data/evidence"
        "${INSTALL_DIR}/database"
        "${INSTALL_DIR}/reports"
        "${INSTALL_DIR}/dashboard/templates"
        "${INSTALL_DIR}/dashboard/static/css"
        "${INSTALL_DIR}/dashboard/static/js"
        "${INSTALL_DIR}/src"
        "${INSTALL_DIR}/tests"
    )

    for dir in "${dirs[@]}"; do
        mkdir -p "$dir"
    done

    log_ok "Directories created under ${INSTALL_DIR}"
}

# --- Application setup -------------------------------------------------------
copy_files() {
    log_section "Copying Application Files"

    rsync -av --delete \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='.git' \
        --exclude='venv' \
        --exclude='*.egg-info' \
        "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

    log_ok "Files copied to ${INSTALL_DIR}"
}

create_virtualenv() {
    log_section "Creating Python Virtual Environment"

    if [[ -d "${INSTALL_DIR}/venv" ]]; then
        log_info "Removing existing virtualenv"
        rm -rf "${INSTALL_DIR}/venv"
    fi

    "$PYTHON_BIN" -m venv "${INSTALL_DIR}/venv"
    log_ok "Virtualenv created"

    log_info "Installing Python dependencies..."
    "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip --quiet
    "${INSTALL_DIR}/venv/bin/pip" install \
        --requirement "${INSTALL_DIR}/requirements.txt" \
        --quiet

    log_ok "Python dependencies installed"
}

set_permissions() {
    log_section "Setting Permissions"

    # Ownership: root owns the code, netwatch owns data directories
    chown -R root:root "${INSTALL_DIR}"
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" \
        "${INSTALL_DIR}/logs" \
        "${INSTALL_DIR}/data" \
        "${INSTALL_DIR}/database" \
        "${INSTALL_DIR}/reports"

    # Read/execute for service user on app files
    chmod -R 755 "${INSTALL_DIR}/src"
    chmod -R 755 "${INSTALL_DIR}/venv"

    # Config readable by service user
    chmod 640 "${INSTALL_DIR}/config/config.yaml"
    chown "root:${SERVICE_GROUP}" "${INSTALL_DIR}/config/config.yaml"

    # Scripts executable
    chmod +x "${INSTALL_DIR}/install.sh"
    chmod +x "${INSTALL_DIR}/update.sh"

    # Allow ping for the Python interpreter (cap_net_raw)
    local python_bin="${INSTALL_DIR}/venv/bin/python"
    if command -v setcap &>/dev/null; then
        setcap cap_net_raw+ep "$python_bin" || log_warn "Could not set cap_net_raw on python — ping may require sudo"
    else
        log_warn "setcap not found — installing libcap2-bin"
        apt-get install -y libcap2-bin --quiet
        setcap cap_net_raw+ep "$python_bin" || log_warn "Could not set cap_net_raw"
    fi

    log_ok "Permissions configured"
}

install_service() {
    log_section "Installing systemd Service"

    # Patch WorkingDirectory in service file
    sed -i "s|WorkingDirectory=.*|WorkingDirectory=${INSTALL_DIR}|" "${INSTALL_DIR}/netwatch.service"
    sed -i "s|ExecStart=.*|ExecStart=${INSTALL_DIR}/venv/bin/python -m src.main|" "${INSTALL_DIR}/netwatch.service"
    sed -i "s|ReadWritePaths=.*|ReadWritePaths=${INSTALL_DIR}/logs ${INSTALL_DIR}/data ${INSTALL_DIR}/database ${INSTALL_DIR}/reports|" "${INSTALL_DIR}/netwatch.service"

    cp "${INSTALL_DIR}/netwatch.service" "/etc/systemd/system/${SERVICE_NAME}.service"
    chmod 644 "/etc/systemd/system/${SERVICE_NAME}.service"

    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}.service"

    log_ok "Service '${SERVICE_NAME}' installed and enabled"
}

# --- Logrotate ---------------------------------------------------------------
install_logrotate() {
    log_section "Configuring Log Rotation"

    cat > "/etc/logrotate.d/${SERVICE_NAME}" << EOF
${INSTALL_DIR}/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su ${SERVICE_USER} ${SERVICE_GROUP}
}
EOF

    log_ok "Logrotate configured (/etc/logrotate.d/${SERVICE_NAME})"
}

# --- Final -------------------------------------------------------------------
start_service() {
    log_section "Starting NetWatch Service"

    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        systemctl restart "${SERVICE_NAME}"
        log_ok "Service restarted"
    else
        systemctl start "${SERVICE_NAME}"
        log_ok "Service started"
    fi

    sleep 2

    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        log_ok "Service is running"
    else
        log_error "Service failed to start"
        log_info "Check logs with: sudo journalctl -u ${SERVICE_NAME} -n 50"
        exit 1
    fi
}

print_summary() {
    local dashboard_port
    dashboard_port=$(grep -oP 'port:\s*\K[0-9]+' "${INSTALL_DIR}/config/config.yaml" 2>/dev/null || echo "8080")
    local hostname
    hostname=$(hostname -I | awk '{print $1}')

    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  NetWatch Installation Complete!${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${BLUE}Dashboard:${NC}  http://${hostname}:${dashboard_port}"
    echo -e "  ${BLUE}Service:${NC}    sudo systemctl status ${SERVICE_NAME}"
    echo -e "  ${BLUE}Logs:${NC}       sudo journalctl -u ${SERVICE_NAME} -f"
    echo -e "  ${BLUE}Config:${NC}     ${INSTALL_DIR}/config/config.yaml"
    echo -e "  ${BLUE}Reports:${NC}    ${INSTALL_DIR}/reports/"
    echo ""
    echo -e "  ${YELLOW}To generate a PDF report:${NC}"
    echo -e "  sudo -u ${SERVICE_USER} ${INSTALL_DIR}/venv/bin/python -c \\"
    echo -e "    \"from src.reports import generate_monthly_report; import asyncio; asyncio.run(generate_monthly_report())\""
    echo ""
    echo -e "  ${YELLOW}To stop the service:${NC}"
    echo -e "  sudo systemctl stop ${SERVICE_NAME}"
    echo ""
}

# --- Entry point -------------------------------------------------------------
main() {
    echo -e "${BLUE}"
    echo "  ███╗   ██╗███████╗████████╗██╗    ██╗ █████╗ ████████╗ ██████╗██╗  ██╗"
    echo "  ████╗  ██║██╔════╝╚══██╔══╝██║    ██║██╔══██╗╚══██╔══╝██╔════╝██║  ██║"
    echo "  ██╔██╗ ██║█████╗     ██║   ██║ █╗ ██║███████║   ██║   ██║     ███████║"
    echo "  ██║╚██╗██║██╔══╝     ██║   ██║███╗██║██╔══██║   ██║   ██║     ██╔══██║"
    echo "  ██║ ╚████║███████╗   ██║   ╚███╔███╔╝██║  ██║   ██║   ╚██████╗██║  ██║"
    echo "  ╚═╝  ╚═══╝╚══════╝   ╚═╝    ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝    ╚═════╝╚═╝  ╚═╝"
    echo -e "${NC}"
    echo "  Network Monitoring System — Production Installer"
    echo "  Version 1.0 | github.com/kaldox/netwatch"
    echo ""

    check_root
    check_os
    check_python
    check_network_tools
    install_system_packages
    create_user
    create_directories
    copy_files
    create_virtualenv
    set_permissions
    install_service
    install_logrotate
    start_service
    print_summary
}

main "$@"
