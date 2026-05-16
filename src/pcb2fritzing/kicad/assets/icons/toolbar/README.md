# Toolbar Icon Placeholders

Put KiCad Action Plugin toolbar icons in this folder.

Required filenames expected by the plugin:
- icon_light.png
- icon_dark.png

Recommended format:
- PNG
- 24x24 px (works well in toolbar)
- Transparent background

Behavior:
- If either file is missing, KiCad falls back to the default toolbar icon.
- If only icon_light.png exists, KiCad will use it for the light theme only.
- If only icon_dark.png exists, KiCad will use it for the dark theme only.
