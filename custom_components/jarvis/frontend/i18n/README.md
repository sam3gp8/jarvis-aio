# JARVIS panel UI translations

One JSON file per language (e.g. `fr.json`), keyed by the **exact English string** shown
in the panel, mapping to the translation. The panel picks the file matching your Home
Assistant language automatically; untranslated strings simply stay English.

To add or extend a language: copy an existing file, translate the values, keep the keys
identical. Technical values (entity IDs, model names, numbers) are never translated.
