#!/usr/bin/env bash
# Clean AI Agent Profile & Pre-flight IP Guard for Orca / Claude Code / Codex
#
# Usage:
#   ./scripts/orca-clean-agent.sh check                # Verify exit IP is non-RU and ISP/clean
#   ./scripts/orca-clean-agent.sh purge-legacy         # Backup & remove banned ~/.claude identifiers + Keychain
#   ./scripts/orca-clean-agent.sh init <profile-name>  # Create isolated CLAUDE_CONFIG_DIR with shared skills
#   ./scripts/orca-clean-agent.sh run <profile-name> [claude|codex] [args...]
set -euo pipefail

PROFILES_ROOT="${HOME}/.claude-profiles"
SHARED_SKILLS="${HOME}/.claude/skills"

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[0;33m%s\033[0m\n' "$*"; }

check_exit_ip() {
    local info
    info="$(curl -fsSL --max-time 8 https://ipinfo.io/json 2>/dev/null || true)"
    if [[ -z "${info}" ]]; then
        red "[BLOCKED] Could not reach ipinfo.io. Refusing to start AI agent without verified exit."
        exit 1
    fi

    local ip country org
    ip="$(printf '%s' "${info}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ip",""))')"
    country="$(printf '%s' "${info}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("country",""))')"
    org="$(printf '%s' "${info}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("org",""))')"

    if [[ "${country}" == "RU" || "${country}" == "BY" ]]; then
        red "[KILL-SWITCH] Exit IP ${ip} is in ${country} (${org})! Aborting immediately to protect account."
        exit 1
    fi

    local org_lower
    org_lower="$(printf '%s' "${org}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${org_lower}" == *"aeza"* || "${org_lower}" == *"zomro"* ]]; then
        red "[KILL-SWITCH] Exit IP ${ip} belongs to flagged ASN (${org})! Configure IPRoyal Static ISP first."
        exit 1
    fi

    green "[OK] Clean Exit Verified: IP=${ip} | Country=${country} | ASN=${org}"
}

purge_legacy() {
    local backup_dir="${HOME}/.claude-legacy-backup-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "${backup_dir}"
    yellow "Backing up legacy Claude state to ${backup_dir}..."

    if [[ -f "${HOME}/.claude.json" ]]; then
        mv "${HOME}/.claude.json" "${backup_dir}/.claude.json"
        green "Moved ~/.claude.json (contained old userID / anonymousId / oauthAccount)"
    fi

    for item in ".credentials.json" "statsig" "projects" "todos"; do
        if [[ -e "${HOME}/.claude/${item}" ]]; then
            mv "${HOME}/.claude/${item}" "${backup_dir}/${item}"
            green "Moved ~/.claude/${item}"
        fi
    done

    # Purge macOS Keychain entries for Claude Code OAuth
    for svc in "Claude Code-credentials" "Claude Code" "claude.ai"; do
        if security find-generic-password -s "${svc}" >/dev/null 2>&1; then
            security delete-generic-password -s "${svc}" >/dev/null 2>&1 || true
            green "Deleted macOS Keychain item: ${svc}"
        fi
    done

    green "Legacy identifiers purged. Skills in ~/.claude/skills remain intact."
}

init_profile() {
    local profile="${1:?Usage: $0 init <profile-name>}"
    local dir="${PROFILES_ROOT}/${profile}"
    mkdir -p "${dir}"

    if [[ -d "${SHARED_SKILLS}" && ! -e "${dir}/skills" ]]; then
        ln -s "${SHARED_SKILLS}" "${dir}/skills"
        green "Linked shared skills -> ${dir}/skills"
    fi

    if [[ -f "${HOME}/.claude/settings.json" && ! -f "${dir}/settings.json" ]]; then
        cp "${HOME}/.claude/settings.json" "${dir}/settings.json"
        green "Copied settings.json -> ${dir}/settings.json"
    fi

    green "Profile initialized: CLAUDE_CONFIG_DIR=${dir}"
}

run_agent() {
    local profile="${1:?Usage: $0 run <profile-name> [claude|codex] [args...]}"
    shift
    local cmd="${1:-claude}"
    shift || true

    check_exit_ip
    init_profile "${profile}"

    export CLAUDE_CONFIG_DIR="${PROFILES_ROOT}/${profile}"
    exec "${cmd}" "$@"
}

case "${1:-check}" in
    check)
        check_exit_ip
        ;;
    purge-legacy)
        purge_legacy
        ;;
    init)
        shift
        init_profile "$@"
        ;;
    run)
        shift
        run_agent "$@"
        ;;
    *)
        echo "Usage: $0 {check|purge-legacy|init <profile>|run <profile> [claude|codex] [args...]}"
        exit 1
        ;;
esac
