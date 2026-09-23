#!/usr/bin/env bash
# Fake ffmpeg pour les tests. Simule un export qui dure environ 1 seconde
# et écrit des métriques de progression compatibles avec Kut-Studio.

set -u

OUTPUT_PATH=""
TOTAL_DURATION_US=1000000  # 1 seconde simulée
SLEEP_INTERVAL=0.05        # 50ms entre chaque "frame"

while [[ $# -gt 0 ]]; do
    case "$1" in
        FAIL*)
            echo "Erreur simulée pour les tests" >&2
            exit 1
            ;;
        *)
            # Le dernier argument est le fichier de sortie
            if [[ "${1:0:1}" != "-" ]]; then
                OUTPUT_PATH="$1"
            fi
            shift
            ;;
    esac
done

# Simuler la progression
ELAPSED_US=0
STEP_US=$((TOTAL_DURATION_US / 20))  # 20 frames

echo "fake_ffmpeg starting, output: $OUTPUT_PATH"

while [[ $ELAPSED_US -lt $TOTAL_DURATION_US ]]; do
    echo "out_time_us=$ELAPSED_US"
    sleep $SLEEP_INTERVAL
    ELAPSED_US=$((ELAPSED_US + STEP_US))
done

# Créer un fichier de sortie vide pour simuler le résultat
if [[ -n "$OUTPUT_PATH" ]]; then
    mkdir -p "$(dirname "$OUTPUT_PATH")"
    : > "$OUTPUT_PATH"
    echo "fake_ffmpeg wrote: $OUTPUT_PATH"
fi

exit 0
