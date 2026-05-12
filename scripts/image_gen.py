#!/usr/bin/env python3
"""Fallback CLI for explicit image generation or editing with GPT Image models.

Used only when the user explicitly opts into CLI fallback mode, or when explicit
transparent output requires the `gpt-image-1.5` fallback path.

Defaults to gpt-image-2 and a structured prompt augmentation workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest

from io import BytesIO

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None  # type: ignore[assignment]

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_RESPONSES_MODEL = "gpt-5.4-mini"
DEFAULT_RESPONSES_REASONING_EFFORT = "high"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "medium"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_CONCURRENCY = 5
DEFAULT_DOWNSCALE_SUFFIX = "-web"
DEFAULT_OUTPUT_DIR = "output/imagegen"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
GPT_IMAGE_MODEL_PREFIX = "gpt-image-"

ALLOWED_LEGACY_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
ALLOWED_QUALITIES = {"low", "medium", "high", "auto"}
ALLOWED_BACKGROUNDS = {"transparent", "opaque", "auto", None}
ALLOWED_INPUT_FIDELITIES = {"low", "high", None}
ALLOWED_INPUT_DETAILS = {"low", "high", "auto", None}
ALLOWED_RESPONSES_ACTIONS = {"auto", "generate", "edit", None}
ALLOWED_REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}

GPT_IMAGE_2_MODEL = "gpt-image-2"
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_EDGE = 3840
GPT_IMAGE_2_MAX_RATIO = 3.0

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_BATCH_JOBS = 500


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _uuid_filename(output_format: str) -> str:
    return f"{uuid.uuid4()}.{output_format}"


def _looks_like_directory_path(path_text: str) -> bool:
    return path_text.endswith(("/", "\\"))


@dataclass(frozen=True)
class OpenAIConnection:
    api_key: Optional[str]
    auth_source: Optional[str]
    base_url: Optional[str]
    config_model: Optional[str]
    config_reasoning_effort: Optional[str]
    auth_path: Optional[Path]
    config_path: Optional[Path]


class ImageApiRequestError(Exception):
    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def _non_empty_string(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _candidate_codex_dirs() -> List[Path]:
    candidates: List[Path] = []
    codex_home = os.getenv("CODEX_HOME")
    if codex_home:
        candidates.append(Path(codex_home).expanduser())
    candidates.extend([Path.cwd() / ".codex", Path.home() / ".codex"])

    seen: set[str] = set()
    unique: List[Path] = []
    for path in candidates:
        key = str(path.expanduser()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path.expanduser())
    return unique


def _first_existing_codex_file(filename: str) -> Optional[Path]:
    for directory in _candidate_codex_dirs():
        path = directory / filename
        if path.exists() and path.is_file():
            return path
    return None


def _parse_simple_toml(text: str) -> Dict[str, Any]:
    """Parse the simple Codex config shape needed when tomllib is unavailable."""
    root: Dict[str, Any] = {}
    current = root

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = root
            for part in line[1:-1].split("."):
                part = part.strip().strip('"').strip("'")
                current = current.setdefault(part, {})
            continue
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if (
            len(raw_value) >= 2
            and raw_value[0] == raw_value[-1]
            and raw_value[0] in {'"', "'"}
        ):
            value: Any = raw_value[1:-1]
        elif raw_value.lower() in {"true", "false"}:
            value = raw_value.lower() == "true"
        else:
            value = raw_value
        current[key] = value

    return root


def _read_codex_config(path: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
        if tomllib is not None:
            data = tomllib.loads(raw)
        else:
            data = _parse_simple_toml(raw)
    except Exception as exc:
        _warn(f"Could not parse Codex config {path}: {exc}")
        return None, None, None

    provider_name = _non_empty_string(data.get("model_provider")) or "OpenAI"
    providers = data.get("model_providers")
    provider: Any = None
    if isinstance(providers, dict):
        provider = providers.get(provider_name) or providers.get("OpenAI")

    base_url = None
    if isinstance(provider, dict):
        base_url = _non_empty_string(provider.get("base_url"))

    model = _non_empty_string(data.get("model"))
    reasoning_effort = _non_empty_string(data.get("model_reasoning_effort"))
    if reasoning_effort and reasoning_effort not in ALLOWED_REASONING_EFFORTS:
        _warn(
            f"Ignoring unsupported model_reasoning_effort={reasoning_effort!r} in {path}; "
            f"using {DEFAULT_RESPONSES_REASONING_EFFORT}."
        )
        reasoning_effort = None
    return base_url, model, reasoning_effort


def _read_auth_file(path: Path) -> Tuple[Optional[str], Optional[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _warn(f"Could not parse Codex auth file {path}: {exc}")
        return None, None

    if isinstance(data, dict):
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            access_token = _non_empty_string(tokens.get("access_token"))
            if access_token:
                return access_token, f"{path}:tokens.access_token"

        api_key = _non_empty_string(data.get("OPENAI_API_KEY"))
        if api_key:
            return api_key, f"{path}:OPENAI_API_KEY"

    return None, None


def _resolve_openai_connection(*, dry_run: bool = False) -> OpenAIConnection:
    auth_path = _first_existing_codex_file("auth.json")
    config_path = _first_existing_codex_file("config.toml")

    api_key = None
    auth_source = None
    if auth_path:
        api_key, auth_source = _read_auth_file(auth_path)

    if not api_key:
        api_key = _non_empty_string(os.getenv("OPENAI_API_KEY"))
        if api_key:
            auth_source = "env:OPENAI_API_KEY"

    if not api_key and not dry_run:
        searched = ", ".join(str(p / "auth.json") for p in _candidate_codex_dirs())
        _die(
            "No usable OpenAI credential found. Looked for tokens.access_token or "
            f"OPENAI_API_KEY in auth.json ({searched}), then env:OPENAI_API_KEY."
        )
    if not api_key and dry_run:
        _warn("No OpenAI credential found; continuing because this is a dry-run.")

    base_url = None
    config_model = None
    config_reasoning_effort = None
    if config_path:
        base_url, config_model, config_reasoning_effort = _read_codex_config(config_path)

    return OpenAIConnection(
        api_key=api_key,
        auth_source=auth_source,
        base_url=base_url,
        config_model=config_model,
        config_reasoning_effort=config_reasoning_effort,
        auth_path=auth_path,
        config_path=config_path,
    )


def _effective_base_url(base_url: Optional[str]) -> str:
    return (base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/")


def _responses_endpoint(base_url: Optional[str]) -> str:
    return f"{_effective_base_url(base_url)}/responses"


def _api_endpoint(base_url: Optional[str], path: str) -> str:
    return f"{_effective_base_url(base_url)}/{path.lstrip('/')}"


def _dependency_hint(package: str, *, upgrade: bool = False) -> str:
    command = f"uv pip install {'-U ' if upgrade else ''}{package}"
    return (
        "Activate the repo-selected environment first, then install it with "
        f"`{command}`. If this repo uses a local virtualenv, start with "
        "`source .venv/bin/activate`; otherwise use this repo's configured shared fallback "
        "environment. If your project declares dependencies, prefer that project's normal "
        "`uv sync` flow."
    )


def _ensure_api_key(dry_run: bool) -> None:
    connection = _resolve_openai_connection(dry_run=dry_run)
    if connection.auth_source:
        print(f"OpenAI credential source: {connection.auth_source}", file=sys.stderr)


def _read_prompt(prompt: Optional[str], prompt_file: Optional[str]) -> str:
    if prompt and prompt_file:
        _die("Use --prompt or --prompt-file, not both.")
    if prompt_file:
        path = Path(prompt_file)
        if not path.exists():
            _die(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()
    if prompt:
        return prompt.strip()
    _die("Missing prompt. Use --prompt or --prompt-file.")
    return ""  # unreachable


def _check_image_paths(paths: Iterable[str]) -> List[Path]:
    resolved: List[Path] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            _die(f"Image file not found: {path}")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            _warn(f"Image exceeds 50MB limit: {path}")
        resolved.append(path)
    return resolved


def _detect_image_mime(path: Path, data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"

    guessed, _ = mimetypes.guess_type(str(path))
    if guessed in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return guessed

    _die(f"Unsupported image type for Responses input: {path}")
    return "application/octet-stream"  # unreachable


def _image_path_to_data_url(path: Path) -> str:
    data = path.read_bytes()
    mime_type = _detect_image_mime(path, data)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _responses_input_image_items(args: argparse.Namespace) -> List[Dict[str, Any]]:
    detail = getattr(args, "input_detail", None)
    if detail not in ALLOWED_INPUT_DETAILS:
        _die("--input-detail must be one of low, high, or auto.")

    items: List[Dict[str, Any]] = []

    for path in _check_image_paths(getattr(args, "input_image", None) or []):
        item: Dict[str, Any] = {
            "type": "input_image",
            "image_url": _image_path_to_data_url(path),
        }
        if detail:
            item["detail"] = detail
        items.append(item)

    for image_url in getattr(args, "input_image_url", None) or []:
        image_url = image_url.strip()
        if not image_url:
            _die("--input-image-url cannot be empty.")
        item = {
            "type": "input_image",
            "image_url": image_url,
        }
        if detail:
            item["detail"] = detail
        items.append(item)

    for file_id in getattr(args, "input_file_id", None) or []:
        file_id = file_id.strip()
        if not file_id:
            _die("--input-file-id cannot be empty.")
        item = {
            "type": "input_image",
            "file_id": file_id,
        }
        if detail:
            item["detail"] = detail
        items.append(item)

    return items


def _has_responses_input_images(args: argparse.Namespace) -> bool:
    return any(
        bool(getattr(args, attr, None))
        for attr in ("input_image", "input_image_url", "input_file_id")
    )


def _normalize_output_format(fmt: Optional[str]) -> str:
    if not fmt:
        return DEFAULT_OUTPUT_FORMAT
    fmt = fmt.lower()
    if fmt not in {"png", "jpeg", "jpg", "webp"}:
        _die("output-format must be png, jpeg, jpg, or webp.")
    return "jpeg" if fmt == "jpg" else fmt


def _parse_size(size: str) -> Optional[Tuple[int, int]]:
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _validate_gpt_image_2_size(size: str) -> None:
    if size == "auto":
        return

    parsed = _parse_size(size)
    if parsed is None:
        _die("size must be auto or WIDTHxHEIGHT, for example 1024x1024.")

    width, height = parsed
    max_edge = max(width, height)
    min_edge = min(width, height)
    total_pixels = width * height

    if max_edge > GPT_IMAGE_2_MAX_EDGE:
        _die("gpt-image-2 size maximum edge length must be less than or equal to 3840px.")
    if width % 16 != 0 or height % 16 != 0:
        _die("gpt-image-2 size width and height must be multiples of 16px.")
    if max_edge / min_edge > GPT_IMAGE_2_MAX_RATIO:
        _die("gpt-image-2 size long edge to short edge ratio must not exceed 3:1.")
    if total_pixels < GPT_IMAGE_2_MIN_PIXELS or total_pixels > GPT_IMAGE_2_MAX_PIXELS:
        _die(
            "gpt-image-2 size total pixels must be at least 655,360 and no more than 8,294,400."
        )


def _validate_size(size: str, model: str) -> None:
    if model == GPT_IMAGE_2_MODEL:
        _validate_gpt_image_2_size(size)
        return

    if size not in ALLOWED_LEGACY_SIZES:
        _die(
            "size must be one of 1024x1024, 1536x1024, 1024x1536, or auto for this GPT Image model."
        )


def _validate_quality(quality: str) -> None:
    if quality not in ALLOWED_QUALITIES:
        _die("quality must be one of low, medium, high, or auto.")


def _validate_background(background: Optional[str]) -> None:
    if background not in ALLOWED_BACKGROUNDS:
        _die("background must be one of transparent, opaque, or auto.")


def _validate_input_fidelity(input_fidelity: Optional[str]) -> None:
    if input_fidelity not in ALLOWED_INPUT_FIDELITIES:
        _die("input-fidelity must be one of low or high.")


def _validate_responses_options(args: argparse.Namespace) -> None:
    action = getattr(args, "responses_action", None)
    if action not in ALLOWED_RESPONSES_ACTIONS:
        _die("--responses-action must be one of auto, generate, or edit.")
    if action == "edit" and not _has_responses_input_images(args):
        _die("--responses-action edit requires --input-image, --input-image-url, or --input-file-id.")

    detail = getattr(args, "input_detail", None)
    if detail not in ALLOWED_INPUT_DETAILS:
        _die("--input-detail must be one of low, high, or auto.")


def _validate_model(model: str) -> None:
    if not model.startswith(GPT_IMAGE_MODEL_PREFIX):
        _die(
            "model must be a GPT Image model (for example gpt-image-1.5, gpt-image-1, or gpt-image-1-mini)."
        )


def _validate_transparency(background: Optional[str], output_format: str) -> None:
    if background == "transparent" and output_format not in {"png", "webp"}:
        _die("transparent background requires output-format png or webp.")


def _validate_model_specific_options(
    *,
    model: str,
    background: Optional[str],
    input_fidelity: Optional[str] = None,
) -> None:
    if model != GPT_IMAGE_2_MODEL:
        return
    if background == "transparent":
        _die(
            "transparent backgrounds are not supported in gpt-image-2, the latest model. "
            "Use --model gpt-image-1.5 --background transparent --output-format png instead."
        )
    if input_fidelity is not None:
        _die(
            "input_fidelity is not supported in gpt-image-2 because image inputs always use high fidelity for this model."
        )


def _image_model_arg(args: argparse.Namespace) -> str:
    return getattr(args, "model", None) or DEFAULT_MODEL


def _size_arg(args: argparse.Namespace) -> str:
    return getattr(args, "size", None) or DEFAULT_SIZE


def _quality_arg(args: argparse.Namespace) -> str:
    return getattr(args, "quality", None) or DEFAULT_QUALITY


def _responses_model_arg(
    args: argparse.Namespace,
    connection: Optional[OpenAIConnection] = None,
) -> str:
    responses_model = _non_empty_string(getattr(args, "responses_model", None))
    shared_model = _non_empty_string(getattr(args, "model", None))
    if responses_model and shared_model and responses_model != shared_model:
        _die("Use either --responses-model or --model for Responses mode, not both.")
    if responses_model:
        return responses_model
    if shared_model:
        return shared_model
    if connection and connection.config_model:
        return connection.config_model
    return DEFAULT_RESPONSES_MODEL


def _responses_reasoning_effort_arg(connection: Optional[OpenAIConnection] = None) -> str:
    if connection and connection.config_reasoning_effort:
        return connection.config_reasoning_effort
    return DEFAULT_RESPONSES_REASONING_EFFORT


def _validate_generate_payload(payload: Dict[str, Any]) -> None:
    model = str(payload.get("model", DEFAULT_MODEL))
    _validate_model(model)
    n = int(payload.get("n", 1))
    if n < 1 or n > 10:
        _die("n must be between 1 and 10")
    size = str(payload.get("size", DEFAULT_SIZE))
    quality = str(payload.get("quality", DEFAULT_QUALITY))
    background = payload.get("background")
    _validate_size(size, model)
    _validate_quality(quality)
    _validate_background(background)
    _validate_model_specific_options(model=model, background=background)
    oc = payload.get("output_compression")
    if oc is not None and not (0 <= int(oc) <= 100):
        _die("output_compression must be between 0 and 100")


def _build_output_paths(
    out: Optional[str],
    output_format: str,
    count: int,
    out_dir: Optional[str],
) -> List[Path]:
    ext = "." + output_format
    explicit_out = _non_empty_string(out)

    if out_dir:
        out_base = Path(out_dir)
        out_base.mkdir(parents=True, exist_ok=True)
        if explicit_out:
            base = Path(explicit_out)
            if base.suffix == "":
                base = base.with_suffix(ext)
            elif output_format and base.suffix.lstrip(".").lower() != output_format:
                _warn(
                    f"Output extension {base.suffix} does not match output-format {output_format}."
                )
            base = out_base / base.name
            if count == 1:
                return [base]
            return [
                base.with_name(f"{base.stem}-{i}{base.suffix}")
                for i in range(1, count + 1)
            ]
        return [out_base / _uuid_filename(output_format) for _ in range(count)]

    if not explicit_out:
        default_dir = Path(DEFAULT_OUTPUT_DIR)
        default_dir.mkdir(parents=True, exist_ok=True)
        return [default_dir / _uuid_filename(output_format) for _ in range(count)]

    if _looks_like_directory_path(explicit_out):
        out_base = Path(explicit_out)
        out_base.mkdir(parents=True, exist_ok=True)
        return [out_base / _uuid_filename(output_format) for _ in range(count)]

    out_path = Path(explicit_out)
    if out_path.exists() and out_path.is_dir():
        out_path.mkdir(parents=True, exist_ok=True)
        return [out_path / _uuid_filename(output_format) for _ in range(count)]

    if out_path.suffix == "":
        out_path = out_path.with_suffix(ext)
    elif output_format and out_path.suffix.lstrip(".").lower() != output_format:
        _warn(
            f"Output extension {out_path.suffix} does not match output-format {output_format}."
        )

    if count == 1:
        return [out_path]

    return [
        out_path.with_name(f"{out_path.stem}-{i}{out_path.suffix}")
        for i in range(1, count + 1)
    ]


def _augment_prompt(args: argparse.Namespace, prompt: str) -> str:
    fields = _fields_from_args(args)
    return _augment_prompt_fields(args.augment, prompt, fields)


def _augment_prompt_fields(augment: bool, prompt: str, fields: Dict[str, Optional[str]]) -> str:
    if not augment:
        return prompt

    sections: List[str] = []
    if fields.get("use_case"):
        sections.append(f"Use case: {fields['use_case']}")
    sections.append(f"Primary request: {prompt}")
    if fields.get("scene"):
        sections.append(f"Scene/background: {fields['scene']}")
    if fields.get("subject"):
        sections.append(f"Subject: {fields['subject']}")
    if fields.get("style"):
        sections.append(f"Style/medium: {fields['style']}")
    if fields.get("composition"):
        sections.append(f"Composition/framing: {fields['composition']}")
    if fields.get("lighting"):
        sections.append(f"Lighting/mood: {fields['lighting']}")
    if fields.get("palette"):
        sections.append(f"Color palette: {fields['palette']}")
    if fields.get("materials"):
        sections.append(f"Materials/textures: {fields['materials']}")
    if fields.get("text"):
        sections.append(f"Text (verbatim): \"{fields['text']}\"")
    if fields.get("constraints"):
        sections.append(f"Constraints: {fields['constraints']}")
    if fields.get("negative"):
        sections.append(f"Avoid: {fields['negative']}")

    return "\n".join(sections)


def _fields_from_args(args: argparse.Namespace) -> Dict[str, Optional[str]]:
    return {
        "use_case": getattr(args, "use_case", None),
        "scene": getattr(args, "scene", None),
        "subject": getattr(args, "subject", None),
        "style": getattr(args, "style", None),
        "composition": getattr(args, "composition", None),
        "lighting": getattr(args, "lighting", None),
        "palette": getattr(args, "palette", None),
        "materials": getattr(args, "materials", None),
        "text": getattr(args, "text", None),
        "constraints": getattr(args, "constraints", None),
        "negative": getattr(args, "negative", None),
    }


def _print_request(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _read_json_response(raw: bytes, *, context: str) -> Dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImageApiRequestError(
            f"{context} returned non-JSON response: {text[:1000]}"
        ) from exc
    if not isinstance(value, dict):
        raise ImageApiRequestError(f"{context} returned unexpected JSON type.")
    return value


def _http_json_post(
    connection: OpenAIConnection,
    path: str,
    payload: Dict[str, Any],
    *,
    context: str,
) -> Dict[str, Any]:
    if not connection.api_key:
        raise ImageApiRequestError(f"Missing OpenAI credential for {context}.")

    body = json.dumps(payload).encode("utf-8")
    request = urlrequest.Request(
        _api_endpoint(connection.base_url, path),
        data=body,
        headers={
            "Authorization": f"Bearer {connection.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlrequest.urlopen(request, timeout=600) as response:
            return _read_json_response(response.read(), context=context)
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise ImageApiRequestError(
            f"{context} failed with HTTP {exc.code}: {raw[:2000]}",
            status=exc.code,
        ) from exc
    except urlerror.URLError as exc:
        raise ImageApiRequestError(f"{context} request failed: {exc}") from exc


def _multipart_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _http_multipart_post(
    connection: OpenAIConnection,
    path: str,
    fields: Dict[str, Any],
    files: List[Tuple[str, Path, str, bytes]],
    *,
    context: str,
) -> Dict[str, Any]:
    if not connection.api_key:
        raise ImageApiRequestError(f"Missing OpenAI credential for {context}.")

    boundary = f"----imagegen6-{time.time_ns()}"
    body = bytearray()

    for name, value in fields.items():
        if value is None:
            continue
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(
            f'Content-Disposition: form-data; name="{_multipart_escape(name)}"\r\n\r\n'.encode(
                "utf-8"
            )
        )
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for field_name, path_obj, mime_type, data in files:
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{_multipart_escape(field_name)}"; '
                f'filename="{_multipart_escape(path_obj.name)}"\r\n'
            ).encode("utf-8")
        )
        body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("ascii"))
        body.extend(data)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("ascii"))

    request = urlrequest.Request(
        _api_endpoint(connection.base_url, path),
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {connection.api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlrequest.urlopen(request, timeout=600) as response:
            return _read_json_response(response.read(), context=context)
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise ImageApiRequestError(
            f"{context} failed with HTTP {exc.code}: {raw[:2000]}",
            status=exc.code,
        ) from exc
    except urlerror.URLError as exc:
        raise ImageApiRequestError(f"{context} request failed: {exc}") from exc


def _extract_image_b64_list(response: Dict[str, Any], *, context: str) -> List[str]:
    data = response.get("data")
    if not isinstance(data, list):
        raise ImageApiRequestError(f"{context} response did not include data[].")

    images: List[str] = []
    for item in data:
        if isinstance(item, dict):
            image_b64 = _non_empty_string(item.get("b64_json"))
            if image_b64:
                images.append(image_b64)

    if not images:
        raise ImageApiRequestError(f"{context} response did not include data[].b64_json.")
    return images


def _call_images_generation(
    connection: OpenAIConnection,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return _http_json_post(
        connection,
        "/images/generations",
        payload,
        context="Images generation API",
    )


def _call_images_edit(
    connection: OpenAIConnection,
    payload: Dict[str, Any],
    image_paths: List[Path],
    mask_path: Optional[Path],
) -> Dict[str, Any]:
    fields = dict(payload)
    files: List[Tuple[str, Path, str, bytes]] = []
    for path_obj in image_paths:
        data = path_obj.read_bytes()
        files.append(("image", path_obj, _detect_image_mime(path_obj, data), data))
    if mask_path:
        data = mask_path.read_bytes()
        files.append(("mask", mask_path, _detect_image_mime(mask_path, data), data))

    return _http_multipart_post(
        connection,
        "/images/edits",
        fields,
        files,
        context="Images edit API",
    )


def _decode_and_write(images: List[str], outputs: List[Path], force: bool) -> None:
    for idx, image_b64 in enumerate(images):
        if idx >= len(outputs):
            break
        out_path = outputs[idx]
        if out_path.exists() and not force:
            _die(f"Output already exists: {out_path} (use --force to overwrite)")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(image_b64))
        print(f"Wrote {out_path}")


def _derive_downscale_path(path: Path, suffix: str) -> Path:
    if suffix and not suffix.startswith("-") and not suffix.startswith("_"):
        suffix = "-" + suffix
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")


def _downscale_image_bytes(image_bytes: bytes, *, max_dim: int, output_format: str) -> bytes:
    try:
        from PIL import Image
    except Exception:
        _die(f"Downscaling requires Pillow. {_dependency_hint('pillow')}")

    if max_dim < 1:
        _die("--downscale-max-dim must be >= 1")

    with Image.open(BytesIO(image_bytes)) as img:
        img.load()
        w, h = img.size
        scale = min(1.0, float(max_dim) / float(max(w, h)))
        target = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))

        resized = img if target == (w, h) else img.resize(target, Image.Resampling.LANCZOS)

        fmt = output_format.lower()
        if fmt == "jpg":
            fmt = "jpeg"

        if fmt == "jpeg":
            if resized.mode in ("RGBA", "LA") or ("transparency" in getattr(resized, "info", {})):
                bg = Image.new("RGB", resized.size, (255, 255, 255))
                bg.paste(resized.convert("RGBA"), mask=resized.convert("RGBA").split()[-1])
                resized = bg
            else:
                resized = resized.convert("RGB")

        out = BytesIO()
        resized.save(out, format=fmt.upper())
        return out.getvalue()


def _decode_write_and_downscale(
    images: List[str],
    outputs: List[Path],
    *,
    force: bool,
    downscale_max_dim: Optional[int],
    downscale_suffix: str,
    output_format: str,
) -> None:
    for idx, image_b64 in enumerate(images):
        if idx >= len(outputs):
            break
        out_path = outputs[idx]
        if out_path.exists() and not force:
            _die(f"Output already exists: {out_path} (use --force to overwrite)")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        raw = base64.b64decode(image_b64)
        out_path.write_bytes(raw)
        print(f"Wrote {out_path}")

        if downscale_max_dim is None:
            continue

        derived = _derive_downscale_path(out_path, downscale_suffix)
        if derived.exists() and not force:
            _die(f"Output already exists: {derived} (use --force to overwrite)")
        derived.parent.mkdir(parents=True, exist_ok=True)
        resized = _downscale_image_bytes(raw, max_dim=downscale_max_dim, output_format=output_format)
        derived.write_bytes(resized)
        print(f"Wrote {derived}")


def _build_responses_image_payload(
    *,
    model: str,
    reasoning_effort: str,
    prompt: str,
    output_format: str,
    input_images: Optional[List[Dict[str, Any]]] = None,
    action: Optional[str] = None,
) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = [
        {
            "type": "input_text",
            "text": prompt,
        }
    ]
    content.extend(input_images or [])

    tool: Dict[str, Any] = {
        "type": "image_generation",
        "output_format": output_format,
    }
    if action:
        tool["action"] = action

    return {
        "model": model,
        "reasoning": {
            "effort": reasoning_effort,
        },
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": content,
            }
        ],
        "tools": [tool],
        "tool_choice": "auto",
        "stream": True,
        "store": False,
    }


def _iter_sse_data(byte_iter: Iterable[bytes]) -> Iterable[str]:
    data_lines: List[str] = []
    for raw_line in byte_iter:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield "\n".join(data_lines)


def _extract_image_generation_results(value: Any) -> List[str]:
    results: List[str] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "image_generation_call":
                result = _non_empty_string(node.get("result"))
                if result and result not in seen:
                    seen.add(result)
                    results.append(result)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return results


def _redact_large_data_urls(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, child in value.items():
            if (
                key == "image_url"
                and isinstance(child, str)
                and child.startswith("data:image/")
                and ";base64," in child
            ):
                prefix, encoded = child.split(",", 1)
                redacted[key] = f"{prefix},<omitted {len(encoded)} base64 chars>"
            else:
                redacted[key] = _redact_large_data_urls(child)
        return redacted
    if isinstance(value, list):
        return [_redact_large_data_urls(child) for child in value]
    return value


def _call_responses_image_generation(
    connection: OpenAIConnection,
    payload: Dict[str, Any],
) -> List[str]:
    if not connection.api_key:
        _die("Missing OpenAI credential for Responses API request.")

    body = json.dumps(payload).encode("utf-8")
    request = urlrequest.Request(
        _responses_endpoint(connection.base_url),
        data=body,
        headers={
            "Authorization": f"Bearer {connection.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    images: List[str] = []
    seen: set[str] = set()

    def add_results(values: Iterable[str]) -> None:
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            images.append(value)

    try:
        with urlrequest.urlopen(request, timeout=600) as response:
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                for data in _iter_sse_data(response):
                    if data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    add_results(_extract_image_generation_results(event))
            else:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    _die(f"Responses API returned non-JSON response: {raw[:1000]}")
                add_results(_extract_image_generation_results(obj))
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        _die(f"Responses API request failed with HTTP {exc.code}: {raw[:2000]}")
    except urlerror.URLError as exc:
        _die(f"Responses API request failed: {exc}")

    if not images:
        _die(
            "Responses API completed without an image_generation_call result. "
            "Verify the provider preserves the image_generation hosted tool."
        )
    return images


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:60] if value else "job"


def _normalize_job(job: Any, idx: int) -> Dict[str, Any]:
    if isinstance(job, str):
        prompt = job.strip()
        if not prompt:
            _die(f"Empty prompt at job {idx}")
        return {"prompt": prompt}
    if isinstance(job, dict):
        if "prompt" not in job or not str(job["prompt"]).strip():
            _die(f"Missing prompt for job {idx}")
        return job
    _die(f"Invalid job at index {idx}: expected string or object.")
    return {}  # unreachable


def _read_jobs_jsonl(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        _die(f"Input file not found: {p}")
    jobs: List[Dict[str, Any]] = []
    for line_no, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item: Any
            if line.startswith("{"):
                item = json.loads(line)
            else:
                item = line
            jobs.append(_normalize_job(item, idx=line_no))
        except json.JSONDecodeError as exc:
            _die(f"Invalid JSON on line {line_no}: {exc}")
    if not jobs:
        _die("No jobs found in input file.")
    if len(jobs) > MAX_BATCH_JOBS:
        _die(f"Too many jobs ({len(jobs)}). Max is {MAX_BATCH_JOBS}.")
    return jobs


def _merge_non_null(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(dst)
    for k, v in src.items():
        if v is not None:
            merged[k] = v
    return merged


def _job_output_paths(
    *,
    out_dir: Path,
    output_format: str,
    idx: int,
    prompt: str,
    n: int,
    explicit_out: Optional[str],
) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "." + output_format
    explicit_out = _non_empty_string(explicit_out)

    if explicit_out:
        base = Path(explicit_out)
        if base.suffix == "":
            base = base.with_suffix(ext)
        elif base.suffix.lstrip(".").lower() != output_format:
            _warn(
                f"Job {idx}: output extension {base.suffix} does not match output-format {output_format}."
            )
        base = out_dir / base.name
    else:
        base = out_dir / _uuid_filename(output_format)

    if n == 1:
        return [base]
    return [
        base.with_name(f"{base.stem}-{i}{base.suffix}")
        for i in range(1, n + 1)
    ]


def _extract_retry_after_seconds(exc: Exception) -> Optional[float]:
    # Best-effort: provider error shapes vary. Prefer a conservative fallback.
    for attr in ("retry_after", "retry_after_seconds"):
        val = getattr(exc, attr, None)
        if isinstance(val, (int, float)) and val >= 0:
            return float(val)
    msg = str(exc)
    m = re.search(r"retry[- ]after[:= ]+([0-9]+(?:\\.[0-9]+)?)", msg, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, ImageApiRequestError) and exc.status == 429:
        return True
    name = exc.__class__.__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _is_transient_error(exc: Exception) -> bool:
    if _is_rate_limit_error(exc):
        return True
    if isinstance(exc, ImageApiRequestError) and exc.status is not None:
        return exc.status in {408, 409} or exc.status >= 500
    name = exc.__class__.__name__.lower()
    if "timeout" in name or "timedout" in name or "tempor" in name:
        return True
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg or "connection reset" in msg


async def _generate_one_with_retries(
    connection: OpenAIConnection,
    payload: Dict[str, Any],
    *,
    attempts: int,
    job_label: str,
) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(_call_images_generation, connection, payload)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_error(exc):
                raise
            if attempt == attempts:
                raise
            sleep_s = _extract_retry_after_seconds(exc)
            if sleep_s is None:
                sleep_s = min(60.0, 2.0**attempt)
            print(
                f"{job_label} attempt {attempt}/{attempts} failed ({exc.__class__.__name__}); retrying in {sleep_s:.1f}s",
                file=sys.stderr,
            )
            await asyncio.sleep(sleep_s)
    raise last_exc or RuntimeError("unknown error")


async def _run_generate_batch(args: argparse.Namespace) -> int:
    jobs = _read_jobs_jsonl(args.input)
    out_dir = Path(args.out_dir)

    base_fields = _fields_from_args(args)
    base_payload = {
        "model": _image_model_arg(args),
        "n": args.n,
        "size": _size_arg(args),
        "quality": _quality_arg(args),
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "moderation": args.moderation,
    }

    if args.dry_run:
        for i, job in enumerate(jobs, start=1):
            prompt = str(job["prompt"]).strip()
            fields = _merge_non_null(base_fields, job.get("fields", {}))
            # Allow flat job keys as well (use_case, scene, etc.)
            fields = _merge_non_null(fields, {k: job.get(k) for k in base_fields.keys()})
            augmented = _augment_prompt_fields(args.augment, prompt, fields)

            job_payload = dict(base_payload)
            job_payload["prompt"] = augmented
            job_payload = _merge_non_null(job_payload, {k: job.get(k) for k in base_payload.keys()})
            job_payload = {k: v for k, v in job_payload.items() if v is not None}

            _validate_generate_payload(job_payload)
            effective_output_format = _normalize_output_format(job_payload.get("output_format"))
            _validate_transparency(job_payload.get("background"), effective_output_format)
            job_payload["output_format"] = effective_output_format

            n = int(job_payload.get("n", 1))
            outputs = _job_output_paths(
                out_dir=out_dir,
                output_format=effective_output_format,
                idx=i,
                prompt=prompt,
                n=n,
                explicit_out=job.get("out"),
            )
            downscaled = None
            if args.downscale_max_dim is not None:
                downscaled = [
                    str(_derive_downscale_path(p, args.downscale_suffix)) for p in outputs
                ]
            _print_request(
                {
                    "endpoint": "/v1/images/generations",
                    "job": i,
                    "outputs": [str(p) for p in outputs],
                    "outputs_downscaled": downscaled,
                    **job_payload,
                }
            )
        return 0

    connection = _resolve_openai_connection()
    sem = asyncio.Semaphore(args.concurrency)

    any_failed = False

    async def run_job(i: int, job: Dict[str, Any]) -> Tuple[int, Optional[str]]:
        nonlocal any_failed
        prompt = str(job["prompt"]).strip()
        job_label = f"[job {i}/{len(jobs)}]"

        fields = _merge_non_null(base_fields, job.get("fields", {}))
        fields = _merge_non_null(fields, {k: job.get(k) for k in base_fields.keys()})
        augmented = _augment_prompt_fields(args.augment, prompt, fields)

        payload = dict(base_payload)
        payload["prompt"] = augmented
        payload = _merge_non_null(payload, {k: job.get(k) for k in base_payload.keys()})
        payload = {k: v for k, v in payload.items() if v is not None}

        n = int(payload.get("n", 1))
        _validate_generate_payload(payload)
        effective_output_format = _normalize_output_format(payload.get("output_format"))
        _validate_transparency(payload.get("background"), effective_output_format)
        payload["output_format"] = effective_output_format
        outputs = _job_output_paths(
            out_dir=out_dir,
            output_format=effective_output_format,
            idx=i,
            prompt=prompt,
            n=n,
            explicit_out=job.get("out"),
        )
        try:
            async with sem:
                print(f"{job_label} starting", file=sys.stderr)
                started = time.time()
                result = await _generate_one_with_retries(
                    connection,
                    payload,
                    attempts=args.max_attempts,
                    job_label=job_label,
                )
                elapsed = time.time() - started
                print(f"{job_label} completed in {elapsed:.1f}s", file=sys.stderr)
            images = _extract_image_b64_list(result, context="Images generation API")
            _decode_write_and_downscale(
                images,
                outputs,
                force=args.force,
                downscale_max_dim=args.downscale_max_dim,
                downscale_suffix=args.downscale_suffix,
                output_format=effective_output_format,
            )
            return i, None
        except Exception as exc:
            any_failed = True
            print(f"{job_label} failed: {exc}", file=sys.stderr)
            if args.fail_fast:
                raise
            return i, str(exc)

    tasks = [asyncio.create_task(run_job(i, job)) for i, job in enumerate(jobs, start=1)]

    try:
        await asyncio.gather(*tasks)
    except Exception:
        for t in tasks:
            if not t.done():
                t.cancel()
        raise

    return 1 if any_failed else 0


def _generate_batch(args: argparse.Namespace) -> None:
    exit_code = asyncio.run(_run_generate_batch(args))
    if exit_code:
        raise SystemExit(exit_code)


def _responses_generate_options(args: argparse.Namespace) -> List[str]:
    unsupported: List[str] = []
    if getattr(args, "size", None):
        unsupported.append("--size")
    if getattr(args, "quality", None):
        unsupported.append("--quality")
    if getattr(args, "background", None):
        unsupported.append("--background")
    if getattr(args, "output_compression", None) is not None:
        unsupported.append("--output-compression")
    if getattr(args, "moderation", None):
        unsupported.append("--moderation")
    output_format = getattr(args, "output_format", None)
    if output_format and _normalize_output_format(output_format) != "png":
        unsupported.append("--output-format")
    return unsupported


def _responses_only_options(args: argparse.Namespace) -> List[str]:
    options: List[str] = []
    if _has_responses_input_images(args):
        options.append("--input-image/--input-image-url/--input-file-id")
    if getattr(args, "input_detail", None):
        options.append("--input-detail")
    if getattr(args, "responses_action", None):
        options.append("--responses-action")
    if getattr(args, "responses_model", None):
        options.append("--responses-model")
    return options


def _should_use_responses_generation(args: argparse.Namespace) -> bool:
    api = getattr(args, "api", "auto")
    if api == "responses":
        return True
    if api == "images":
        return False
    if _responses_only_options(args):
        return True
    explicit_model = _non_empty_string(getattr(args, "model", None))
    if explicit_model:
        return not explicit_model.startswith(GPT_IMAGE_MODEL_PREFIX)
    return not _responses_generate_options(args)


def _generate_via_responses(args: argparse.Namespace, prompt: str) -> None:
    unsupported = _responses_generate_options(args)
    if unsupported:
        _die(
            "Responses image_generation mode does not support "
            f"{', '.join(unsupported)}. Use --api images for those controls."
        )

    output_format = _normalize_output_format(args.output_format)
    output_paths = _build_output_paths(args.out, output_format, args.n, args.out_dir)
    downscaled = None
    if args.downscale_max_dim is not None:
        downscaled = [str(_derive_downscale_path(p, args.downscale_suffix)) for p in output_paths]

    connection = _resolve_openai_connection(dry_run=args.dry_run)
    model = _responses_model_arg(args, connection)
    reasoning_effort = _responses_reasoning_effort_arg(connection)
    input_images = _responses_input_image_items(args)
    payload = _build_responses_image_payload(
        model=model,
        reasoning_effort=reasoning_effort,
        prompt=prompt,
        output_format=output_format,
        input_images=input_images,
        action=args.responses_action,
    )

    if args.dry_run:
        _print_request(
            _redact_large_data_urls({
                "endpoint": _responses_endpoint(connection.base_url),
                "auth_source": connection.auth_source,
                "config": str(connection.config_path) if connection.config_path else None,
                "outputs": [str(p) for p in output_paths],
                "outputs_downscaled": downscaled,
                **payload,
            })
        )
        return

    print(
        "Calling Responses API with hosted image_generation tool. "
        "This can take up to a couple of minutes.",
        file=sys.stderr,
    )
    started = time.time()
    images: List[str] = []
    for index in range(args.n):
        if args.n > 1:
            print(f"Responses generation {index + 1}/{args.n}", file=sys.stderr)
        images.extend(_call_responses_image_generation(connection, payload))
        if len(images) >= args.n:
            break
    elapsed = time.time() - started
    print(f"Responses generation completed in {elapsed:.1f}s.", file=sys.stderr)

    _decode_write_and_downscale(
        images[: args.n],
        output_paths,
        force=args.force,
        downscale_max_dim=args.downscale_max_dim,
        downscale_suffix=args.downscale_suffix,
        output_format=output_format,
    )


def _generate(args: argparse.Namespace) -> None:
    prompt = _read_prompt(args.prompt, args.prompt_file)
    prompt = _augment_prompt(args, prompt)

    if _should_use_responses_generation(args):
        _generate_via_responses(args, prompt)
        return

    payload = {
        "model": _image_model_arg(args),
        "prompt": prompt,
        "n": args.n,
        "size": _size_arg(args),
        "quality": _quality_arg(args),
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "moderation": args.moderation,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    output_format = _normalize_output_format(args.output_format)
    _validate_transparency(args.background, output_format)
    payload["output_format"] = output_format
    output_paths = _build_output_paths(args.out, output_format, args.n, args.out_dir)
    downscaled = None
    if args.downscale_max_dim is not None:
        downscaled = [str(_derive_downscale_path(p, args.downscale_suffix)) for p in output_paths]

    if args.dry_run:
        _print_request(
            {
                "endpoint": "/v1/images/generations",
                "outputs": [str(p) for p in output_paths],
                "outputs_downscaled": downscaled,
                **payload,
            }
        )
        return

    print(
        "Calling Image API (generation). This can take up to a couple of minutes.",
        file=sys.stderr,
    )
    started = time.time()
    connection = _resolve_openai_connection()
    try:
        result = _call_images_generation(connection, payload)
        images = _extract_image_b64_list(result, context="Images generation API")
    except ImageApiRequestError as exc:
        _die(str(exc))
    elapsed = time.time() - started
    print(f"Generation completed in {elapsed:.1f}s.", file=sys.stderr)

    _decode_write_and_downscale(
        images,
        output_paths,
        force=args.force,
        downscale_max_dim=args.downscale_max_dim,
        downscale_suffix=args.downscale_suffix,
        output_format=output_format,
    )


def _edit(args: argparse.Namespace) -> None:
    prompt = _read_prompt(args.prompt, args.prompt_file)
    prompt = _augment_prompt(args, prompt)

    image_paths = _check_image_paths(args.image)
    mask_path = Path(args.mask) if args.mask else None
    if mask_path:
        if not mask_path.exists():
            _die(f"Mask file not found: {mask_path}")
        if mask_path.suffix.lower() != ".png":
            _warn(f"Mask should be a PNG with an alpha channel: {mask_path}")
        if mask_path.stat().st_size > MAX_IMAGE_BYTES:
            _warn(f"Mask exceeds 50MB limit: {mask_path}")

    payload = {
        "model": _image_model_arg(args),
        "prompt": prompt,
        "n": args.n,
        "size": _size_arg(args),
        "quality": _quality_arg(args),
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "input_fidelity": args.input_fidelity,
        "moderation": args.moderation,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    output_format = _normalize_output_format(args.output_format)
    _validate_transparency(args.background, output_format)
    payload["output_format"] = output_format
    _validate_input_fidelity(args.input_fidelity)
    output_paths = _build_output_paths(args.out, output_format, args.n, args.out_dir)
    downscaled = None
    if args.downscale_max_dim is not None:
        downscaled = [str(_derive_downscale_path(p, args.downscale_suffix)) for p in output_paths]

    if args.dry_run:
        payload_preview = dict(payload)
        payload_preview["image"] = [str(p) for p in image_paths]
        if mask_path:
            payload_preview["mask"] = str(mask_path)
        _print_request(
            {
                "endpoint": "/v1/images/edits",
                "outputs": [str(p) for p in output_paths],
                "outputs_downscaled": downscaled,
                **payload_preview,
            }
        )
        return

    print(
        f"Calling Image API (edit) with {len(image_paths)} image(s).",
        file=sys.stderr,
    )
    started = time.time()
    connection = _resolve_openai_connection()
    try:
        result = _call_images_edit(connection, payload, image_paths, mask_path)
        images = _extract_image_b64_list(result, context="Images edit API")
    except ImageApiRequestError as exc:
        _die(str(exc))

    elapsed = time.time() - started
    print(f"Edit completed in {elapsed:.1f}s.", file=sys.stderr)
    _decode_write_and_downscale(
        images,
        output_paths,
        force=args.force,
        downscale_max_dim=args.downscale_max_dim,
        downscale_suffix=args.downscale_suffix,
        output_format=output_format,
    )


def _open_files(paths: List[Path]):
    return _FileBundle(paths)


def _open_mask(mask_path: Optional[Path]):
    if mask_path is None:
        return _NullContext()
    return _SingleFile(mask_path)


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


class _SingleFile:
    def __init__(self, path: Path):
        self._path = path
        self._handle = None

    def __enter__(self):
        self._handle = self._path.open("rb")
        return self._handle

    def __exit__(self, exc_type, exc, tb):
        if self._handle:
            try:
                self._handle.close()
            except Exception:
                pass
        return False


class _FileBundle:
    def __init__(self, paths: List[Path]):
        self._paths = paths
        self._handles: List[object] = []

    def __enter__(self):
        self._handles = [p.open("rb") for p in self._paths]
        return self._handles

    def __exit__(self, exc_type, exc, tb):
        for handle in self._handles:
            try:
                handle.close()
            except Exception:
                pass
        return False


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--size")
    parser.add_argument("--quality")
    parser.add_argument("--background")
    parser.add_argument("--output-format")
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--moderation")
    parser.add_argument(
        "--out",
        help=f"Output filename or path. Defaults to a UUID filename under {DEFAULT_OUTPUT_DIR}/ when omitted.",
    )
    parser.add_argument(
        "--out-dir",
        help="Output directory. When no --out filename is provided, files are UUID-named.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--augment", dest="augment", action="store_true")
    parser.add_argument("--no-augment", dest="augment", action="store_false")
    parser.set_defaults(augment=True)

    # Prompt augmentation hints
    parser.add_argument("--use-case")
    parser.add_argument("--scene")
    parser.add_argument("--subject")
    parser.add_argument("--style")
    parser.add_argument("--composition")
    parser.add_argument("--lighting")
    parser.add_argument("--palette")
    parser.add_argument("--materials")
    parser.add_argument("--text")
    parser.add_argument("--constraints")
    parser.add_argument("--negative")

    # Post-processing (optional): generate an additional downscaled copy for fast web loading.
    parser.add_argument("--downscale-max-dim", type=int)
    parser.add_argument("--downscale-suffix", default=DEFAULT_DOWNSCALE_SUFFIX)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fallback CLI for explicit image generation or editing via GPT Image models"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser("generate", help="Create a new image")
    _add_shared_args(gen_parser)
    gen_parser.add_argument(
        "--api",
        choices=["auto", "responses", "images"],
        default="auto",
        help=(
            "Generation API. auto uses Responses image_generation when --size and "
            "Images-only controls are omitted; otherwise it uses Images API."
        ),
    )
    gen_parser.add_argument(
        "--responses-model",
        help=(
            "Responses API model. Equivalent to --model in Responses mode. "
            "Defaults to model in Codex config.toml, then "
            f"{DEFAULT_RESPONSES_MODEL}."
        ),
    )
    gen_parser.add_argument(
        "--input-image",
        action="append",
        help="Local image path to pass to Responses API as input_image. Repeat for multiple images.",
    )
    gen_parser.add_argument(
        "--input-image-url",
        action="append",
        help="Remote image URL to pass to Responses API as input_image. Repeat for multiple images.",
    )
    gen_parser.add_argument(
        "--input-file-id",
        action="append",
        help="OpenAI file ID to pass to Responses API as input_image. Repeat for multiple files.",
    )
    gen_parser.add_argument(
        "--input-detail",
        choices=["low", "high", "auto"],
        help="Optional detail level for Responses input images.",
    )
    gen_parser.add_argument(
        "--responses-action",
        choices=["auto", "generate", "edit"],
        help="Optional action for the Responses image_generation tool.",
    )
    gen_parser.set_defaults(func=_generate)

    batch_parser = subparsers.add_parser(
        "generate-batch",
        help="Generate multiple prompts concurrently (JSONL input)",
    )
    _add_shared_args(batch_parser)
    batch_parser.add_argument("--input", required=True, help="Path to JSONL file (one job per line)")
    batch_parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    batch_parser.add_argument("--max-attempts", type=int, default=3)
    batch_parser.add_argument("--fail-fast", action="store_true")
    batch_parser.set_defaults(func=_generate_batch)

    edit_parser = subparsers.add_parser("edit", help="Edit an existing image")
    _add_shared_args(edit_parser)
    edit_parser.add_argument("--image", action="append", required=True)
    edit_parser.add_argument("--mask")
    edit_parser.add_argument("--input-fidelity")
    edit_parser.set_defaults(func=_edit)

    args = parser.parse_args()
    if args.n < 1 or args.n > 10:
        _die("--n must be between 1 and 10")
    if getattr(args, "concurrency", 1) < 1 or getattr(args, "concurrency", 1) > 25:
        _die("--concurrency must be between 1 and 25")
    if getattr(args, "max_attempts", 3) < 1 or getattr(args, "max_attempts", 3) > 10:
        _die("--max-attempts must be between 1 and 10")
    if args.output_compression is not None and not (0 <= args.output_compression <= 100):
        _die("--output-compression must be between 0 and 100")
    if args.command == "generate-batch" and not args.out_dir:
        _die("generate-batch requires --out-dir")
    if getattr(args, "downscale_max_dim", None) is not None and args.downscale_max_dim < 1:
        _die("--downscale-max-dim must be >= 1")

    uses_responses = args.command == "generate" and _should_use_responses_generation(args)
    if uses_responses:
        unsupported = _responses_generate_options(args)
        if unsupported:
            _die(
                "Responses image_generation mode does not support "
                f"{', '.join(unsupported)}. Use --api images for those controls."
            )
        _validate_responses_options(args)
    else:
        if args.command == "generate":
            responses_only = _responses_only_options(args)
            if responses_only:
                _die(
                    "Images API generate mode does not support "
                    f"{', '.join(responses_only)}. Use --api responses for input images, "
                    "or use the edit subcommand for Images API edits."
                )
        model = _image_model_arg(args)
        _validate_model(model)
        _validate_size(_size_arg(args), model)
        _validate_quality(_quality_arg(args))
        _validate_background(args.background)
        _validate_model_specific_options(
            model=model,
            background=args.background,
            input_fidelity=getattr(args, "input_fidelity", None),
        )
        _ensure_api_key(args.dry_run)

    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
