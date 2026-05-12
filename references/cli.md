# CLI reference (`scripts/image_gen.py`)

This file is for the default CLI/API mode. Read it for normal image generation, editing, project-bound outputs, API/model controls, or after the user explicitly confirms that a transparent-output request should use the `gpt-image-1.5` true-transparency path.

`generate-batch` is the CLI subcommand for many different prompts.

## What this CLI does
- `generate`: generate a new image from a prompt. With `--api auto`, omit `--size` to prefer Responses API hosted `image_generation`; provide `--size` or Images-only controls to use Images API.
- `edit`: edit one or more existing images through Images API.
- `generate-batch`: run many Images API generation jobs from a JSONL file after the user explicitly chooses CLI/API/model controls.

Real API calls require **network access** plus a credential resolved from Codex auth files or `OPENAI_API_KEY`. `--dry-run` does not require a credential.

## Quick start (works from any repo)
Set a stable path to the skill CLI (default `CODEX_HOME` is `~/.codex`):

```
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export IMAGE_GEN="$CODEX_HOME/skills/imagegen6/scripts/image_gen.py"
```

Install dependencies into that environment with its package manager. In uv-managed environments, `uv pip install ...` remains the preferred path.

## Quick start

Dry-run (no API call; no network required; does not require the OpenAI Python SDK):

```bash
python "$IMAGE_GEN" generate \
  --prompt "Test" \
  --out output/imagegen/test.png \
  --dry-run
```

Notes:
- One-off dry-runs print the API payload and the computed output path(s).
- Repo-local finals should live under `output/imagegen/`.

Generate through Responses hosted `image_generation` (no `--size`; requires credential + network):

```bash
python "$IMAGE_GEN" generate \
  --prompt "A cozy alpine cabin at dawn" \
  --out output/imagegen/alpine-cabin.png
```

Generate through Images API with explicit size:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A cozy alpine cabin at dawn" \
  --size 1024x1024 \
  --out output/imagegen/alpine-cabin.png
```

Edit:

```bash
python "$IMAGE_GEN" edit \
  --image input.png \
  --prompt "Replace only the background with a warm sunset" \
  --out output/imagegen/sunset-edit.png
```

## Guardrails
- Use the bundled CLI directly (`python "$IMAGE_GEN" ...`) after activating the correct environment.
- Do **not** create one-off runners (for example `gen_images.py`) unless the user explicitly asks for a custom wrapper.
- **Never modify** `scripts/image_gen.py`. If something is missing, ask the user before doing anything else.
- Do not silently downgrade from CLI `gpt-image-2` or built-in `image_gen` to CLI `gpt-image-1.5`; ask first unless the user already explicitly requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback.

## Defaults
- Generate API: `--api auto`
- Responses model: explicit `--responses-model` or `--model`, then `model = "..."` from Codex `config.toml`, then `gpt-5.4-mini`
- Images API model: `gpt-image-2`
- Images API size: `auto`
- Images API quality: `medium`
- Output format: `png`
- Default one-off output path: `output/imagegen/<uuid>.png`
- Background: unspecified unless `--background` is set

## Credentials and base URL

For both Responses API and Images API calls, the script resolves credentials in this order:

1. `$CODEX_HOME/auth.json`, then current workspace `.codex/auth.json`, then `~/.codex/auth.json`.
2. Use `tokens.access_token` when present and non-empty.
3. Otherwise use `OPENAI_API_KEY` from `auth.json`.
4. Otherwise use environment variable `OPENAI_API_KEY`.
5. If none exists, fail with an error.

The script resolves the API base URL from the first available `config.toml` in the same config directory search order. It reads `model_provider`, finds `[model_providers.<name>]` such as `[model_providers.OpenAI]`, and uses `base_url`. If no config value exists, it uses the OpenAI default base URL.

For Responses generation, model precedence is:

1. `--responses-model` or `--model` from the command line.
2. Top-level `model = "..."` in the selected Codex `config.toml`.
3. `gpt-5.4-mini` when the field is missing or the config cannot be parsed.

For Responses reasoning effort, the script reads top-level `model_reasoning_effort = "..."` from the selected Codex `config.toml` and sends it as `reasoning.effort`. If the field is missing, unsupported, or the config cannot be parsed, it sends `high`.

## Responses image_generation path

Use this path when CLI/API generation is requested and no exact dimensions are needed:

```bash
python "$IMAGE_GEN" generate \
  --api responses \
  --prompt "A modern abstract test image, no text, no watermark" \
  --out output/imagegen/abstract-test.png
