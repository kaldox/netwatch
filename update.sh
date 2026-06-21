#!/usr/bin/env bash
# =============================================================================
# NetWatch Update Script
# Updates NetWatch in-place with minimal downtime
# =============================================================================
set -euo pipefail

INSTALL_DIR="/opt/netwatch"
SERVICE_NAME="netwatch"
BACKUP_DIR="/opt/netwatch-backups"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_section() { echo -e "\n${BLUE}=== $* ===${NC}"; }

die() {
    log_error "$*"
    exit 1
}

# --- Preflight ---------------------------------------------------------------
check_root() {
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root. Use: sudo ./update.sh"
    fi
}

check_install() {
    if [[ ! -d "${INSTALL_DIR}" ]]; then
        die "NetWatch not installed at ${INSTALL_DIR}. Run install.sh first."
    fi
}

# --- Backup ------------------------------------------------------------------
backup_current() {
    log_section "Backing Up Current Installation"
    mkdir -p "${BACKUP_DIR}"

    local backup_path="${BACKUP_DIR}/netwatch_${TIMESTAMP}"
    mkdir -p "${backup_path}"

    # Backup code (not data/logs/db — those are precious and large)
    rsync -a \
        --exclude='venv' \
        --exclude='logs' \
        --exclude='data' \
        --exclude='database' \
        --exclude='reports' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "${INSTALL_DIR}/" "${backup_path}/"

    log_ok "Backup created: ${backup_path}"

    # Keep only last 5 backups
    local backup_count
    backup_count=$(find "${BACKUP_DIR}" -maxdepth 1 -type d -name 'netwatch_*' | wc -l)
    if [[ "$backup_count" -gt 5 ]]; then
        log_info "Pruning old backups (keeping 5 most recent)"
        find "${BACKUP_DIR}" -maxdepth 1 -type d -name 'netwatch_*' \
            | sort | head -n -5 | xargs rm -rf
    fi
}

# --- Update ------------------------------------------------------------------
stop_service() {
    log_section "Stopping Service"
    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        systemctl stop "${SERVICE_NAME}"
        log_ok "Service stopped"
    else
        log_info "Service was not running"
    fi
}

update_files() {
    log_section "Updating Application Files"

    rsync -av --delete \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='.git' \
        --exclude='venv' \
        --exclude='*.egg-info' \
        --exclude='logs' \
        --exclude='data' \
        --exclude='database' \
        --exclude='reports' \
        "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

    log_ok "Application files updated"
}

update_config() {
    log_section "Checking Configuration"

    local current_config="${INSTALL_DIR}/config/config.yaml"
    local new_config="${SCRIPT_DIR}/config/config.yaml"

    if [[ -f "${current_config}" ]]; then
        if ! diff -q "${current_config}" "${new_config}" &>/dev/null; then
            log_warn "config.yaml has changed."
            log_warn "Your current config is preserved at: ${current_config}"
            log_warn "New default config available at: ${new_config}.new"
            cp "${new_config}" "${current_config}.new"
        else
            log_ok "Config unchanged"
        fi
    else
        cp "${new_config}" "${current_config}"
        log_ok "Config installed"
    fi
}

update_dependencies() {
    log_section "Updating Python Dependencies"

    "${INSTALL_DIR}/venv/bin/pip" install \
        --upgrade \
        --requirement "${INSTALL_DIR}/requirements.txt" \
        --quiet

    log_ok "Dependencies updated"
}

update_service() {
    log_section "Updating systemd Service"

    local new_service="${INSTALL_DIR}/netwatch.service"
    local installed_service="/etc/systemd/system/${SERVICE_NAME}.service"

    if ! diff -q "${new_service}" "${installed_service}" &>/dev/null 2>&1; then
        log_info "Updating service file"
        cp "${new_service}" "${installed_service}"
        chmod 644 "${installed_service}"
        systemctl daemon-reload
        log_ok "Service file updated and daemon reloaded"
    else
        log_ok "Service file unchanged"
    fi
}

update_permissions() {
    log_section "Updating Permissions"
    local service_user="netwatch"
    local service_group="netwatch"

    chown -R root:root "${INSTALL_DIR}"
    chown -R "${service_user}:${service_group}" \
        "${INSTALL_DIR}/logs" \
        "${INSTALL_DIR}/data" \
        "${INSTALL_DIR}/database" \
        "${INSTALL_DIR}/reports"

    chmod -R 755 "${INSTALL_DIR}/src"
    chmod 640 "${INSTALL_DIR}/config/config.yaml"
    chown "root:${service_group}" "${INSTALL_DIR}/config/config.yaml"
    chmod +x "${INSTALL_DIR}/install.sh"
    chmod +x "${INSTALL_DIR}/update.sh"

    local python_bin="${INSTALL_DIR}/venv/bin/python"
    if command -v setcap &>/dev/null; then
        setcap cap_net_raw+ep "$python_bin" 2>/dev/null || true
    fi

    log_ok "Permissions updated"
}

start_service() {
    log_section "Starting Service"
    systemctl start "${SERVICE_NAME}"

    sleep 2

    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        log_ok "Service is running"
    else
        log_error "Service failed to start after update"
        log_info "Showing last 20 log lines:"
        journalctl -u "${SERVICE_NAME}" -n 20 --no-pager
        echo ""
        log_warn "To roll back, restore from backup:"
        log_warn "  rsync -a ${BACKUP_DIR}/netwatch_${TIMESTAMP}/ ${INSTALL_DIR}/"
        log_warn "  systemctl start ${SERVICE_NAME}"
        exit 1
    fi
}

run_tests() {
    log_section "Running Tests"

    if ! "${INSTALL_DIR}/venv/bin/python" -m pytest \
        "${INSTALL_DIR}/tests/" \
        -q \
        --tb=short \
        2>&1; then
        log_warn "Some tests failed — see output above"
        log_warn "The service has been started anyway. Verify manually."
    else
        log_ok "All tests passed"
    fi
}

print_summary() {
    local version
    version=$(cd "${INSTALL_DIR}" && git describe --tags --always 2>/dev/null || echo "unknown")

    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  NetWatch Update Complete!${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${BLUE}Version:${NC}    ${version}"
    echo -e "  ${BLUE}Backup:${NC}     ${BACKUP_DIR}/netwatch_${TIMESTAMP}"
    echo -e "  ${BLUE}Status:${NC}     sudo systemctl status ${SERVICE_NAME}"
    echo -e "  ${BLUE}Logs:${NC}       sudo journalctl -u ${SERVICE_NAME} -f"
    echo ""
}

# --- Entry point -------------------------------------------------------------
main() {
    echo -e "${BLUE}NetWatch Update Script — $(date)${NC}"
    echo ""

    check_root
    check_install
    backup_current
    stop_service
    update_files
    update_config
    update_dependencies
    update_service
    update_permissions
    run_tests
    start_service
    print_summary
}

main "$@"
