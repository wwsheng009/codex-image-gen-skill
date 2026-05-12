---
name: "imagegen6"
description: "Generate or edit raster images when the task benefits from AI-created bitmap visuals such as photos, illustrations, textures, sprites, mockups, or transparent-background cutouts. Use the bundled CLI/API workflow by default when Codex should create a brand-new image, transform an existing image, derive visual variants from references, or save project assets. The CLI can use either the Images API or the Responses API hosted image_generation tool, resolves credentials from Codex auth/config files, and prefers Responses image_generation when no explicit image size is requested. Use the built-in image_gen tool only for inline preview, credential-free fallback, or built-in transparent chroma-key workflows. Do not use when the task is better handled by editing SVG/vector/code-native assets or building visuals directly in HTML/CSS/canvas."
---

# Image Generation Skill

Generates or edits images for the current project (for example website assets, game assets, UI mockups, product mockups, wireframes, logo design, photorealistic images, or infographics).

## Top-level modes and rules

This skill has three execution paths:

- **Default CLI/API mode (preferred):** `scripts/image_gen.py`. Use this for normal project-bound generation, edits, image references, exact output paths, batch work, and reproducible API behavior.
- **Responses image_generation CLI mode:** `scripts/image_gen.py generate` with `--api auto` or `--api responses`. Use this when generating without an explicit image size. It calls `POST /v1/responses` with `{"type":"image_generation","output_format":"png"}` in `tools`.
- **Images API CLI mode:** `scripts/image_gen.py` using `/v1/images/generations` or `/v1/images/edits`. Use this when exact size, quality, background, masks, edit inputs, or batch generation are required.
- **Built-in tool fallback mode:** built-in `image_gen` tool. Use only for inline preview, when no usable CLI/API credential exists and the user accepts fallback behavior, or for the built-in-first transparent chroma-key workflow.

Within default CLI/API mode, the CLI exposes three subcommands:

- `generate`
- `edit`
- `generate-batch`

Rules:
- Use `scripts/image_gen.py` by default for normal image generation and editing requests.
- For generation without explicit dimensions, use `scripts/image_gen.py generate --api auto` so the script uses the Responses API hosted `image_generation` tool.
- Use `--api images` or supply `--size` when exact image dimensions are required; the Responses hosted tool path intentionally does not send a size parameter.
- Treat the labeled prompt lines in this skill as prompt text, not CLI flags. Map them only to the documented options in `references/cli.md`; do not invent flags such as `--asset-type`.
- If the user explicitly asks for a transparent image/background, stay on built-in `image_gen` first: prompt for a flat removable chroma-key background, then remove it locally with the installed helper at `${CODEX_HOME:-$HOME/.codex}/skills/imagegen6/scripts/remove_chroma_key.py`.
- Never silently switch from built-in `image_gen` or CLI `gpt-image-2` to CLI `gpt-image-1.5`. Treat this as a model/path downgrade and ask the user before doing it, unless the user has already explicitly requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback.
- If a transparent request appears too complex for clean chroma-key removal, asks for true/native transparency, or local removal fails validation, explain that true transparency requires CLI `gpt-image-1.5 --background transparent --output-format png` because `gpt-image-2` does not support `background=transparent`, then ask whether to proceed. Run the CLI fallback only after the user confirms.
- The word `batch` means CLI mode when the user wants saved deliverables or reproducible generation. Use `generate-batch` for many different prompts; use `--n` only for variants of one prompt.
- If CLI/API credentials are missing, tell the user the built-in `image_gen` fallback exists and does not require local credentials. Proceed with built-in fallback only if the user accepts fallback behavior or only needs inline preview.
- Use the bundled `scripts/image_gen.py` workflow. Do not create one-off SDK runners.
- Never modify `scripts/image_gen.py`. If something is missing, ask the user before doing anything else.

