#!/usr/bin/env python3
"""Phase-0 verification CLI — run this BEFORE trusting any of the pipeline's
assumptions against your own account. No Ansible/Docker needed; just the Python deps
installed and Ollama running somewhere reachable.

This is a hard go/no-go gate, not a formality: it validates, against your real
Supernote account and your real handwriting, the three things this pipeline assumes
but can't confirm without live credentials:

  (a) the hand-rolled Supernote client's login + folder-listing actually works
      against your self-hosted Supernote Private Cloud and matches the taxonomy
      configured in config/taxonomy.default.yml (or your own override)
  (b) the crossed-out-vs-struck-through symbol semantics in
      config/symbol-mapping.default.yml (or your own override) match what your
      pages actually look like
  (c) a given Ollama vision model is accurate enough transcribing your handwriting
      on CPU to be worth building the rest of the pipeline around

Usage:
    export SUPERNOTE_CLOUD_URL=https://your-supernote-private-cloud.example.com
    export SUPERNOTE_USERNAME=you@example.com
    export SUPERNOTE_PASSWORD=...
    export OLLAMA_HOST=http://localhost:11434

    # Step 1 — just check the taxonomy matches what this pipeline assumes:
    python scripts/htr_bench.py list

    # Step 2 — pull one real note, render a page, run it through HTR + structuring,
    # and print everything so it can be checked against the real page by eye:
    python scripts/htr_bench.py transcribe --path "/NOTE/Note/Journal/Daily/2026/2026-07-09.note" --page 0 \\
        --model qwen3-vl:8b --save-image /tmp/page.png
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from home_pkms.htr.ollama_client import OllamaHTRClient  # noqa: E402
from home_pkms.htr.prompts import build_transcription_prompt  # noqa: E402
from home_pkms.structuring.symbol_mapping import SymbolMappingConfig, classify  # noqa: E402
from home_pkms.supernote.client import SupernoteClient  # noqa: E402
from home_pkms.supernote.note_parser import parse_note_bytes  # noqa: E402
from home_pkms.taxonomy import TaxonomyConfig  # noqa: E402

DEFAULT_SYMBOL_CONFIG = Path(__file__).parent.parent / "config" / "symbol-mapping.default.yml"
DEFAULT_TAXONOMY_CONFIG = Path(__file__).parent.parent / "config" / "taxonomy.default.yml"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: environment variable {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def _connect() -> SupernoteClient:
    base_url = _require_env("SUPERNOTE_CLOUD_URL")
    username = _require_env("SUPERNOTE_USERNAME")
    password = _require_env("SUPERNOTE_PASSWORD")
    client = SupernoteClient(base_url)
    print(f"Logging in to {base_url} as {username}...")
    client.login(username, password)
    print("Login OK.")
    return client


def cmd_list(args: argparse.Namespace) -> None:
    taxonomy = TaxonomyConfig.load(Path(args.taxonomy_config))
    client = _connect()
    try:
        entries = client.list_folder("/", recursive=True)
    finally:
        client.close()

    print(f"\n{len(entries)} total entries returned.\n")

    by_category: Counter[str] = Counter()
    unrecognized: list[str] = []
    samples: dict[str, list[str]] = {}

    for entry in entries:
        if entry.is_folder or not entry.name.endswith(".note"):
            continue
        categorized = taxonomy.categorize_path(entry.path_display)
        if categorized is None:
            unrecognized.append(entry.path_display)
            continue
        category, year, title = categorized
        by_category[category] += 1
        samples.setdefault(category, [])
        if len(samples[category]) < 3:
            samples[category].append(f"{entry.path_display}  (year={year}, title={title!r})")

    print(f"Notes per category (configured in {args.taxonomy_config}):")
    for category, count in sorted(by_category.items()):
        print(f"  {category:12s} {count:5d}")
        for sample in samples.get(category, []):
            print(f"      e.g. {sample}")

    if unrecognized:
        print(f"\n{len(unrecognized)} note(s) did NOT match the configured taxonomy:")
        for path in unrecognized[:20]:
            print(f"  {path}")
        print(
            f"\nIf these are real notes (not just {taxonomy.index_note}), either "
            f"reorganize them to fit <source_root>/<category>/<year>/<file>.note, or "
            f"adjust {args.taxonomy_config} to match your actual folder structure."
        )
    else:
        print("\nAll notes matched the configured taxonomy. ✅")


def cmd_transcribe(args: argparse.Namespace) -> None:
    client = _connect()
    try:
        entries = client.list_folder("/", recursive=True)
        target = next((e for e in entries if e.path_display == args.path), None)
        if target is None:
            print(f"ERROR: no note found at path {args.path!r}", file=sys.stderr)
            print("Run `htr_bench.py list` to see real paths.", file=sys.stderr)
            sys.exit(1)

        print(f"Downloading {target.path_display} (id={target.id}, size={target.size})...")
        data = client.download(target.id)
    finally:
        client.close()

    print(f"Parsing .note file ({len(data)} bytes)...")
    notebook = parse_note_bytes(data, policy="loose")
    print(f"Total pages: {notebook.total_pages}")

    if args.page >= notebook.total_pages:
        print(f"ERROR: page {args.page} out of range (0..{notebook.total_pages - 1})", file=sys.stderr)
        sys.exit(1)

    png_bytes = notebook.render_page_png(args.page)
    if args.save_image:
        Path(args.save_image).write_bytes(png_bytes)
        print(f"Saved rendered page to {args.save_image} — open it and compare by eye.")

    symbol_config = SymbolMappingConfig.load(Path(args.symbol_config))
    prompt = build_transcription_prompt(symbol_config)

    ollama_host = _require_env("OLLAMA_HOST")
    print(f"\nSending page to Ollama ({ollama_host}, model={args.model})...")
    htr_client = OllamaHTRClient(ollama_host, model=args.model)
    try:
        vlm_lines = htr_client.transcribe_page(png_bytes, prompt)
    finally:
        htr_client.close()

    print(f"\n{len(vlm_lines)} lines transcribed:\n")
    for line in vlm_lines:
        classified = classify(line, symbol_config)
        flags = []
        if line.symbol_crossed_out:
            flags.append("crossed_out")
        if line.text_struck_through:
            flags.append("struck_through")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        review = " ⚠️ NEEDS REVIEW" if classified.needs_review else ""
        print(
            f"  [{line.raw_symbol:13s}]{flag_str:22s} conf={line.confidence:.2f}  "
            f"-> {classified.entry_type}/{classified.state}{review}"
        )
        print(f"      text: {line.text!r}")

    print(
        "\nCompare the above against the saved page image. If crossed_out/"
        "struck_through -> complete/cancelled mapping looks wrong, edit "
        f"{args.symbol_config} (the 'marks' section) — it's meant to be tuned."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List all notes and check the folder taxonomy")
    list_parser.add_argument(
        "--taxonomy-config", default=str(DEFAULT_TAXONOMY_CONFIG), help="Path to taxonomy yml"
    )
    list_parser.set_defaults(func=cmd_list)

    transcribe_parser = sub.add_parser("transcribe", help="Download one note, render a page, run HTR")
    transcribe_parser.add_argument(
        "--path", required=True, help="Supernote path, e.g. /NOTE/Note/Journal/Daily/2026/2026-07-09.note"
    )
    transcribe_parser.add_argument("--page", type=int, default=0, help="0-indexed page number")
    transcribe_parser.add_argument("--model", default="qwen3-vl:8b", help="Ollama model tag")
    transcribe_parser.add_argument("--save-image", help="Path to save the rendered page PNG for visual comparison")
    transcribe_parser.add_argument(
        "--symbol-config", default=str(DEFAULT_SYMBOL_CONFIG), help="Path to symbol-mapping yml"
    )
    transcribe_parser.set_defaults(func=cmd_transcribe)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
