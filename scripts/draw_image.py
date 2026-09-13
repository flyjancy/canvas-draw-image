#!/usr/bin/env python3
"""Standalone OpenAI-compatible image generation helper.

The web app proxies image requests through Next.js. This script calls the same
upstream endpoints directly, so it can run outside the project environment.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import mimetypes
import os
import re
import shlex
import ssl
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


QUALITY_BASE = {
    "low": 1024,
    "medium": 2048,
    "high": 2880,
    "standard": 1024,
    "hd": 2048,
}
QUALITY_ALIASES = {"1k": "low", "2k": "medium", "4k": "high"}
DEFAULT_IMAGE_SHORT_SIDE = 1024
IMAGE_SIZE_STEP = 16
IMAGE_MIN_PIXELS = 655360
IMAGE_MAX_PIXELS = 8294400
IMAGE_MAX_EDGE = 3840
IMAGE_MAX_RATIO = 3
DEFAULT_BASE_URL = "https://api.openai.com"
DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_USER_AGENT = "curl/8.7.1"
USER_CONFIG_FILE = Path.home() / ".canvas-draw-image.env"
CONFIG_FILE_NAMES = (".canvas-draw-image.env", ".env")
PROFILE_FILE_NAMES = (
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".profile",
    ".config/fish/config.fish",
    "Documents/PowerShell/Microsoft.PowerShell_profile.ps1",
    "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
)


class ImageApiError(RuntimeError):
    pass


def env_value(name: str, fallback: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    return local_config_value(name) or shell_profile_value(name) or fallback


def local_config_value(name: str) -> str | None:
    candidates = [Path.cwd() / file_name for file_name in CONFIG_FILE_NAMES]
    candidates.extend(Path.home() / file_name for file_name in CONFIG_FILE_NAMES)
    return first_profile_value(name, candidates)


def shell_profile_value(name: str) -> str | None:
    return first_profile_value(name, [Path.home() / file_name for file_name in PROFILE_FILE_NAMES])


def first_profile_value(name: str, paths: list[Path]) -> str | None:
    seen: set[Path] = set()
    for profile in paths:
        try:
            resolved = profile.expanduser().resolve()
        except OSError:
            resolved = profile.expanduser()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        value = read_profile_value(resolved, name)
        if value:
            return value
    return None


def read_profile_value(path: Path, name: str) -> str | None:
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for parser in (parse_posix_assignment, parse_fish_assignment, parse_powershell_assignment):
            value = parser(line, name)
            if value:
                return value
    return None


def parse_posix_assignment(line: str, name: str) -> str | None:
    try:
        parts = shlex.split(line, comments=True, posix=True)
    except ValueError:
        return None
    if not parts:
        return None
    if parts[0] == "export":
        parts = parts[1:]
    for part in parts:
        if part.startswith(f"{name}="):
            return part.split("=", 1)[1] or None
    return None


def parse_fish_assignment(line: str, name: str) -> str | None:
    try:
        parts = shlex.split(line, comments=True, posix=True)
    except ValueError:
        return None
    if len(parts) < 3 or parts[0] != "set":
        return None
    values = [part for part in parts[1:] if not part.startswith("-")]
    if len(values) < 2 or values[0] != name:
        return None
    return " ".join(values[1:]) or None


def parse_powershell_assignment(line: str, name: str) -> str | None:
    match = re.match(rf"^\$env:{re.escape(name)}\s*=\s*(.+?)\s*$", line, re.I)
    if not match:
        return None
    value = match.group(1).strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1] or None
    return value or None


def setup_config(args: argparse.Namespace, *, require_missing: bool = False) -> None:
    if not sys.stdin.isatty():
        raise ImageApiError(
            "Missing image API configuration. Run this once in a terminal:\n"
            "  python3 ~/.codex/skills/canvas-draw-image/scripts/draw_image.py --setup\n"
            "or create ~/.canvas-draw-image.env with CANVAS_API_KEY and CANVAS_BASE_URL."
        )

    print("First-time Canvas Draw Image setup")
    print(f"Config will be saved to: {USER_CONFIG_FILE}")
    api_key = prompt_secret("API key", args.api_key, required=True)
    base_url = prompt_text("Base URL", args.base_url or DEFAULT_BASE_URL, required=True)
    model = prompt_text("Image model", args.model or DEFAULT_MODEL, required=True)
    write_user_config(
        {
            "CANVAS_API_KEY": api_key,
            "CANVAS_BASE_URL": base_url.rstrip("/"),
            "CANVAS_IMAGE_MODEL": model,
        }
    )
    args.api_key = api_key
    args.base_url = base_url.rstrip("/")
    args.model = model
    if require_missing:
        print("Configuration saved. Continuing with the current request.")
    else:
        print("Configuration saved.")


def prompt_secret(label: str, current: str | None, *, required: bool) -> str:
    suffix = " [press Enter to keep existing]" if current else ""
    while True:
        value = getpass.getpass(f"{label}{suffix}: ").strip()
        if value:
            return value
        if current:
            return current
        if not required:
            return ""
        print(f"{label} is required.")


def prompt_text(label: str, current: str | None, *, required: bool) -> str:
    default = current or ""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip() or default
        if value:
            return value
        if not required:
            return ""
        print(f"{label} is required.")


def write_user_config(updates: dict[str, str]) -> None:
    USER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = USER_CONFIG_FILE.read_text(errors="ignore").splitlines() if USER_CONFIG_FILE.exists() else []
    pending = dict(updates)
    next_lines: list[str] = []
    assignment = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]+)=")
    for line in lines:
        match = assignment.match(line)
        key = match.group(1) if match else ""
        if key in pending:
            next_lines.append(format_env_assignment(key, pending.pop(key)))
        else:
            next_lines.append(line)
    if next_lines and next_lines[-1].strip():
        next_lines.append("")
    next_lines.extend(format_env_assignment(key, value) for key, value in pending.items())
    USER_CONFIG_FILE.write_text("\n".join(next_lines).rstrip() + "\n")
    USER_CONFIG_FILE.chmod(0o600)


def format_env_assignment(key: str, value: str) -> str:
    return f"{key}={shlex.quote(value)}"


def normalize_quality(value: str) -> str | None:
    quality = value.strip().lower()
    quality = QUALITY_ALIASES.get(quality, quality)
    return quality if quality in QUALITY_BASE else None


def parse_image_ratio(value: str) -> tuple[float, float]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("Use size auto, WIDTHxHEIGHT, or a ratio like 9:16.")
    width = float(parts[0])
    height = float(parts[1])
    if width <= 0 or height <= 0:
        raise ValueError("Image ratio values must be positive.")
    if max(width, height) / min(width, height) > IMAGE_MAX_RATIO:
        raise ValueError("Image aspect ratio cannot exceed 3:1.")
    return width, height


def parse_image_dimensions(value: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)x(\d+)$", value, re.I)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def validate_image_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive integers.")
    if width % IMAGE_SIZE_STEP or height % IMAGE_SIZE_STEP:
        raise ValueError("Image width and height must be multiples of 16.")
    if max(width, height) > IMAGE_MAX_EDGE:
        raise ValueError("Image longest edge cannot exceed 3840 px.")
    if max(width, height) / min(width, height) > IMAGE_MAX_RATIO:
        raise ValueError("Image aspect ratio cannot exceed 3:1.")
    pixels = width * height
    if pixels < IMAGE_MIN_PIXELS or pixels > IMAGE_MAX_PIXELS:
        raise ValueError("Image pixels must be between 655360 and 8294400.")


def resolve_size(quality: str | None, ratio: str) -> str:
    ratio_width, ratio_height = parse_image_ratio(ratio)
    base_pixels = QUALITY_BASE.get(quality or "")
    is_landscape = ratio_width >= ratio_height
    long_ratio = ratio_width / ratio_height if is_landscape else ratio_height / ratio_width
    if base_pixels:
        target_pixels = base_pixels * base_pixels
        long_side_raw = (target_pixels * long_ratio) ** 0.5
        long_side = int(long_side_raw // IMAGE_SIZE_STEP * IMAGE_SIZE_STEP)
        short_side = round(long_side / long_ratio / IMAGE_SIZE_STEP) * IMAGE_SIZE_STEP
    else:
        short_side = DEFAULT_IMAGE_SHORT_SIDE
        long_side = round(short_side * long_ratio / IMAGE_SIZE_STEP) * IMAGE_SIZE_STEP

    width = long_side if is_landscape else short_side
    height = short_side if is_landscape else long_side
    validate_image_size(width, height)
    return f"{width}x{height}"


def resolve_request_size(quality: str | None, size: str) -> str | None:
    value = size.strip()
    if not value or value.lower() == "auto":
        return None
    dimensions = parse_image_dimensions(value)
    if dimensions:
        validate_image_size(*dimensions)
        return f"{dimensions[0]}x{dimensions[1]}"
    if ":" in value:
        return resolve_size(quality, value)
    raise ValueError("Unsupported size. Use auto, WIDTHxHEIGHT, or a ratio like 9:16.")


def build_api_url(base_url: str, path: str) -> str:
    normalized = normalize_ark_plan_base_url(base_url.strip().rstrip("/"))
    lower = normalized.lower()
    if lower.endswith("/v1") or lower.endswith("/api/v3") or lower.endswith("/api/plan/v3"):
        api_base_url = normalized
    else:
        api_base_url = f"{normalized}/v1"
    return f"{api_base_url}{path}"


def normalize_ark_plan_base_url(base_url: str) -> str:
    try:
        parsed = urlparse(base_url)
        path = parsed.path.rstrip("/")
        lower_path = path.lower()
        marker = "/api/plan/v3"
        index = lower_path.find(marker)
        if index < 0:
            return base_url
        end = index + len(marker)
        if len(lower_path) != end and lower_path[end] != "/":
            return base_url
        return parsed._replace(path=path[:end], query="", fragment="").geturl().rstrip("/")
    except Exception:
        return base_url


def read_json_response(response: Any) -> dict[str, Any]:
    text = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImageApiError(text[:500] or "The API did not return JSON.") from exc


def extract_error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    response = payload.get("response")
    response_error = response.get("error") if isinstance(response, dict) else None
    for value in (
        payload.get("msg"),
        error.get("message") if isinstance(error, dict) else None,
        response_error.get("message") if isinstance(response_error, dict) else None,
    ):
        if isinstance(value, str) and value:
            return value
    return ""


def raise_for_api_error(payload: dict[str, Any]) -> None:
    if isinstance(payload.get("code"), int) and payload["code"] != 0:
        raise ImageApiError(str(payload.get("msg") or "Request failed."))
    message = extract_error_message(payload)
    if message:
        raise ImageApiError(message)


def ssl_context(insecure: bool) -> ssl.SSLContext | None:
    if not insecure:
        return None
    return ssl._create_unverified_context()


def post_json(url: str, api_key: str, payload: dict[str, Any], timeout: int, insecure: bool) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    return request_json(request, timeout, insecure)


def post_multipart(
    url: str,
    api_key: str,
    fields: dict[str, str],
    files: list[tuple[str, Path]],
    timeout: int,
    insecure: bool,
) -> dict[str, Any]:
    boundary = f"----canvas-python-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    for field_name, path in files:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode()
        )
        body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode())
        body.extend(path.read_bytes())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    request = Request(
        url,
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    return request_json(request, timeout, insecure)


def request_json(request: Request, timeout: int, insecure: bool) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout, context=ssl_context(insecure)) as response:
            payload = read_json_response(response)
            raise_for_api_error(payload)
            return payload
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            payload = {}
        message = extract_error_message(payload) or status_error(exc.code)
        raise ImageApiError(message) from exc
    except URLError as exc:
        raise ImageApiError(str(exc.reason or exc)) from exc


def status_error(status: int) -> str:
    if status in (401, 403):
        return "Authentication failed. Check API key, plan, or model permission."
    if status == 429:
        return "Rate limited or quota is not enough."
    return f"Request failed: HTTP {status}"


def prompt_with_system(system_prompt: str, prompt: str) -> str:
    system = system_prompt.strip()
    return f"{system}\n\n{prompt}" if system else prompt


def prompt_with_references(prompt: str, references: list[Path]) -> str:
    text = prompt.strip()
    if not references:
        return text
    labels = "、".join(f"Image {index + 1}" for index in range(len(references)))
    return f"Reference image labels: {labels}. Understand image references by these labels.\n\n{text}"


def build_generation_payload(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    quality = normalize_quality(args.quality)
    size = resolve_request_size(quality, args.size)
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt_with_system(args.system_prompt, prompt),
        "n": args.count,
        "response_format": "b64_json",
        "output_format": args.output_format,
    }
    if quality:
        payload["quality"] = quality
    if size:
        payload["size"] = size
    return payload


def build_edit_fields(args: argparse.Namespace, prompt: str, references: list[Path]) -> dict[str, str]:
    payload = build_generation_payload(args, prompt_with_references(prompt, references))
    return {key: str(value) for key, value in payload.items()}


def parse_images(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ImageApiError("The API response does not contain data[].")
    images: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        b64_json = item.get("b64_json")
        url = item.get("url") or item.get("image_url")
        if isinstance(b64_json, str) and b64_json:
            images.append(f"data:image/png;base64,{b64_json}")
        elif isinstance(url, str) and url:
            images.append(url)
    if not images:
        raise ImageApiError("The API did not return any image data.")
    return images


def save_images(images: list[str], output_dir: Path, output_format: str, prompt: str, timeout: int, insecure: bool) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(prompt) or "image"
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    paths: list[Path] = []
    for index, image in enumerate(images, start=1):
        if image.startswith("data:"):
            ext, content = split_data_url(image, output_format)
            data = base64.b64decode(content)
        else:
            ext, data = download_image(image, output_format, timeout, insecure)
        path = output_dir / f"{timestamp}-{slug}-{index}.{ext}"
        path.write_bytes(data)
        paths.append(path)
    return paths


def split_data_url(data_url: str, fallback_ext: str) -> tuple[str, str]:
    match = re.match(r"^data:([^;,]+);base64,(.+)$", data_url, re.S)
    if not match:
        raise ImageApiError("Invalid data URL returned by API.")
    mime_type = match.group(1)
    ext = mimetypes.guess_extension(mime_type) or f".{fallback_ext}"
    return ext.lstrip(".").replace("jpeg", "jpg"), match.group(2)


def download_image(url: str, fallback_ext: str, timeout: int, insecure: bool) -> tuple[str, bytes]:
    request = Request(url, headers={"User-Agent": "canvas-draw-image/1.0"})
    with urlopen(request, timeout=timeout, context=ssl_context(insecure)) as response:
        data = response.read()
        content_type = response.headers.get("content-type", "")
    ext = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or Path(urlparse(url).path).suffix
    return (ext.lstrip(".") or fallback_ext).replace("jpeg", "jpg"), data


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return slug[:48]


def existing_paths(paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for value in paths:
        path = Path(value).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Reference file not found: {path}")
        result.append(path)
    return result


def run_once(args: argparse.Namespace, prompt: str) -> list[Path]:
    references = existing_paths(args.reference)
    mask = Path(args.mask).expanduser() if args.mask else None
    if mask and not mask.is_file():
        raise FileNotFoundError(f"Mask file not found: {mask}")

    if references:
        url = build_api_url(args.base_url, "/images/edits")
        fields = build_edit_fields(args, prompt, references)
        files = [("image", path) for path in references]
        if mask:
            files.append(("mask", mask))
        payload = post_multipart(url, args.api_key, fields, files, args.timeout, args.insecure)
    else:
        url = build_api_url(args.base_url, "/images/generations")
        payload = post_json(url, args.api_key, build_generation_payload(args, prompt), args.timeout, args.insecure)

    return save_images(parse_images(payload), args.output_dir, args.output_format, prompt, args.timeout, args.insecure)


def read_prompt_loop() -> str | None:
    try:
        value = input("Prompt (blank to quit): ").strip()
    except EOFError:
        return None
    return value or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or edit images through an OpenAI-compatible image API.")
    parser.add_argument("prompt_arg", nargs="*", help="Prompt text. If omitted, enter interactive mode.")
    parser.add_argument("-p", "--prompt", help="Prompt text. Overrides positional prompt.")
    parser.add_argument("--setup", action="store_true", help="Prompt for API key, base URL, and model, then save them to ~/.canvas-draw-image.env.")
    parser.add_argument("--api-key", default=env_value("CANVAS_API_KEY") or env_value("OPENAI_API_KEY"), help="API key. Defaults to CANVAS_API_KEY or OPENAI_API_KEY.")
    parser.add_argument("--base-url", default=env_value("CANVAS_BASE_URL") or env_value("OPENAI_BASE_URL") or DEFAULT_BASE_URL, help="API base URL.")
    parser.add_argument("--model", default=env_value("CANVAS_IMAGE_MODEL", DEFAULT_MODEL), help="Image model name.")
    parser.add_argument("--quality", default=env_value("CANVAS_IMAGE_QUALITY", "auto"), help="auto, low, medium, high, standard, hd, 1k, 2k, or 4k.")
    parser.add_argument("--size", default=env_value("CANVAS_IMAGE_SIZE", "1:1"), help="auto, WIDTHxHEIGHT, or ratio such as 1:1, 16:9, 9:16.")
    parser.add_argument("--count", type=int, default=int(env_value("CANVAS_IMAGE_COUNT", "1") or "1"), help="Number of images, 1-15.")
    parser.add_argument("--reference", action="append", default=[], help="Reference image path. Repeat for multiple images; switches to /images/edits.")
    parser.add_argument("--mask", help="Optional mask image path for edits.")
    parser.add_argument("--system-prompt", default=env_value("CANVAS_SYSTEM_PROMPT", ""), help="Optional text prepended to each prompt.")
    parser.add_argument("--output-format", default=env_value("CANVAS_IMAGE_FORMAT", DEFAULT_OUTPUT_FORMAT), help="Requested output format, usually png.")
    parser.add_argument("--output-dir", type=Path, default=Path(env_value("CANVAS_OUTPUT_DIR", "output/drawings") or "output/drawings"), help="Directory for saved images.")
    parser.add_argument("--timeout", type=int, default=int(env_value("CANVAS_TIMEOUT", "180") or "180"), help="HTTP timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification. Use only for diagnosing broken local certificates.")
    args = parser.parse_args()
    args.count = max(1, min(15, abs(args.count)))
    return args


def main() -> int:
    args = parse_args()
    if args.setup:
        setup_config(args)
        if not args.prompt and not args.prompt_arg:
            return 0
    prompt = args.prompt or " ".join(args.prompt_arg).strip()
    prompts = [prompt] if prompt else []
    if not args.api_key:
        try:
            setup_config(args, require_missing=True)
        except ImageApiError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    if not prompts:
        print("Interactive mode. Existing --reference files apply to every prompt.")
        while True:
            next_prompt = read_prompt_loop()
            if not next_prompt:
                break
            try:
                paths = run_once(args, next_prompt)
                for path in paths:
                    print(path)
            except (ImageApiError, OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
        return 0

    try:
        paths = run_once(args, prompts[0])
    except (ImageApiError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
