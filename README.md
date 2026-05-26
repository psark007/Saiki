# Saiki

**Saiki** (`採記`) is a small toolkit for Anki-based language learning workflows:
listening playlists, word mining, YouTube transcript mining, TTS sentence
imports, and known/new word comparison.

The name is a coined Japanese compound from `採` as in gathering/collecting and
`記` as in remembering or recording. Pronunciation: `saiki`, roughly
"sigh-key".

```shell
./saiki.py --help
```

## Requirements

- Python 3.12 recommended
- [Anki](https://apps.ankiweb.net/) with [AnkiConnect](https://github.com/amikey/anki-connect)
- `ffmpeg`
- Python dependencies from `requirements.txt`
- spaCy models for word mining:

```shell
python -m spacy download es_core_news_sm
python -m spacy download ja_core_news_lg
```

Setup example:

```shell
python3.12 -m venv ~/.venv/saiki
source ~/.venv/saiki/bin/activate
python3 -m pip install -U pip
pip install -r requirements.txt
sudo dnf install ffmpeg
```

## Configuration

Defaults are built in, but you can override them with YAML:

```shell
~/.config/saiki/config.yaml
```

Or pass a config explicitly:

```shell
./saiki.py --config ./config.yaml words jp
```

Example:

```yaml
anki_connect_url: http://localhost:8765
media_dir: ~/.var/app/net.ankiweb.Anki/data/Anki2/User 1/collection.media
audio_output_root: ~/Languages/Anki/anki-audio
word_output_root: ~/Languages/Anki/anki-words
sentence_dir: ~/Languages/Anki
note_model: Basic
fields:
  front: Front
  back: Back
languages:
  jp:
    name: japanese
    transcript_code: ja
    tts_code: ja
    tts_tld: com
    tts_tempo: 1.35
    decks: ["日本語"]
    field: Back
    word_model: ja_core_news_lg
    sentence_file: sentences_jp.txt
  es:
    name: spanish
    transcript_code: es
    tts_code: es
    tts_tld: es
    tts_tempo: 1.25
    decks: ["Español"]
    field: Back
    word_model: es_core_news_sm
    sentence_file: sentences_es.txt
```

A copyable template is also available at `examples/config.yaml`.

Supported language codes by default:

- `jp`
- `es`

## CLI

### Audio

Extract audio referenced by `[sound:...]` tags from configured decks and create
an `.m3u` playlist.

```shell
./saiki.py audio jp
./saiki.py audio es --concat
./saiki.py audio jp --media-dir ~/.local/share/Anki2/User\ 1/collection.media --copy-only-new
```

Outputs go to `~/Languages/Anki/anki-audio/<language>/` by default.

### Words

Extract frequent words from Anki notes using AnkiConnect and spaCy.

```shell
./saiki.py words jp
./saiki.py words es --deck "Español"
./saiki.py words es --query 'deck:"Español" tag:youtube'
./saiki.py words jp --min-freq 3 --out words_jp.txt
./saiki.py words jp --full-field
```

Output format:

```text
word frequency
```

Examples:

```text
comer 12
hablar 9
行く (行き) 8
見る (見た) 6
```

### YouTube

Mine vocabulary or sentence rows from YouTube subtitles.

```shell
./saiki.py youtube es VIDEO_ID
./saiki.py youtube es VIDEO_ID --top 50
./saiki.py youtube jp VIDEO_ID --mode sentences
./saiki.py youtube es VIDEO_ID --raw --no-stopwords
```

Export Anki-ready sentence rows:

```shell
./saiki.py youtube es VIDEO_ID --mode sentences --out youtube.tsv
```

Export only rows that appear to contain unknown vocabulary:

```shell
./saiki.py youtube es VIDEO_ID \
  --mode sentences \
  --out youtube_new.tsv \
  --known-words ~/Languages/Anki/anki-words/spanish/words_es.txt \
  --only-new
```

Sentence exports contain:

```text
sentence    timestamp    video_url    vocab_guess
```

### Import

Generate TTS audio and add sentence cards to Anki.

```shell
./saiki.py import es
./saiki.py import jp ~/Languages/Anki/sentences_jp.txt
./saiki.py import es youtube.tsv --tags youtube,manual
```

The importer accepts plain text sentence files and TSV/CSV files with a
`sentence` column. `text-to-speech` is always added as a tag. If `--tags` is not
provided, `AI-generated` is added.

### Known/New Words

Compare any generated word list against an existing known list:

```shell
./saiki.py compare-words transcript_words.txt ~/Languages/Anki/anki-words/spanish/words_es.txt
```

This prints entries from the first file whose word key does not appear in the
second file.

## Card Assumptions

The default configuration assumes Basic notes with audio on `Front` and the
target-language sentence on `Back`. Word mining reads only the first visible
line by default; use `--full-field` to process the whole field.

![anki_basic_card_jp](./figures/anki_basic_card_jp.png)

## To Do

- Add support for different Anki note/card types, including configurable field
  mappings per language and per import workflow.
- Support multiple import profiles, such as sentence cards, vocab cards, audio
  cards, and cloze cards.
- Let YouTube exports map directly into configurable note fields, not just a
  fixed `sentence` column.
- Add richer transcript filtering, such as minimum/maximum sentence length,
  duplicate removal, and punctuation cleanup.
- Add optional audio slicing from videos when timestamp data is available.
- Improve known/new word matching with better lemmatization for transcript
  vocabulary.
- Add more language profiles beyond Japanese and Spanish.
- Add a dry-run mode for imports that previews notes before sending anything to
  AnkiConnect.
- Build a GUI for common workflows like transcript review, sentence selection,
  import previews, and configuration editing.
- Add integration tests with mocked AnkiConnect responses.
- Add shell completion or a small installed command once packaging becomes
  useful.

## Tests

Pure logic tests use the standard library test runner:

```shell
python -m unittest discover -s tests
```

## License

This project is licensed under the MIT License. See [`LICENSE`](./LICENSE).
