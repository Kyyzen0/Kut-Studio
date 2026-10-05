#!/usr/bin/env bash
# Signature Developer ID et notarisation macOS de Kut-Studio, pour .github/workflows/release.yml.
#
#   macos_signing.sh setup                      importe le certificat dans un trousseau temporaire et exporte
#                                               KUT_STUDIO_CODESIGN_IDENTITY (build.py signe alors chaque binaire)
#   macos_signing.sh notarize <app> <statut>    vérifie la signature, notarise, agrafe le ticket ; écrit l'état
#                                               « notarized », « signed » ou « adhoc » dans le fichier <statut>
#   macos_signing.sh cleanup                    supprime le trousseau et toute clé décodée (à lancer même en échec)
#
# Secrets (Settings › Secrets and variables › Actions) ; aucun n'est jamais écrit dans le dépôt ni affiché :
#   MACOS_CERTIFICATE_P12_BASE64   certificat « Developer ID Application » + clé privée, export .p12 en base64
#   MACOS_CERTIFICATE_PASSWORD     mot de passe du .p12
#   MACOS_SIGNING_IDENTITY         nom exact, ex. « Developer ID Application: Nom (TEAMID1234) »
#   APPLE_API_KEY_P8_BASE64        clé d'API App Store Connect (.p8) en base64, pour notarytool
#   APPLE_API_KEY_ID               identifiant de cette clé
#   APPLE_API_ISSUER_ID            identifiant de l'émetteur (Issuer ID)
#
# Sans les trois premiers : signature ad hoc, NON notarisée — avertissement dans le journal, le résumé du job et les
# notes de la release. Sans les trois derniers : signée Developer ID mais NON notarisée, idem. Jamais d'échec
# silencieux : si les secrets sont là et que la notarisation échoue, le job échoue et rien n'est publié.
set -euo pipefail

TMP="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"

have() { [ -n "${!1:-}" ]; }

setup() {
    for name in MACOS_CERTIFICATE_P12_BASE64 MACOS_CERTIFICATE_PASSWORD MACOS_SIGNING_IDENTITY; do
        if ! have "$name"; then
            echo "::warning title=macOS non signé::Secret $name absent : construction signée ad hoc, NON notarisée."
            return 0
        fi
    done
    local keychain="$TMP/kut-signing.keychain-db"
    local password
    password="$(openssl rand -base64 24)"
    security create-keychain -p "$password" "$keychain"
    security set-keychain-settings -lut 21600 "$keychain"
    security unlock-keychain -p "$password" "$keychain"
    local certificate="$TMP/kut-signing.p12"
    (umask 077 && printf '%s' "$MACOS_CERTIFICATE_P12_BASE64" | base64 --decode > "$certificate")
    security import "$certificate" -k "$keychain" -P "$MACOS_CERTIFICATE_PASSWORD" \
        -T /usr/bin/codesign -T /usr/bin/security >/dev/null
    rm -f "$certificate"
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$password" "$keychain" >/dev/null
    # Ajoute le trousseau temporaire à la liste de recherche, sans retirer ceux du système.
    # shellcheck disable=SC2046
    security list-keychains -d user -s "$keychain" $(security list-keychains -d user | tr -d '"')
    if ! security find-identity -v -p codesigning "$keychain" | grep -F -q "$MACOS_SIGNING_IDENTITY"; then
        echo "::error::L'identité de signature indiquée par MACOS_SIGNING_IDENTITY est absente du certificat importé."
        exit 1
    fi
    {
        echo "KUT_STUDIO_CODESIGN_IDENTITY=$MACOS_SIGNING_IDENTITY"
        echo "KUT_SIGNING_KEYCHAIN=$keychain"
    } >> "$GITHUB_ENV"
}

notarize() {
    local app="$1" status_file="$2"
    if [ ! -d "$app" ]; then
        echo "::error::Application introuvable : $app"
        exit 1
    fi
    if [ -z "${KUT_STUDIO_CODESIGN_IDENTITY:-}" ]; then
        codesign --verify --deep --strict "$app"
        echo "adhoc" > "$status_file"
        echo "::warning title=macOS non notarisé::Paquet signé ad hoc et NON notarisé : macOS bloquera sa première ouverture."
        echo "- macOS : **signature ad hoc, non notarisé** (secrets de signature absents)" >> "$SUMMARY"
        return 0
    fi
    codesign --verify --deep --strict --verbose=2 "$app"
    codesign --display --verbose=2 "$app" 2>&1 | grep -E "^(Authority|TeamIdentifier|Runtime)" || true
    for name in APPLE_API_KEY_P8_BASE64 APPLE_API_KEY_ID APPLE_API_ISSUER_ID; do
        if ! have "$name"; then
            echo "signed" > "$status_file"
            echo "::warning title=macOS non notarisé::Secret $name absent : paquet signé Developer ID mais NON notarisé."
            echo "- macOS : signé Developer ID, **non notarisé**" >> "$SUMMARY"
            return 0
        fi
    done
    local key="$TMP/kut-notary-key.p8" archive="$TMP/kut-notarize.zip" result="$TMP/kut-notary-result.json"
    (umask 077 && printf '%s' "$APPLE_API_KEY_P8_BASE64" | base64 --decode > "$key")
    ditto -c -k --sequesterRsrc --keepParent "$app" "$archive"
    local credentials=(--key "$key" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER_ID")
    xcrun notarytool submit "$archive" "${credentials[@]}" --wait --timeout 45m --output-format json > "$result" || true
    rm -f "$archive"
    local status submission
    status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", ""))' "$result" 2>/dev/null || true)"
    submission="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$result" 2>/dev/null || true)"
    if [ "$status" != "Accepted" ]; then
        echo "::error::Notarisation refusée ou incomplète (statut : ${status:-inconnu}). Rien ne sera publié."
        if [ -n "$submission" ]; then
            xcrun notarytool log "$submission" "${credentials[@]}" || true
        fi
        rm -f "$key"
        exit 1
    fi
    rm -f "$key"
    xcrun stapler staple "$app"
    xcrun stapler validate "$app"
    spctl --assess --type execute --verbose=4 "$app"
    echo "notarized" > "$status_file"
    echo "- macOS : signé Developer ID et **notarisé** (ticket agrafé)" >> "$SUMMARY"
}

cleanup() {
    if [ -n "${KUT_SIGNING_KEYCHAIN:-}" ] && [ -f "$KUT_SIGNING_KEYCHAIN" ]; then
        security delete-keychain "$KUT_SIGNING_KEYCHAIN" || true
    fi
    rm -f "$TMP/kut-signing.p12" "$TMP/kut-notary-key.p8" "$TMP/kut-notarize.zip"
}

case "${1:-}" in
    setup) setup ;;
    notarize) notarize "${2:?application .app attendue}" "${3:?fichier de statut attendu}" ;;
    cleanup) cleanup ;;
    *) echo "usage : $0 setup | notarize <app> <fichier de statut> | cleanup" >&2; exit 2 ;;
esac