Built-in save-path policy:
- In built-in tool mode, Codex saves generated images under `$CODEX_HOME/*` by default.
- Do not describe or rely on OS temp as the default built-in destination.
- Do not describe or rely on a destination-path argument (if any) on the built-in `image_gen` tool. If a specific location is needed, generate first and then move or copy the selected output from `$CODEX_HOME/generated_images/...`.
- Save-path precedence in built-in mode:
  1. If the user names a destination, move or copy the selected output there.
  2. If the image is meant for the current project, move or copy the final selected image into the workspace before finishing.
  3. If the image is only for preview or brainstorming, render it inline; the underlying file can remain at the default `$CODEX_HOME/*` path.
- Never leave a project-referenced asset only at the default `$CODEX_HOME/*` path.
- Do not overwrite an existing asset unless the user explicitly asked for replacement; otherwise create a sibling versioned filename such as `hero-v2.png` or `item-icon-edited.png`.

Shared prompt guidance for all modes lives in `references/prompting.md` and `references/sample-prompts.md`.

CLI/API docs/resources:
- `references/cli.md`
- `references/image-api.md`
- `references/codex-network.md`
- `scripts/image_gen.py`

Local post-processing helper:
- `${CODEX_HOME:-$HOME/.codex}/skills/imagegen6/scripts/remove_chroma_key.py`: removes a flat chroma-key background from a generated image and writes a PNG/WebP with alpha. Prefer auto-key sampling, soft matte, and despill for antialiased edges.

## When to use
- Generate a new image (concept art, product shot, cover, website hero)
- Generate a new image using one or more reference images for style, composition, or mood
- Edit an existing image (inpainting, lighting or weather transformations, background replacement, object removal, compositing, transparent background)
- Produce many assets or variants for one task

## When not to use
- Extending or matching an existing SVG/vector icon set, logo system, or illustration library inside the repo
- Creating simple shapes, diagrams, wireframes, or icons that are better produced directly in SVG, HTML/CSS, or canvas
- Making a small project-local asset edit when the source file already exists in an editable native format
- Any task where the user clearly wants deterministic code-native output instead of a generated bitmap

## Decision tree

Think about two separate questions:

1. **Intent:** is this a new image or an edit of an existing image?
2. **Execution strategy:** is this one asset or many assets/variants?

Intent:
- If the user wants to modify an existing image while preserving parts of it, treat the request as **edit**.
- If the user provides images only as references for style, composition, mood, or subject guidance, treat the request as **generate**.
- If the user provides no images, treat the request as **generate**.

Built-in edit semantics:
- Built-in edit mode is for images already visible in the conversation context, such as attached images or images generated earlier in the thread.
- If the user wants to edit a local image file with the built-in tool, first load it with built-in `view_image` tool so the image is visible in the conversation context, then proceed with the built-in edit flow.
- Do not promise arbitrary filesystem-path editing through the built-in tool.
- If a local file still needs direct file-path control, masks, or other explicit CLI-only parameters, use the explicit CLI fallback only when the user asks for it.
- For edits, preserve invariants aggressively and save non-destructively by default.

Execution strategy:
- In the built-in default path, produce many assets or variants by issuing one `image_gen` call per requested asset or variant.
- In the CLI fallback path, use the CLI `generate-batch` subcommand only when the user explicitly chose CLI mode and needs many prompts/assets.
- For many distinct assets, do not use `n` as a substitute for separate prompts. `n` is for variants of one prompt; distinct assets need distinct built-in calls or distinct CLI `generate-batch` jobs.

Assume the user wants a new image unless they clearly ask to change an existing one.

## Workflow
1. Decide the execution path: CLI/API by default; use built-in `image_gen` only for inline preview, accepted credential-free fallback, or the built-in-first transparent chroma-key workflow.
2. Decide the intent: `generate` or `edit`.
3. Decide whether the output is preview-only or meant to be consumed by the current project.
4. Decide the execution strategy: single asset vs repeated built-in calls vs CLI `generate-batch`.
5. Collect inputs up front: prompt(s), exact text (verbatim), constraints/avoid list, and any input images.
6. For every input image, label its role explicitly:
   - reference image
   - edit target
   - supporting insert/style/compositing input
