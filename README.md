# Canvas Draw Image Skill

Codex skill for generating or editing images through an OpenAI-compatible image API. It bundles a standalone Python script and does not require the Infinite Canvas web app to be running.

## Install

Clone this repository into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/LinckLin/canvas-draw-image.git ~/.codex/skills/canvas-draw-image
```

Restart Codex so the skill list is refreshed.

## Configure

Set at least:

- `CANVAS_API_KEY`: your API key
- `CANVAS_BASE_URL`: your OpenAI-compatible base URL, for example `https://example.com/v1`

Optional variables:

- `CANVAS_IMAGE_MODEL`, default `gpt-image-2`
- `CANVAS_IMAGE_SIZE`, default `1:1`
- `CANVAS_IMAGE_QUALITY`, default `auto`
- `CANVAS_IMAGE_COUNT`, default `1`
- `CANVAS_OUTPUT_DIR`, default `output/drawings`

Do not commit API keys. `.env` and `.canvas-draw-image.env` are ignored by Git.

### Portable config file

Works across shells:

```bash
cat > ~/.canvas-draw-image.env <<'EOF'
CANVAS_API_KEY="your-api-key"
CANVAS_BASE_URL="https://example.com/v1"
CANVAS_IMAGE_MODEL="gpt-image-2"
EOF
```

### Bash or zsh

Add to `~/.bashrc`, `~/.bash_profile`, `~/.zshrc`, `~/.zprofile`, or `~/.profile`:

```bash
export CANVAS_API_KEY="your-api-key"
export CANVAS_BASE_URL="https://example.com/v1"
export CANVAS_IMAGE_MODEL="gpt-image-2"
```

### fish

```fish
set -Ux CANVAS_API_KEY "your-api-key"
set -Ux CANVAS_BASE_URL "https://example.com/v1"
set -Ux CANVAS_IMAGE_MODEL "gpt-image-2"
```

### PowerShell

```powershell
[Environment]::SetEnvironmentVariable("CANVAS_API_KEY", "your-api-key", "User")
[Environment]::SetEnvironmentVariable("CANVAS_BASE_URL", "https://example.com/v1", "User")
[Environment]::SetEnvironmentVariable("CANVAS_IMAGE_MODEL", "gpt-image-2", "User")
```

Restart the terminal after setting persistent environment variables.

## Use With Codex

Ask Codex:

```text
Use $canvas-draw-image to generate an image of a white mechanical cat in a cyberpunk city.
```

The skill runs:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py "prompt text"
```

## Use Directly

Generate one image:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --count 1 --size 1:1 "a blue circle on a white background"
```

Generate multiple images:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --count 3 --size 16:9 "rainy futuristic city street"
```

Edit using a reference image:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --reference ./reference.png "turn this into a watercolor illustration"
```

Use a mask:

```bash
python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --reference ./source.png --mask ./mask.png "only change the transparent masked area"
```

## Output

The script prints saved image paths. Verify a result with:

```bash
file output/drawings/*.png
```

## TLS Notes

If Python reports `CERTIFICATE_VERIFY_FAILED`, fix the local Python CA setup instead of relying on insecure requests.

For python.org macOS builds, run the bundled certificate installer, for example:

```bash
/Applications/Python\ 3.14/Install\ Certificates.command
```

`--insecure` exists only for temporary diagnostics.
