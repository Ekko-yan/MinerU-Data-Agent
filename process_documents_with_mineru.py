from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_INPUT_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpeg",
    ".jpg",
    ".jp2",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
    ".docx",
    ".pptx",
    ".xlsx",
}


CONTENT_LIST_SUFFIX = "_content_list.json"
CONTENT_LIST_V2_SUFFIX = "_content_list_v2.json"
MIDDLE_JSON_SUFFIX = "_middle.json"
MODEL_JSON_SUFFIX = "_model.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use MinerU to parse unstructured documents into Markdown, JSON, "
            "and normalized JSONL chunks."
        )
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        type=Path,
        help="Document file or directory. Supports PDF, images, DOCX, PPTX, XLSX.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=Path("outputs"),
        type=Path,
        help="Directory for MinerU native outputs and normalized artifacts.",
    )
    parser.add_argument(
        "-b",
        "--backend",
        default="pipeline",
        choices=[
            "pipeline",
            "vlm-http-client",
            "hybrid-http-client",
            "vlm-auto-engine",
            "hybrid-auto-engine",
        ],
        help=(
            "MinerU backend. Use 'pipeline' for CPU-friendly local parsing; "
            "use hybrid/vlm modes when local or remote VLM capacity is available."
        ),
    )
    parser.add_argument(
        "-m",
        "--method",
        default="auto",
        choices=["auto", "txt", "ocr"],
        help="PDF parsing method used by pipeline/hybrid backends.",
    )
    parser.add_argument(
        "-l",
        "--lang",
        default="ch",
        help="OCR language hint, for example ch or en.",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Existing mineru-api base URL. If omitted, MinerU starts a temporary local API.",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        help="OpenAI-compatible model server URL for *-http-client backends.",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Start page for PDF parsing, zero based.",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="End page for PDF parsing, zero based.",
    )
    parser.add_argument(
        "--formula",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable formula parsing.",
    )
    parser.add_argument(
        "--table",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable table parsing.",
    )
    parser.add_argument(
        "--image-analysis",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable image/chart analysis for VLM and hybrid backends.",
    )
    parser.add_argument(
        "--model-source",
        default="modelscope",
        choices=["modelscope", "huggingface"],
        help="Model download/source mirror. 'modelscope' is often smoother in China.",
    )
    parser.add_argument(
        "--mineru-bin",
        default=None,
        type=Path,
        help="Path to mineru executable. By default the script auto-detects it.",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Do not run MinerU; only rebuild manifest/chunks from an existing output directory.",
    )
    return parser.parse_args()


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def resolve_mineru_executable(explicit_path: Path | None) -> str:
    if explicit_path:
        resolved = explicit_path.expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"MinerU executable not found: {resolved}")
        return str(resolved)

    script_dir = Path(__file__).resolve().parent
    local_candidates = [
        script_dir / ".venv_mineru" / "Scripts" / "mineru.exe",
        script_dir / ".venv_mineru" / "bin" / "mineru",
        Path(sys.executable).resolve().parent / "Scripts" / "mineru.exe",
        Path(sys.executable).resolve().parent / "mineru",
    ]
    for candidate in local_candidates:
        if candidate.exists():
            return str(candidate)

    path_match = shutil.which("mineru")
    if path_match:
        return path_match

    raise FileNotFoundError(
        "Cannot find MinerU CLI. Activate the MinerU environment or pass --mineru-bin."
    )


def validate_input(input_path: Path) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    if input_path.is_file() and input_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
        raise ValueError(
            f"Unsupported input suffix {input_path.suffix!r}. "
            f"Supported: {', '.join(sorted(SUPPORTED_INPUT_SUFFIXES))}"
        )
    if input_path.is_dir():
        supported = [
            path
            for path in input_path.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
        ]
        if not supported:
            raise ValueError(f"No supported documents found in: {input_path}")


def build_mineru_command(args: argparse.Namespace) -> list[str]:
    command = [
        resolve_mineru_executable(args.mineru_bin),
        "-p",
        str(args.input.resolve()),
        "-o",
        str(args.output.resolve()),
        "-b",
        args.backend,
        "-m",
        args.method,
        "-l",
        args.lang,
        "-f",
        bool_text(args.formula),
        "-t",
        bool_text(args.table),
        "--image-analysis",
        bool_text(args.image_analysis),
        "--start",
        str(args.start),
    ]
    if args.end is not None:
        command.extend(["--end", str(args.end)])
    if args.api_url:
        command.extend(["--api-url", args.api_url])
    if args.server_url:
        command.extend(["--url", args.server_url])
    return command


