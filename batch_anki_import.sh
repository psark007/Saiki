#!/bin/bash

prog="$(basename "$0")"

print_help() {
  cat <<EOF
usage: $prog [-h] [--tags TAG1,TAG2,...] {es,jp}

positional arguments:
  {es,jp}

options:
  -h, --help            show this help message and exit
  --tags TAG1,TAG2,...  comma-separated list of additional tags (default: AI-generated)
                        text-to-speech is always included
EOF
}

arg_error_missing_lang() {
  echo "usage: $prog [-h] [--tags TAG1,TAG2,...] {es,jp}" >&2
  echo "$prog: error: the following arguments are required: lang" >&2
  exit 2
}

arg_error_unknown() {
  echo "usage: $prog [-h] [--tags TAG1,TAG2,...] {es,jp}" >&2
  echo "$prog: error: unrecognized arguments: $*" >&2
  exit 2
}

lang=""
custom_tags=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --tags)
      if [[ -z "$2" || "$2" == -* ]]; then
        echo "$prog: error: --tags requires an argument" >&2
        exit 2
      fi
      custom_tags="$2"
      shift 2
      ;;
    jp|es)
      if [[ -n "$lang" ]]; then
        arg_error_unknown "$1"
      fi
      lang="$1"
      shift
      ;;
    *)
      arg_error_unknown "$1"
      ;;
  esac
done

[[ -z "$lang" ]] && arg_error_missing_lang

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$prog: error: required command not found: $1" >&2
    exit 1
  fi
}

require_command gtts-cli
require_command ffmpeg
require_command curl
require_command jq

# Build tags JSON array - text-to-speech is always included
tags=("text-to-speech")
if [[ -n "$custom_tags" ]]; then
  IFS=',' read -ra tag_array <<< "$custom_tags"
  for tag in "${tag_array[@]}"; do
    # Trim whitespace
    tag="$(printf '%s' "$tag" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -n "$tag" ]] && tags+=("$tag")
  done
else
  tags+=("AI-generated")
fi

TAGS="$(printf '%s\n' "${tags[@]}" | jq -R . | jq -s .)"

case "$lang" in
  jp)
    DECK_NAME="日本語"
    LANG_CODE="ja"
    TLD="com"
    TEMPO="1.35"
    SENTENCE_FILE="$HOME/Languages/Anki/sentences_jp.txt"
    ;;
  es)
    DECK_NAME="Español"
    LANG_CODE="es"
    TLD="es"
    TEMPO="1.25"
    SENTENCE_FILE="$HOME/Languages/Anki/sentences_es.txt"
    ;;
esac

count=0

if [[ ! -f "$SENTENCE_FILE" ]]; then
    echo "$prog: error: sentence file not found: $SENTENCE_FILE" >&2
    exit 1
fi

# Use a temporary directory to handle processing
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

while IFS= read -r sentence || [[ -n "$sentence" ]]; do
    [[ -z "$sentence" ]] && continue

    # Generate unique filenames
    BASENAME="tts_$(date +%Y%m%d_%H%M%S)_${lang}_$RANDOM"
    # Path for the raw output from gtts
    RAW_OUTPUT="$TEMP_DIR/${BASENAME}_original.mp3"
    # Path for the sped-up output that goes to Anki
    OUTPUT_PATH="$TEMP_DIR/${BASENAME}.mp3"

    echo "🔊 Processing: $sentence"

    # 1. Generate TTS with specific TLD
    if gtts-cli "$sentence" --lang "$LANG_CODE" --tld "$TLD" --output "$RAW_OUTPUT"; then
        
        # 2. Speed up audio using ffmpeg without changing pitch
        if ffmpeg -loglevel error -i "$RAW_OUTPUT" -filter:a "atempo=$TEMPO" -y "$OUTPUT_PATH" < /dev/null; then
            
            # 3. Add to Anki using the sped-up file
            payload="$(jq -n \
              --arg deck "$DECK_NAME" \
              --arg sentence "$sentence" \
              --arg path "$OUTPUT_PATH" \
              --arg filename "${BASENAME}.mp3" \
              --argjson tags "$TAGS" \
              '{
                action: "addNote",
                version: 6,
                params: {
                  note: {
                    deckName: $deck,
                    modelName: "Basic",
                    fields: {
                      Front: "",
                      Back: $sentence
                    },
                    options: {
                      allowDuplicate: false
                    },
                    tags: $tags,
                    audio: [{
                      path: $path,
                      filename: $filename,
                      fields: ["Front"]
                    }]
                  }
                }
              }')"

            result=$(curl -s localhost:8765 -X POST -H "Content-Type: application/json" -d "$payload")

            if jq -e '.error == null' >/dev/null 2>&1 <<< "$result"; then
                echo "✅ Added card: $sentence"
                ((count++))
            else
                echo "❌ Failed to add card: $sentence"
                echo "$result"
            fi
        else
            echo "❌ Failed to speed up audio for: $sentence"
        fi

        # 4. Cleanup
        rm -f "$OUTPUT_PATH" "$RAW_OUTPUT"
    else
        echo "❌ Failed to generate TTS for: $sentence"
    fi

done <"$SENTENCE_FILE"

echo "🎉 Done! Added $count cards to deck \"$DECK_NAME\"."
