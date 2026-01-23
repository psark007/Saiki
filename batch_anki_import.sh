#!/bin/bash

prog="$(basename "$0")"

print_help() {
  cat <<EOF
usage: $prog [-h] {es,jp}

positional arguments:
  {es,jp}

options:
  -h, --help  show this help message and exit
EOF
}

arg_error_missing_lang() {
  echo "usage: $prog [-h] {es,jp}" >&2
  echo "$prog: error: the following arguments are required: lang" >&2
  exit 2
}

arg_error_unknown() {
  echo "usage: $prog [-h] {es,jp}" >&2
  echo "$prog: error: unrecognized arguments: $*" >&2
  exit 2
}

lang=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
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

case "$lang" in
  jp)
    DECK_NAME="日本語"
    LANG_CODE="ja"
    TLD="com"
    TEMPO="1.35"
    SENTENCE_FILE="$HOME/Documents/sentences_jp.txt"
    ;;
  es)
    DECK_NAME="Español"
    LANG_CODE="es"
    TLD="es"
    TEMPO="1.25"
    SENTENCE_FILE="$HOME/Documents/sentences_es.txt"
    ;;
esac


TAGS='["AI-generated", "text-to-speech"]'
count=0

# Use a temporary directory to handle processing
TEMP_DIR=$(mktemp -d)

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
            result=$(curl -s localhost:8765 -X POST -d "{
		    \"action\": \"addNote\", 
		    \"version\": 6, 
		    \"params\": { 
			    \"note\": { 
				    \"deckName\": \"$DECK_NAME\", 
				    \"modelName\": \"Basic\", 
				    \"fields\": { 
					    \"Front\": \"\", 
					    \"Back\": \"$sentence\" 
				    }, 
				    \"options\": {
					    \"allowDuplicate\": false 
				    }, 
			    	    \"tags\": $TAGS, 
				    \"audio\": [{ 
					    \"path\": \"$OUTPUT_PATH\", 
					    \"filename\": \"${BASENAME}.mp3\", 
					    \"fields\": [\"Front\"] 
				    }] 
		    	    } 
	     		} 
		}")

            if [[ "$result" == *'"error": null'* ]]; then
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

# Cleanup temp directory
rm -rf "$TEMP_DIR"

echo "🎉 Done! Added $count cards to deck \"$DECK_NAME\"."
