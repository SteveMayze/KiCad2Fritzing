# KiCad Plugin Icon Spec

This document defines the icon assets used by PCB to Fritzing Part.

## 1. Required Files

Toolbar icons (Action Plugin button):
- `src/pcb2fritzing/kicad/assets/icons/toolbar/icon_light.png`
- `src/pcb2fritzing/kicad/assets/icons/toolbar/icon_dark.png`

PCM catalog icon (Plugin and Content Manager):
- `src/pcb2fritzing/kicad/assets/icons/pcm/icon.png`

## 2. Sizes and Format

- Format: PNG
- Color: RGBA (transparent background)
- Toolbar size: 24x24 px
- PCM size: 64x64 px

Use exact canvas sizes above. Avoid oversized source files for these final assets.

## 3. Visual Guidance

- Keep the icon shape simple and recognizable at small sizes.
- Use a bold silhouette first; internal details should be minimal.
- Ensure at least 2 px visual padding from canvas edges.
- Avoid thin strokes below 1.5 px at 24x24.
- Avoid text labels inside the icon.

Theme contrast guidance:
- `icon_light.png`: designed for light UI backgrounds.
- `icon_dark.png`: designed for dark UI backgrounds.
- Keep motif consistent between light/dark variants.

## 4. Export Checklist

- Background is transparent.
- No anti-aliased halo from an opaque background.
- Subject remains legible at 100% zoom on a 24x24 preview.
- Light and dark variants are both visually balanced.
- PCM icon reads clearly at 64x64 and at reduced thumbnail scale.

## 5. Build and Validation

Development build (allows missing icons):

```bash
python3 scripts/build_kicad10_dist.py
```

Release build (fails if any required icon is missing):

```bash
python3 scripts/build_kicad10_dist.py --release
```

If release validation fails, add missing files from Section 1 and rebuild.