7. If the edit target is on the local filesystem, prefer CLI/API `edit` or Responses `--input-image`; inspect with `view_image` only when you intentionally choose built-in fallback.
8. If the user asked for a photo, illustration, sprite, product image, banner, or other explicitly raster-style asset, use the CLI/API workflow rather than substituting SVG/HTML/CSS placeholders. If the request is for an icon, logo, or UI graphic that should match existing repo-native SVG/vector/code assets, prefer editing those directly instead.
9. Augment the prompt based on specificity:
   - If the user's prompt is already specific and detailed, normalize it into a clear spec without adding creative requirements.
   - If the user's prompt is generic, add tasteful augmentation only when it materially improves output quality.
10. Use `scripts/image_gen.py` by default.
11. For transparent-output requests, follow the transparent image guidance below: generate with built-in `image_gen` on a flat chroma-key background, copy the selected output into the workspace or `tmp/imagegen/`, run the installed `${CODEX_HOME:-$HOME/.codex}/skills/imagegen6/scripts/remove_chroma_key.py` helper, and validate the alpha result before using it. If this path looks unsuitable or fails, ask before switching to CLI `gpt-image-1.5`.
12. Inspect outputs and validate: subject, style, composition, text accuracy, and invariants/avoid items.
13. Iterate with a single targeted change, then re-check.
14. For preview-only work, render the image inline; the underlying file may remain at the default `$CODEX_HOME/generated_images/...` path.
15. For project-bound work, move or copy the selected artifact into the workspace and update any consuming code or references. Never leave a project-referenced asset only at the default `$CODEX_HOME/generated_images/...` path.
16. For batches or multi-asset requests, persist every requested deliverable final in the workspace unless the user explicitly asked to keep outputs preview-only. Discarded variants do not need to be kept unless requested.
17. Use the CLI/API docs for Responses hosted `image_generation`, Images API model, quality, size, `input_fidelity`, masks, output format, output paths, and network setup.
18. Always report the final saved path(s) for any workspace-bound asset(s) as both a Markdown link and a normalized `file:///...` URI, plus the final prompt or prompt set and whether built-in, Responses API, or Images API mode was used.

## Transparent image requests

Transparent-image requests still use built-in `image_gen` first. Because the built-in tool does not expose a true transparent-background control, create a removable chroma-key source image and then convert the key color to alpha locally.

Default sequence:
1. Use built-in `image_gen` to generate the requested subject on a perfectly flat solid chroma-key background.
2. Choose a key color that is unlikely to appear in the subject: default `#00ff00`, use `#ff00ff` for green subjects, and avoid `#0000ff` for blue subjects.
3. After generation, move or copy the selected source image from `$CODEX_HOME/generated_images/...` into the workspace or `tmp/imagegen/`.
4. Run the installed helper path, not a project-relative script path:
   ```bash
   python "${CODEX_HOME:-$HOME/.codex}/skills/imagegen6/scripts/remove_chroma_key.py" \
     --input <source> \
     --out <final.png> \
     --auto-key border \
     --soft-matte \
     --transparent-threshold 12 \
     --opaque-threshold 220 \
     --despill
   ```
5. Validate that the output has an alpha channel, transparent corners, plausible subject coverage, and no obvious key-color fringe. If a thin fringe remains, retry once with `--edge-contract 1`; use `--edge-feather 0.25` only when the edge is visibly stair-stepped and the subject is not shiny or reflective.
6. Save the final alpha PNG/WebP in the project if the asset is project-bound. Never leave a project-referenced transparent asset only under `$CODEX_HOME/*`.

Prompt transparent requests like this:

```text
Create the requested subject on a perfectly flat solid #00ff00 chroma-key background for background removal.
The background must be one uniform color with no shadows, gradients, texture, reflections, floor plane, or lighting variation.
Keep the subject fully separated from the background with crisp edges and generous padding.
Do not use #00ff00 anywhere in the subject.
No cast shadow, no contact shadow, no reflection, no watermark, and no text unless explicitly requested.
```

Do not automatically use CLI `gpt-image-1.5 --background transparent --output-format png` instead of chroma keying. Ask the user first when the user asks for true/native transparency, when local removal fails validation, or when the requested image is complex: hair, fur, feathers, smoke, glass, liquids, translucent materials, reflective objects, soft shadows, realistic product grounding, or subject colors that conflict with all practical key colors.

Use a concise confirmation like:

```text
This likely needs true native transparency. The default built-in path uses a chroma-key background plus local removal, but true transparency requires the CLI fallback with gpt-image-1.5 because gpt-image-2 does not support background=transparent. It also requires a local Codex/OpenAI credential. Should I proceed with that CLI fallback?
```

## Prompt augmentation

Reformat user prompts into a structured, production-oriented spec. Make the user's goal clearer and more actionable, but do not blindly add detail.

Treat this as prompt-shaping guidance, not a closed schema. Use only the lines that help, and add a short extra labeled line when it materially improves clarity.

### Specificity policy

Use the user's prompt specificity to decide how much augmentation is appropriate:

- If the prompt is already specific and detailed, preserve that specificity and only normalize/structure it.
- If the prompt is generic, you may add tasteful augmentation when it will materially improve the result.

Allowed augmentations:
- composition or framing hints
- polish level or intended-use hints
- practical layout guidance
- reasonable scene concreteness that supports the stated request

Not allowed augmentations:
- extra characters or objects that are not implied by the request
- brand names, slogans, palettes, or narrative beats that are not implied
- arbitrary side-specific placement unless the surrounding layout supports it

## Use-case taxonomy (exact slugs)

Classify each request into one of these buckets and keep the slug consistent across prompts and references.

Generate:
- photorealistic-natural - candid/editorial lifestyle scenes with real texture and natural lighting.
- product-mockup - product/packaging shots, catalog imagery, merch concepts.
- ui-mockup - app/web interface mockups and wireframes; specify the desired fidelity.
- infographic-diagram - diagrams/infographics with structured layout and text.
- scientific-educational - classroom explainers, scientific diagrams, and learning visuals with required labels and accuracy constraints.
- ads-marketing - campaign concepts and ad creatives with audience, brand position, scene, and exact tagline/copy.
- productivity-visual - slide, chart, workflow, and data-heavy business visuals.
- logo-brand - logo/mark exploration, vector-friendly.
- illustration-story - comics, children's book art, narrative scenes.
- stylized-concept - style-driven concept art, 3D/stylized renders.
- historical-scene - period-accurate/world-knowledge scenes.

Edit:
- text-localization - translate/replace in-image text, preserve layout.
- identity-preserve - try-on, person-in-scene; lock face/body/pose.
- precise-object-edit - remove/replace a specific element (including interior swaps).
- lighting-weather - time-of-day/season/atmosphere changes only.
- background-extraction - transparent background / clean cutout. Use built-in `image_gen` with chroma-key removal first for simple opaque subjects; ask before using CLI true transparency for complex subjects.
- style-transfer - apply reference style while changing subject/scene.
- compositing - multi-image insert/merge with matched lighting/perspective.
- sketch-to-render - drawing/line art to photoreal render.

## Shared prompt schema

Use the following labeled spec as shared prompt scaffolding for all execution paths:

```text
Use case: <taxonomy slug>
Asset type: <where the asset will be used>
Primary request: <user's main prompt>
Input images: <Image 1: role; Image 2: role> (optional)
Scene/backdrop: <environment>
Subject: <main subject>
Style/medium: <photo/illustration/3D/etc>
Composition/framing: <wide/close/top-down; placement>
Lighting/mood: <lighting + mood>
Color palette: <palette notes>
Materials/textures: <surface details>
Text (verbatim): "<exact text>"
Constraints: <must keep/must avoid>
Avoid: <negative constraints>
```

Notes:
- `Asset type` and `Input images` are prompt scaffolding, not dedicated CLI flags.
- `Scene/backdrop` refers to the visual setting. It is not the same as the fallback CLI `background` parameter, which controls output transparency behavior.
- Fallback-only execution notes such as `Quality:`, `Input fidelity:`, masks, output format, and output paths belong in the CLI path only. Do not treat them as built-in `image_gen` tool arguments.

Augmentation rules:
- Keep it short.
- Add only the details needed to improve the prompt materially.
- For edits, explicitly list invariants (`change only X; keep Y unchanged`).
- If any critical detail is missing and blocks success, ask a question; otherwise proceed.

## Examples