```

Use local reference/edit images:

```bash
python "$IMAGE_GEN" generate \
  --api responses \
  --prompt "Use the input image as a reference and create a polished product poster" \
  --input-image ./product.png \
  --responses-action auto \
  --out output/imagegen/product-poster.png
```

Edit an input image through the Responses hosted tool:

```bash
python "$IMAGE_GEN" generate \
  --api responses \
  --prompt "Change only the background to a clean studio setup; keep the product unchanged" \
  --input-image ./product.png \
  --responses-action edit \
  --out output/imagegen/product-studio.png
```

Remote image URLs and file IDs are also supported with repeated `--input-image-url` and `--input-file-id`. Use `--input-detail low|high|auto` when the input image detail level matters. Dry-run redacts local base64 data URLs so the payload stays readable.

## Prompt mapping
- `--use-case`, `--scene`, `--subject`, `--style`, `--composition`, `--lighting`, `--palette`, `--materials`, `--text`, `--constraints`, and `--negative` are the supported prompt-shaping fields.
- Do not pass `--asset-type`; it is prompt scaffolding only and has no CLI equivalent.
- If a workflow example includes `Asset type`, keep it in the prompt text or drop it when calling the CLI.

Dry-run payload includes the hosted tool:

```json
{"type":"image_generation","output_format":"png"}
```

Do not pass `--size`, `--model`, `--quality`, `--background`, `--output-compression`, `--moderation`, or a non-PNG `--output-format` in `--api responses` mode. Use `--api images` when those controls are required.

## gpt-image-2 size and model guidance

`gpt-image-2` is the default model for new CLI fallback work.

- Use `--quality low` for fast drafts, thumbnails, and quick iterations.
- Use `--quality medium`, `--quality high`, or `--quality auto` for final assets, dense text, diagrams, identity-sensitive edits, and high-resolution outputs.
- Square images are typically fastest. Use `--size 1024x1024` for quick square drafts.
- If the user asks for 4K-style output, use `--size 3840x2160` for landscape or `--size 2160x3840` for portrait.
- Do not pass `--input-fidelity` with `gpt-image-2`; this model always uses high fidelity for image inputs.
- Do not use `--background transparent` with `gpt-image-2`; the default transparent-image workflow uses built-in `image_gen` on a flat chroma-key background plus local removal. Use `gpt-image-1.5` only after the user explicitly confirms the true-transparent CLI fallback, unless they already requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback.

Popular `gpt-image-2` sizes:
- `1024x1024`
- `1536x1024`
- `1024x1536`
- `2048x2048`
- `2048x1152`
- `3840x2160`
- `2160x3840`
- `auto`

`gpt-image-2` size constraints:
- max edge `<= 3840px`
- both edges multiples of `16px`
- long edge to short edge ratio `<= 3:1`
- total pixels between `655,360` and `8,294,400`
- outputs above `2560x1440` total pixels are experimental

Fast draft:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A product thumbnail of a matte ceramic mug on a stone surface" \
  --quality low \
  --size 1024x1024 \
  --out output/imagegen/mug-draft.png
```

Final 2K landscape:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A polished landing-page hero image of a matte ceramic mug on a stone surface" \
  --quality high \
  --size 2048x1152 \
  --out output/imagegen/mug-hero.png
```

4K landscape:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A detailed architectural visualization at golden hour" \
  --size 3840x2160 \
  --quality high \
  --out output/imagegen/architecture-4k.png
```

True transparent fallback request:

Ask for confirmation before using this command unless the user already explicitly requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback.

```bash
python "$IMAGE_GEN" generate \
  --model gpt-image-1.5 \
  --prompt "A clean product cutout on a transparent background" \
  --background transparent \
  --output-format png \
  --out output/imagegen/product-cutout.png
```

When using this path, explain briefly that built-in `image_gen` plus chroma-key removal is the default transparent-image path, but this request needs true model-native transparency. `gpt-image-2` does not support `background=transparent`, so `gpt-image-1.5` is required for this confirmed fallback.

## Quality, input fidelity, and masks (CLI fallback only)
These are explicit CLI controls. They are not built-in `image_gen` tool arguments.

- `--quality` works for `generate`, `edit`, and `generate-batch`: `low|medium|high|auto`
- `--input-fidelity` is **edit-only** and validated as `low|high`; it is not supported for `gpt-image-2`
- `--mask` is **edit-only**

Example:

```bash
python "$IMAGE_GEN" edit \
  --model gpt-image-1.5 \
  --image input.png \
  --prompt "Change only the background" \
  --quality high \
  --input-fidelity high \
  --out output/imagegen/background-edit.png
```

