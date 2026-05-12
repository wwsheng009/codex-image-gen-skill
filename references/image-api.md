# Image API quick reference

This file is for the default CLI/API mode. Use it for `scripts/image_gen.py`, Responses hosted `image_generation`, Images API generation, Images API edits, model controls, or after the user explicitly confirms that a transparent-output request should use the `gpt-image-1.5` true-transparency path.

These parameters describe the Responses hosted `image_generation` path, the Images API path, and the bundled CLI surface. Do not assume they are normal arguments on the built-in `image_gen` tool.

## Scope
- The `generate --api auto` CLI path prefers Responses hosted `image_generation` when the user does not provide exact size or Images-only controls.
- The Images API paths are intended for GPT Image models (`gpt-image-2`, `gpt-image-1.5`, `gpt-image-1`, and `gpt-image-1-mini`).
- The built-in `image_gen` tool and the CLI/API paths do not expose the same controls.

## Responses hosted image_generation

Use `scripts/image_gen.py generate` without `--size`, or force it with `--api responses`, to call:

```text
POST /v1/responses
```

The request includes:

```json
{
  "tools": [
    {
      "type": "image_generation",
      "output_format": "png"
    }
  ],
  "tool_choice": "auto",
  "stream": true,
  "store": false
}
```

Important details:
- `image_generation` is a hosted tool, not a function tool.
- Do not write it as `{"type":"function","name":"image_generation"}`.
- Input images are passed in `input[].content[]` as `{"type":"input_image","image_url":"..."}` for URLs or base64 data URLs, or `{"type":"input_image","file_id":"file_..."}` for uploaded files.
- The CLI supports local files with `--input-image`, remote URLs with `--input-image-url`, and file IDs with `--input-file-id`; repeat any of these for multiple images.
- Local files are encoded as `data:<mime>;base64,<bytes>` and dry-run redacts the base64 portion.
- Use `--responses-action auto|generate|edit` to set the image_generation tool action. Use `edit` only when transforming an input image.
- Do not pass Images API-only controls such as `size`, `quality`, `background`, `output_compression`, `moderation`, non-PNG `output_format`, or GPT Image `model` to this path.
- The script parses SSE events, finds `image_generation_call.result`, base64-decodes it, and writes the requested output file.
- `--responses-model` or `--model` overrides the Responses model; otherwise the script reads top-level `model = "..."` from Codex `config.toml`, then uses `gpt-5.4-mini` if the field is missing or config parsing fails.
- The script reads top-level `model_reasoning_effort = "..."` from Codex `config.toml` and sends it as `reasoning.effort`; if missing, unsupported, or config parsing fails, it sends `high`.

## Model summary

| Model | Quality | Input fidelity | Resolutions | Recommended use |
| --- | --- | --- | --- | --- |
| `gpt-image-2` | `low`, `medium`, `high`, `auto` | Always high fidelity for image inputs; do not set `input_fidelity` | `auto` or flexible sizes that satisfy the constraints below | Default for new CLI/API workflows: high-quality generation and editing, text-heavy images, photorealism, compositing, identity-sensitive edits, and workflows where fewer retries matter |
| `gpt-image-1.5` | `low`, `medium`, `high`, `auto` | `low`, `high` | `1024x1024`, `1024x1536`, `1536x1024`, `auto` | Confirmed native transparent-background and backward-compatible workflows |
| `gpt-image-1` | `low`, `medium`, `high`, `auto` | `low`, `high` | `1024x1024`, `1024x1536`, `1536x1024`, `auto` | Legacy compatibility |
| `gpt-image-1-mini` | `low`, `medium`, `high`, `auto` | `low`, `high` | `1024x1024`, `1024x1536`, `1536x1024`, `auto` | Cost-sensitive draft batches and lower-stakes previews |

## gpt-image-2 sizes

`gpt-image-2` accepts `auto` or any `WIDTHxHEIGHT` size that satisfies all constraints:

- Maximum edge length must be less than or equal to `3840px`.
- Both edges must be multiples of `16px`.
- Long edge to short edge ratio must not exceed `3:1`.
- Total pixels must be at least `655,360` and no more than `8,294,400`.