### Generation example (hero image)
```text
Use case: product-mockup
Asset type: landing page hero
Primary request: a minimal hero image of a ceramic coffee mug
Style/medium: clean product photography
Composition/framing: wide composition with usable negative space for page copy if needed
Lighting/mood: soft studio lighting
Constraints: no logos, no text, no watermark
```

### Edit example (invariants)
```text
Use case: precise-object-edit
Asset type: product photo background replacement
Primary request: replace only the background with a warm sunset gradient
Constraints: change only the background; keep the product and its edges unchanged; no text; no watermark
```

## Prompting best practices
- Structure prompt as scene/backdrop -> subject -> details -> constraints.
- Include intended use (ad, UI mock, infographic) to set the mode and polish level.
- Use camera/composition language for photorealism.
- Only use SVG/vector stand-ins when the user explicitly asked for vector output or a non-image placeholder.
- Quote exact text and specify typography + placement.
- For tricky words, spell them letter-by-letter and require verbatim rendering.
- For multi-image inputs, reference images by index and describe how they should be used.
- For edits, repeat invariants every iteration to reduce drift.
- Iterate with single-change follow-ups.
- If the prompt is generic, add only the extra detail that will materially help.
- If the prompt is already detailed, normalize it instead of expanding it.
- For CLI fallback only, see `references/cli.md` and `references/image-api.md` for model, `quality`, `input_fidelity`, masks, output format, and output-path guidance.
- For transparent images, use the built-in-first chroma-key workflow unless the request is complex enough to need true CLI transparency; ask before switching to CLI `gpt-image-1.5`.

More principles shared by all modes: `references/prompting.md`.
Copy/paste specs shared by all modes: `references/sample-prompts.md`.

## Guidance by asset type
Asset-type templates (website assets, game assets, wireframes, logo) are consolidated in `references/sample-prompts.md`.

## Responses image_generation guidance

The CLI `generate` command uses `--api auto` by default. In auto mode, when no `--size`, `--model`, `--quality`, `--background`, `--output-compression`, or `--moderation` is provided, it calls the Responses API with a hosted image generation tool:

```json
{"type":"image_generation","output_format":"png"}
```

- Use this path for CLI/API generation when the user did not ask for exact dimensions.
- Use `--api responses` to force this path.
- Use `--responses-model` or `--model` for Responses generation when the user wants to name the model explicitly. If neither is provided, the script reads `model = "..."` from Codex `config.toml`, then falls back to `gpt-5.4-mini`.
- For Responses generation, read `model_reasoning_effort = "..."` from Codex `config.toml` and pass it as `reasoning.effort`. If missing, unsupported, or config parsing fails, use `high`.
- Pass input images to Responses with repeated `--input-image <path>`, `--input-image-url <url>`, or `--input-file-id <file_id>`. Local images are encoded as base64 data URLs in `input_image.image_url`.
- Use `--responses-action auto` for reference-guided generation and `--responses-action edit` when the input image itself should be transformed. `edit` requires at least one input image.
- Use `--input-detail low|high|auto` to control input image detail.
- Do not pass size or Images API-only controls to the Responses path. The Responses hosted tool path uses PNG output. If the user needs exact dimensions or another output format, use `--api images` or provide `--size`.
- Dry-run redacts local image base64 while preserving payload structure. The script parses streaming SSE, extracts `image_generation_call.result`, decodes the base64, and writes the output file.

## gpt-image-2 guidance for Images API CLI fallback

The Images API CLI path defaults to `gpt-image-2`.

- Use `gpt-image-2` for new CLI/API workflows unless the request needs true model-native transparent output.
- If a transparent request may need CLI fallback, ask before using `gpt-image-1.5` unless the user already explicitly requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback. Explain that the built-in chroma-key path is the default, but true transparency requires `gpt-image-1.5` because `gpt-image-2` does not support `background=transparent`.
- `gpt-image-2` always uses high fidelity for image inputs; do not set `input_fidelity` with this model.
- `gpt-image-2` supports `quality` values `low`, `medium`, `high`, and `auto`.
- Use `quality low` for fast drafts, thumbnails, and quick iterations. Use `medium`, `high`, or `auto` for final assets, dense text, diagrams, identity-sensitive edits, or high-resolution outputs.
- Square images are typically fastest to generate. Use `1024x1024` for fast square drafts.
- If the user asks for 4K-style output, use `3840x2160` for landscape or `2160x3840` for portrait.
- `gpt-image-2` size may be `auto` or `WIDTHxHEIGHT` if all constraints hold: max edge `<= 3840px`, both edges multiples of `16px`, long-to-short ratio `<= 3:1`, total pixels between `655,360` and `8,294,400`.

