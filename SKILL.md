---
name: canvas-draw-image
description: Generate or edit images from Codex using the bundled standalone Python OpenAI-compatible image script. Use when the user asks Codex to draw, generate an image, create artwork, run the local drawing script, test the configured image API, or perform reference-image edits outside the Infinite Canvas web app.
---

# Canvas Draw Image

Use the bundled script for deterministic image generation through the user's OpenAI-compatible image endpoint.

## Script

Use `gpt-image-2.5-flare` by default and when the user requests “gpt-image 2.5”. Pass `--model gpt-image-2.5-flare` explicitly; if the user specifies another exact model ID, use that instead.

Run:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --model gpt-image-2.5-flare "prompt text"
```

The script reads configuration in this order:

- CLI args: `--api-key`, `--base-url`, `--model`, `--quality`, `--size`, `--count`
- Environment variables: `CANVAS_API_KEY`, `CANVAS_BASE_URL`, `CANVAS_IMAGE_MODEL`, `CANVAS_IMAGE_QUALITY`, `CANVAS_IMAGE_SIZE`, `CANVAS_IMAGE_COUNT`
- Config files: `./.canvas-draw-image.env`, `./.env`, `~/.canvas-draw-image.env`, or `~/.env`
- Shell profiles for bash, zsh, fish, POSIX profile, and PowerShell profile files

If `CANVAS_API_KEY` is missing in an interactive terminal, the script prompts for API key, Base URL, and model, then saves them to `~/.canvas-draw-image.env` with `600` permissions. In non-interactive runs, it prints setup instructions and exits.

Do not print API keys. Prefer relying on the configured environment/profile or the first-run setup file.

Explicit setup:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --setup
```

## Common Tasks

Generate one image:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --model gpt-image-2.5-flare --count 1 --size 1:1 "一只白色机械猫，赛博朋克风格"
```

Generate multiple images:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --model gpt-image-2.5-flare --count 3 --size 16:9 "雨夜里的未来城市街景"
```

Use a reference image for image editing:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --model gpt-image-2.5-flare --reference /path/to/reference.png "把参考图改成水彩插画风格"
```

Use a mask for local edits:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --model gpt-image-2.5-flare --reference /path/to/source.png --mask /path/to/mask.png "只修改透明蒙版区域"
```

Interactive mode:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --model gpt-image-2.5-flare
```

## Output

The script saves images under `output/drawings` by default and prints each saved path. After generation, verify the output with:

```bash
file <saved-image-path>
ls -lh <saved-image-path>
```

If the user needs the image previewed, use the local image viewing tool on the saved file.

## Notes

- The script calls `/v1/images/generations` for prompt-only generation and `/v1/images/edits` when `--reference` is supplied.
- It accepts `size` as `auto`, `WIDTHxHEIGHT`, or a ratio such as `1:1`, `16:9`, or `9:16`.
- It requests `response_format=b64_json` and saves returned `b64_json` or image URLs as local files.
- If TLS fails, first fix the local Python certificate setup. Use `--insecure` only as a temporary diagnostic.