Popular sizes:

| Label | Size | Notes |
| --- | --- | --- |
| Square | `1024x1024` | Typical fast default |
| Landscape | `1536x1024` | Standard landscape |
| Portrait | `1024x1536` | Standard portrait |
| 2K square | `2048x2048` | Larger square output |
| 2K landscape | `2048x1152` | Widescreen output |
| 4K landscape | `3840x2160` | Widescreen 4K output |
| 4K portrait | `2160x3840` | Vertical 4K output |
| Auto | `auto` | Default size |

Square images are typically fastest to generate. For 4K-style output, use `3840x2160` or `2160x3840`.

## Endpoints
- Responses generation: `POST /v1/responses` with hosted `image_generation`
- Generate: `POST /v1/images/generations` with a JSON body
- Edit: `POST /v1/images/edits` with multipart form-data

## Core parameters for GPT Image models
- `prompt`: text prompt
- `model`: image model
- `n`: number of images (1-10)
- `size`: `auto` by default for `gpt-image-2`; flexible `WIDTHxHEIGHT` sizes are allowed only for `gpt-image-2`; older GPT Image models use `1024x1024`, `1536x1024`, `1024x1536`, or `auto`
- `quality`: `low`, `medium`, `high`, or `auto`
- `background`: output transparency behavior (`transparent`, `opaque`, or `auto`) for generated output; this is not the same thing as the prompt's visual scene/backdrop
- `output_format`: `png` (default), `jpeg`, `webp`
- `output_compression`: 0-100 (jpeg/webp only)
- `moderation`: `auto` (default) or `low`

## Edit-specific parameters
- `image`: one or more input images. For GPT Image models, you can provide up to 16 images.
- `mask`: optional mask image
- `input_fidelity`: `low` or `high` only for models that support it; do not set this for `gpt-image-2`

Model-specific note for `input_fidelity`:
- `gpt-image-2` always uses high fidelity for image inputs and does not support setting `input_fidelity`.
- `gpt-image-1` and `gpt-image-1-mini` preserve all input images, but the first image gets richer textures and finer details.
- `gpt-image-1.5` preserves the first 5 input images with higher fidelity.

## Transparent backgrounds

`gpt-image-2` does not currently support the Image API `background=transparent` parameter. The skill's default transparent-image path is CLI/API generation with a flat chroma-key background, followed by local alpha extraction with `python "${CODEX_HOME:-$HOME/.codex}/skills/imagegen6/scripts/remove_chroma_key.py"`.

Use Images API `gpt-image-1.5` with `background=transparent` and a transparent-capable output format such as `png` or `webp` only after the user explicitly confirms that native-transparency path, unless they already requested `gpt-image-1.5` or true/native transparency. If the subject is too complex for clean chroma-key removal, or local background removal fails validation, explain the tradeoff and ask before switching.

## Output
- Responses path: final `image_generation_call.result` base64 from the streamed response.
- Images API path: `data[]` list with `b64_json` per image.
- The bundled `scripts/image_gen.py` CLI decodes the returned base64 and writes output files for you.

## Limits and notes
- Input images and masks must be under 50MB.
- Use the edits endpoint when the user requests changes to an existing image.
- Masking is prompt-guided; exact shapes are not guaranteed.
- Large sizes and high quality increase latency and cost.
- Use `quality=low` for fast drafts, thumbnails, and quick iterations. Use `medium` or `high` for final assets, dense text, diagrams, identity-sensitive edits, or high-resolution outputs.
- High `input_fidelity` can materially increase input token usage on models that support it.
- If a request fails because a specific option is unsupported by the selected GPT Image model, retry manually without that option only when the option is not required by the user. If true transparent output is required, ask before switching to `gpt-image-1.5` instead of dropping `background=transparent`, unless the user already explicitly chose that native-transparency path.

## Important boundary
- `quality`, `input_fidelity`, explicit masks, `background`, `output_format`, and related parameters are CLI/API execution controls.
- In CLI/API mode, non-PNG `output_format` and exact dimensions require Images API.
- Do not assume they are built-in `image_gen` tool arguments.