Popular `gpt-image-2` sizes:
- `1024x1024` square
- `1536x1024` landscape
- `1024x1536` portrait
- `2048x2048` 2K square
- `2048x1152` 2K landscape
- `3840x2160` 4K landscape
- `2160x3840` 4K portrait
- `auto`

## CLI/API mode only

### Temp and output conventions
These conventions apply only to the CLI/API fallback. They do not describe built-in `image_gen` output behavior.
- Use `tmp/imagegen/` for intermediate files (for example JSONL batches); delete them when done.
- Write final artifacts under `output/imagegen/`.
- Use `--out` or `--out-dir` to control output paths; default to UUID filenames unless the user explicitly supplies a filename.

## Output naming
- For CLI fallback work, if the user does not supply a filename, generate a UUID filename such as `<uuid>.png`.
- If the user supplies `--out` with a filename, preserve that filename.
- If the user supplies only a directory, generate a UUID filename inside that directory.

### Dependencies
Prefer `uv` for dependency management in this repo.

The CLI uses direct HTTP requests for Responses API and Images API calls. It does not require the OpenAI Python SDK.

Required for local chroma-key removal and optional downscaling:
```bash
uv pip install pillow
```

Portability note:
- If you are using the installed skill outside this repo, install dependencies into that environment with its package manager.
- In uv-managed environments, `uv pip install ...` remains the preferred path.

### Environment
- Live CLI/API calls resolve credentials in this order:
  1. `$CODEX_HOME/auth.json`, then current workspace `.codex/auth.json`, then `~/.codex/auth.json`.
  2. If `auth.json` contains `tokens.access_token`, use it.
  3. Otherwise, if `auth.json` contains `OPENAI_API_KEY`, use it.
  4. Otherwise, use environment variable `OPENAI_API_KEY`.
  5. If none exists, return an error.
- The API base URL resolves from the first available `config.toml` in the same config directory search order. Read `model_provider`, then `[model_providers.<name>]`, and use `base_url`; if no config value exists, fall back to the OpenAI default base URL.
- Do not ask the user for `OPENAI_API_KEY` when using the built-in `image_gen` tool.
- Never ask the user to paste the full key in chat. Ask them to place it in Codex auth/config files or set it locally as an environment variable.

If credentials are missing, give the user these steps:
1. Create an API key in the OpenAI platform UI: https://platform.openai.com/api-keys
2. Put it in `.codex/auth.json` as `OPENAI_API_KEY`, or set `OPENAI_API_KEY` as an environment variable.
3. Ensure `.codex/config.toml` has the intended provider `base_url` when using a custom gateway.

If installation is not possible in this environment, tell the user which dependency is missing and how to install it into their active environment.

### Script-mode notes
- CLI commands + examples: `references/cli.md`
- API parameter quick reference: `references/image-api.md`
- Network approvals / sandbox settings for CLI mode: `references/codex-network.md`

## Reference map
- `references/prompting.md`: shared prompting principles for all modes.
- `references/sample-prompts.md`: shared copy/paste prompt recipes for all modes.
- `references/cli.md`: fallback-only CLI/API usage via `scripts/image_gen.py`.
- `references/image-api.md`: fallback-only API/CLI parameter reference.
- `references/codex-network.md`: fallback-only network/sandbox troubleshooting for CLI mode.
- `scripts/image_gen.py`: fallback-only CLI implementation. Do not load or use it unless the user explicitly chooses CLI mode or explicitly confirms a transparent request's true CLI transparency fallback.
- `${CODEX_HOME:-$HOME/.codex}/skills/imagegen6/scripts/remove_chroma_key.py`: local post-processing helper for built-in transparent-image requests.