Mask notes:
- For multi-image edits, pass repeated `--image` flags. Their order is meaningful, so describe each image by index and role in the prompt.
- The CLI accepts a single `--mask`.
- Image and mask must be the same size and format and each under 50MB.
- Masks must include an alpha channel.
- If multiple input images are provided, the mask applies to the first image.
- Masking is prompt-guided; do not promise exact pixel-perfect mask boundaries.
- Use a PNG mask when possible; the script treats mask handling as best-effort and does not perform full preflight validation beyond file checks/warnings.
- In the edit prompt, repeat invariants (`change only the background; keep the subject unchanged`) to reduce drift.

## Output handling
- Use `tmp/imagegen/` for temporary JSONL inputs or scratch files.
- Use `output/imagegen/` for final outputs.
- Reruns fail if a target file already exists unless you pass `--force`.
- `--out` may name a file or a path. If you omit the filename, the CLI generates a UUID filename under `output/imagegen/` by default. If the value ends with a path separator, the CLI treats it as a directory.
- `--out-dir` uses UUID filenames unless a job supplies an explicit `out` filename. If a job does supply `out`, that filename is used under `--out-dir`.
- Downscaled copies use the default suffix `-web` unless you override it.

## Common recipes

Generate with augmentation fields:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A minimal hero image of a ceramic coffee mug" \
  --use-case "product-mockup" \
  --style "clean product photography" \
  --composition "wide product shot with usable negative space for page copy" \
  --constraints "no logos, no text" \
  --out output/imagegen/mug-hero.png
```

Generate + also write a downscaled copy for fast web loading:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A cozy alpine cabin at dawn" \
  --size 1024x1024 \
  --downscale-max-dim 1024 \
  --out output/imagegen/alpine-cabin.png
```

Generate multiple prompts concurrently (async batch):

```bash
mkdir -p tmp/imagegen output/imagegen/batch
cat > tmp/imagegen/prompts.jsonl << 'EOF'
{"prompt":"Cavernous hangar interior with a compact shuttle parked near the center","use_case":"stylized-concept","composition":"wide-angle, low-angle","lighting":"volumetric light rays through drifting fog","constraints":"no logos or trademarks; no watermark","size":"1536x1024"}
{"prompt":"Gray wolf in profile in a snowy forest","use_case":"photorealistic-natural","composition":"eye-level","constraints":"no logos or trademarks; no watermark","size":"1024x1024"}
EOF

python "$IMAGE_GEN" generate-batch \
  --input tmp/imagegen/prompts.jsonl \
  --out-dir output/imagegen/batch \
  --concurrency 5

rm -f tmp/imagegen/prompts.jsonl
```

Notes:
- `generate-batch` requires `--out-dir`.
- generate-batch requires --out-dir.
- Use `--concurrency` to control parallelism (default `5`).
- Per-job overrides are supported in JSONL (for example `size`, `quality`, `background`, `output_format`, `output_compression`, `moderation`, `n`, `model`, `out`, and prompt-augmentation fields).
- `--n` generates multiple variants for a single prompt; `generate-batch` is for many different prompts.
- In batch mode, per-job `out` is treated as a filename under `--out-dir`.
- For many requested deliverable assets, provide one prompt/job per distinct asset. When you omit `out`, the CLI generates a UUID filename for that job.

## CLI notes
- `generate --api auto` chooses Responses API when no `--size` or Images-only controls are present.
- `generate --api images` forces `/v1/images/generations` and uses GPT Image model validation.
- `generate --api responses` forces `/v1/responses` with hosted `image_generation` and rejects Images-only controls.
- Supported sizes depend on the model. `gpt-image-2` supports flexible constrained sizes; older GPT Image models support `1024x1024`, `1536x1024`, `1024x1536`, or `auto`.
- True transparent CLI outputs require `output_format` to be `png` or `webp` and are not supported by `gpt-image-2`.
- `--prompt-file`, `--output-compression`, `--moderation`, `--max-attempts`, `--fail-fast`, `--force`, and `--no-augment` are supported where the selected API path accepts them.
- The Images API paths are intended for GPT Image models. The Responses path uses a Responses-capable model and hosted `image_generation`.

## See also
- API parameter quick reference for fallback CLI/API mode: `references/image-api.md`
- Prompt examples shared across all modes: `references/sample-prompts.md`
- Network/sandbox notes for fallback CLI mode: `references/codex-network.md`
- Built-in-first transparent image workflow: `SKILL.md` and `$CODEX_HOME/skills/imagegen6/scripts/remove_chroma_key.py`