def run_mineru(args: argparse.Namespace) -> list[str]:
    validate_input(args.input)
    args.output.mkdir(parents=True, exist_ok=True)

    command = build_mineru_command(args)
    env = os.environ.copy()
    env["MINERU_MODEL_SOURCE"] = args.model_source

    print("Running MinerU:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"MinerU failed with exit code {completed.returncode}")
    return command


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_text_if_exists(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def strip_known_suffix(name: str, suffix: str) -> str:
    return name[: -len(suffix)] if name.endswith(suffix) else Path(name).stem


def collect_images(parse_dir: Path) -> list[str]:
    images_dir = parse_dir / "images"
    if not images_dir.exists():
        return []
    return [
        str(path.resolve())
        for path in sorted(images_dir.iterdir())
        if path.is_file()
    ]


def resolve_media_path(parse_dir: Path, media_path: str | None) -> str | None:
    if not media_path:
        return None
    candidate = Path(media_path)
    if candidate.is_absolute():
        return str(candidate)
    return str((parse_dir / candidate).resolve())


def item_text(item: dict[str, Any]) -> str:
    pieces: list[str] = []

    text = item.get("text")
    if isinstance(text, str):
        pieces.append(text)

    for key in (
        "table_caption",
        "table_footnote",
        "image_caption",
        "image_footnote",
        "chart_caption",
        "chart_footnote",
        "list_items",
    ):
        value = item.get(key)
        if isinstance(value, str):
            pieces.append(value)
        elif isinstance(value, list):
            pieces.extend(part for part in value if isinstance(part, str))

    table_body = item.get("table_body")
    if isinstance(table_body, str):
        pieces.append(table_body)

    content = item.get("content")
    if isinstance(content, str):
        pieces.append(content)
    elif isinstance(content, dict):
        pieces.extend(flatten_content_text(content))

    return "\n".join(piece.strip() for piece in pieces if piece and piece.strip())


def flatten_content_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            texts.extend(flatten_content_text(item))
        return texts
    if isinstance(value, dict):
        texts = []
        for nested in value.values():
            texts.extend(flatten_content_text(nested))
        return texts
    return []


def normalize_items(
    document_name: str,
    parse_dir: Path,
    content_list: list[Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(content_list):
        if not isinstance(item, dict):
            continue

        row = {
            "document": document_name,
            "element_id": f"{document_name}:{index:06d}",
            "element_index": index,
            "type": item.get("type"),
            "page_idx": item.get("page_idx"),
            "text": item_text(item),
            "bbox": item.get("bbox"),
            "media_path": resolve_media_path(parse_dir, item.get("img_path")),
            "raw": item,
        }
        rows.append(row)
    return rows


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    for row in rows:
        item_type = str(row.get("type") or "unknown")
        by_type[item_type] = by_type.get(item_type, 0) + 1
    return {
        "elements": len(rows),
        "by_type": dict(sorted(by_type.items())),
        "text_elements": sum(1 for row in rows if row.get("text")),
        "media_elements": sum(1 for row in rows if row.get("media_path")),
    }


def find_parse_results(output_dir: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen_parse_dirs: set[Path] = set()

    for content_path in sorted(output_dir.rglob(f"*{CONTENT_LIST_SUFFIX}")):
        if content_path.name.endswith(CONTENT_LIST_V2_SUFFIX):
            continue

        parse_dir = content_path.parent
        if parse_dir in seen_parse_dirs:
            continue
        seen_parse_dirs.add(parse_dir)

        document_name = strip_known_suffix(content_path.name, CONTENT_LIST_SUFFIX)
        markdown_path = parse_dir / f"{document_name}.md"
        content_v2_path = parse_dir / f"{document_name}{CONTENT_LIST_V2_SUFFIX}"
        middle_json_path = parse_dir / f"{document_name}{MIDDLE_JSON_SUFFIX}"
        model_json_path = parse_dir / f"{document_name}{MODEL_JSON_SUFFIX}"

        content_list = read_json(content_path)
        if not isinstance(content_list, list):
            content_list = []

        rows = normalize_items(document_name, parse_dir, content_list)
        documents.append(
            {
                "document": document_name,
                "parse_dir": str(parse_dir.resolve()),
                "markdown_path": str(markdown_path.resolve()) if markdown_path.exists() else None,
                "content_list_path": str(content_path.resolve()),
                "content_list_v2_path": str(content_v2_path.resolve())
                if content_v2_path.exists()
                else None,
                "middle_json_path": str(middle_json_path.resolve())
                if middle_json_path.exists()
                else None,
                "model_json_path": str(model_json_path.resolve()) if model_json_path.exists() else None,
                "images": collect_images(parse_dir),
                "summary": summarize_rows(rows),
                "markdown_preview": (read_text_if_exists(markdown_path) or "")[:2000],
                "chunks": rows,
            }
        )
    return documents


def build_structured_outputs(
    output_dir: Path,
    command: list[str] | None,
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    documents = find_parse_results(output_dir)
    all_chunks = [
        row
        for document in documents
        for row in document["chunks"]
    ]

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input.resolve()),
        "output_dir": str(output_dir.resolve()),
        "mineru_command": command,
        "backend": args.backend,
        "method": args.method,
        "lang": args.lang,
        "documents": [
            {key: value for key, value in document.items() if key != "chunks"}
            for document in documents
        ],
        "summary": {
            "documents": len(documents),
            "chunks": len(all_chunks),
        },
    }

    manifest_path = output_dir / "structured_manifest.json"
    chunks_path = output_dir / "structured_chunks.jsonl"
    write_json(manifest_path, manifest)
    write_jsonl(chunks_path, all_chunks)
    return manifest_path, chunks_path


def main() -> None:
    args = parse_args()
    args.input = args.input.expanduser()
    args.output = args.output.expanduser()

    command = None
    if not args.skip_parse:
        command = run_mineru(args)

    manifest_path, chunks_path = build_structured_outputs(args.output, command, args)
    print(f"Structured manifest: {manifest_path.resolve()}")
    print(f"Structured JSONL chunks: {chunks_path.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
