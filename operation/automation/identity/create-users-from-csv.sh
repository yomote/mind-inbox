#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Create Entra ID users (member/guest) from CSV and optionally add them to groups.

Usage:
  ./operation/identity/create-users-from-csv.sh --csv <path> [options]

Options:
  --csv <path>                 CSV file path (required)
  --default-domain <domain>    Default UPN domain for member users when userPrincipalName is empty
  --default-password <value>   Default password for member users when password is empty
  --invite-redirect-url <url>  Redirect URL for guest invitation (default: https://myapplications.microsoft.com)
  --welcome-message <true|false> Send invitation mail for guest users (default: false)
  -h, --help                   Show this help

CSV header (required):
  mode,displayName,userPrincipalName,email,mailNickname,password,groups

Columns:
  mode: member | guest
  displayName: Display name
  userPrincipalName: Required for member unless --default-domain is set
  email: Required for guest
  mailNickname: Optional (auto-generated if empty)
  password: Optional for member (auto-generated if empty)
  groups: Optional, semicolon separated. Group displayName or objectId

Notes:
  - CSV parser is simple (commas inside fields are not supported).
  - Existing users are reused (idempotent-ish behavior).
EOF
}

CSV_PATH=""
DEFAULT_DOMAIN=""
DEFAULT_PASSWORD=""
INVITE_REDIRECT_URL="https://myapplications.microsoft.com"
WELCOME_MESSAGE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --csv)
      CSV_PATH="$2"
      shift 2
      ;;
    --default-domain)
      DEFAULT_DOMAIN="$2"
      shift 2
      ;;
    --default-password)
      DEFAULT_PASSWORD="$2"
      shift 2
      ;;
    --invite-redirect-url)
      INVITE_REDIRECT_URL="$2"
      shift 2
      ;;
    --welcome-message)
      WELCOME_MESSAGE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$CSV_PATH" ]]; then
  echo "ERROR: --csv is required" >&2
  usage
  exit 1
fi

if [[ ! -f "$CSV_PATH" ]]; then
  echo "ERROR: CSV file not found: $CSV_PATH" >&2
  exit 1
fi

if ! command -v az >/dev/null 2>&1; then
  echo "ERROR: az command not found" >&2
  exit 1
fi

az account show >/dev/null

trim() {
  local v="$1"
  # shellcheck disable=SC2001
  v="$(echo "$v" | sed -e 's/^\s*//' -e 's/\s*$//')"
  echo "$v"
}

lower() {
  echo "$1" | tr '[:upper:]' '[:lower:]'
}

is_uuid() {
  [[ "$1" =~ ^[0-9a-fA-F-]{36}$ ]]
}

generate_password() {
  if command -v openssl >/dev/null 2>&1; then
    local p
    p="$(openssl rand -base64 24 | tr -d '/+=\n' | cut -c1-20)"
    echo "${p}Aa1!"
  else
    echo "Temp$(date +%s)Aa1!"
  fi
}

safe_nickname() {
  local src="$1"
  local nick
  nick="$(echo "$src" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9._-')"
  if [[ -z "$nick" ]]; then
    nick="user$(date +%s)"
  fi
  echo "$nick"
}

find_user_id_by_upn() {
  local upn="$1"
  az ad user show --id "$upn" --query id -o tsv 2>/dev/null || true
}

find_user_id_by_mail() {
  local mail="$1"
  az ad user list --filter "mail eq '$mail'" --query "[0].id" -o tsv 2>/dev/null || true
}

resolve_group_id() {
  local key="$1"
  if [[ -z "$key" ]]; then
    echo ""
    return 0
  fi

  if is_uuid "$key"; then
    echo "$key"
    return 0
  fi

  az ad group list --filter "displayName eq '$key'" --query "[0].id" -o tsv 2>/dev/null || true
}

add_user_to_groups() {
  local user_id="$1"
  local groups="$2"

  if [[ -z "$groups" ]]; then
    return 0
  fi

  IFS=';' read -r -a group_array <<< "$groups"
  for g in "${group_array[@]}"; do
    local group_key group_id
    group_key="$(trim "$g")"
    [[ -z "$group_key" ]] && continue

    group_id="$(resolve_group_id "$group_key")"
    if [[ -z "$group_id" ]]; then
      echo "WARN: group not found: $group_key" >&2
      continue
    fi

    if az ad group member add --group "$group_id" --member-id "$user_id" >/dev/null 2>&1; then
      echo "  + group assigned: $group_key"
    else
      echo "  = group already assigned or add failed: $group_key"
    fi
  done
}

line_no=0
created=0
reused=0
failed=0

while IFS=',' read -r mode display_name upn email mail_nickname password groups || [[ -n "${mode:-}" ]]; do
  line_no=$((line_no + 1))

  mode="$(trim "${mode:-}")"
  display_name="$(trim "${display_name:-}")"
  upn="$(trim "${upn:-}")"
  email="$(trim "${email:-}")"
  mail_nickname="$(trim "${mail_nickname:-}")"
  password="$(trim "${password:-}")"
  groups="$(trim "${groups:-}")"

  # header / blank lines
  if [[ "$line_no" -eq 1 && "$(lower "$mode")" == "mode" ]]; then
    continue
  fi
  [[ -z "$mode$display_name$upn$email$mail_nickname$password$groups" ]] && continue

  mode="$(lower "$mode")"
  echo "[$line_no] processing: mode=$mode displayName=$display_name"

  if [[ -z "$display_name" ]]; then
    echo "ERROR: displayName is required (line $line_no)" >&2
    failed=$((failed + 1))
    continue
  fi

  if [[ "$mode" == "member" ]]; then
    if [[ -z "$upn" ]]; then
      if [[ -z "$DEFAULT_DOMAIN" ]]; then
        echo "ERROR: userPrincipalName is required for member users (line $line_no)" >&2
        failed=$((failed + 1))
        continue
      fi
      local_part="$(echo "$display_name" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-20)"
      [[ -z "$local_part" ]] && local_part="user$line_no"
      upn="${local_part}@${DEFAULT_DOMAIN}"
    fi

    if [[ -z "$mail_nickname" ]]; then
      mail_nickname="$(safe_nickname "${upn%@*}")"
    fi

    user_id="$(find_user_id_by_upn "$upn")"
    if [[ -n "$user_id" ]]; then
      echo "  = existing member user: $upn"
      reused=$((reused + 1))
    else
      if [[ -z "$password" ]]; then
        if [[ -n "$DEFAULT_PASSWORD" ]]; then
          password="$DEFAULT_PASSWORD"
        else
          password="$(generate_password)"
        fi
      fi

      user_id="$(az ad user create \
        --display-name "$display_name" \
        --user-principal-name "$upn" \
        --mail-nickname "$mail_nickname" \
        --password "$password" \
        --force-change-password-next-sign-in true \
        --query id -o tsv)"

      echo "  + created member user: $upn"
      created=$((created + 1))
    fi

    add_user_to_groups "$user_id" "$groups"

  elif [[ "$mode" == "guest" ]]; then
    if [[ -z "$email" ]]; then
      echo "ERROR: email is required for guest users (line $line_no)" >&2
      failed=$((failed + 1))
      continue
    fi

    user_id="$(find_user_id_by_mail "$email")"
    if [[ -n "$user_id" ]]; then
      echo "  = existing guest user (mail): $email"
      reused=$((reused + 1))
    else
      user_id="$(az rest \
        --method post \
        --uri 'https://graph.microsoft.com/v1.0/invitations' \
        --headers Content-Type=application/json \
        --body "{\"invitedUserEmailAddress\":\"$email\",\"inviteRedirectUrl\":\"$INVITE_REDIRECT_URL\",\"sendInvitationMessage\":$WELCOME_MESSAGE,\"invitedUserDisplayName\":\"$display_name\"}" \
        --query invitedUser.id -o tsv)"

      echo "  + invited guest user: $email"
      created=$((created + 1))
    fi

    add_user_to_groups "$user_id" "$groups"

  else
    echo "ERROR: mode must be member or guest (line $line_no)" >&2
    failed=$((failed + 1))
    continue
  fi

done < "$CSV_PATH"

echo ""
echo "Done. created=$created reused=$reused failed=$failed"
if [[ "$failed" -gt 0 ]]; then
  exit 2
fi
