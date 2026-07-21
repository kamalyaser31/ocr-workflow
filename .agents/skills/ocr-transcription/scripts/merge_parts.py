import json
import os
import argparse
import sys
from pathlib import Path, PureWindowsPath

# Ensure local scripts directory is in sys.path for importing _shared
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import write_text_atomic  # noqa: E402


def resolve_workspace_file(directory: Path, filename: str) -> Path:
    """Resolve a plain filename while keeping it inside its workspace."""
    windows_name = PureWindowsPath(filename)
    if not filename or Path(filename).name != filename:
        raise ValueError(f"Unsafe workspace filename: {filename!r}")
    if windows_name.name != filename or windows_name.is_absolute():
        raise ValueError(f"Unsafe workspace filename: {filename!r}")
    resolved_path = (directory / filename).resolve()
    if resolved_path.parent != directory.resolve():
        raise ValueError(f"Workspace path escapes its directory: {filename!r}")
    return resolved_path


def resolve_final_output(project_root: Path, filename: str) -> Path:
    """Confine the final Markdown file to the project's md directory."""
    if Path(filename).suffix.lower() != ".md":
        raise ValueError("The final output name must end with .md.")
    output_dir = (project_root / "md").resolve()
    output_path = resolve_workspace_file(output_dir, filename)
    if output_path.exists():
        raise FileExistsError(
            f"Final output already exists and will not be overwritten: "
            f"{output_path}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_path


def collect_part_paths(chunks: list, output_parts_dir: Path) -> list:
    """Validate every tracked chunk before any merged file is created."""
    incomplete_parts = [
        chunk["part"] for chunk in chunks if chunk["status"] != "completed"
    ]
    if incomplete_parts:
        raise ValueError(f"Incomplete parts cannot be merged: {incomplete_parts}")

    part_paths = []
    for chunk in chunks:
        output_filename = chunk.get("output_file")
        if not output_filename:
            raise ValueError(f"Chunk {chunk['part']} has no validated output file.")
        part_path = resolve_workspace_file(output_parts_dir, output_filename)
        if not part_path.is_file():
            raise FileNotFoundError(
                f"Validated file for chunk {chunk['part']} is missing: " f"{part_path}"
            )
        part_paths.append(part_path)
    return part_paths


def merge_part_files(part_paths: list, merged_path: Path) -> None:
    """Create one durable Markdown file from validated part files."""
    merged_content = []
    for part_path in part_paths:
        merged_content.append(part_path.read_text(encoding="utf-8").rstrip())
        print(f"Merged: {part_path.name}")
    write_text_atomic(merged_path, "\n\n".join(merged_content) + "\n")


def cleanup_completed_run(
    progress_path: Path,
    progress_data: dict,
    part_paths: list,
) -> None:
    """Remove only verified intermediates belonging to the completed run."""
    output_parts_dir = progress_path.parent
    for part_path in part_paths:
        part_path.unlink()
        print(f"Deleted Markdown part: {part_path.name}")
    if progress_data.get("is_split", True):
        for chunk in progress_data["chunks"]:
            pdf_path = resolve_workspace_file(output_parts_dir, chunk["filename"])
            if pdf_path.exists():
                pdf_path.unlink()
                print(f"Deleted PDF chunk: {pdf_path.name}")
    progress_path.unlink()


def load_merge_state(progress_file: Path) -> dict:
    """Load a nonempty merge state."""
    progress_data = json.loads(progress_file.read_text(encoding="utf-8"))
    if not progress_data.get("chunks"):
        raise ValueError("No chunks found in progress.json.")
    return progress_data


def execute_merge(progress_file: Path, output_name_override=None) -> bool:
    """Execute a validated merge and cleanup its intermediates."""
    progress_data = load_merge_state(progress_file)
    chunks = progress_data["chunks"]
    part_paths = collect_part_paths(chunks, progress_file.parent)
    final_name = output_name_override or progress_data.get(
        "final_filename", "final_full_ocr_output.md"
    )
    project_root = progress_file.parent.parent
    final_path = resolve_final_output(project_root, final_name)
    merge_part_files(part_paths, final_path)
    cleanup_completed_run(
        progress_file,
        progress_data,
        part_paths,
    )
    print(f"Final merged file written to: '{final_path}'")
    return True


def merge_parts(progress_path, final_output_name_override=None):
    """Merge a complete tracked run without overwriting user files."""
    progress_file = Path(progress_path).resolve()
    if not progress_file.is_file():
        print(f"Error: {progress_file} not found.", file=sys.stderr)
        return False
    try:
        return execute_merge(progress_file, final_output_name_override)
    except (
        FileExistsError,
        FileNotFoundError,
        json.JSONDecodeError,
        KeyError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # Force UTF-8 for stdout to prevent Windows encoding errors
    if sys.stdout.encoding != "utf-8":
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Merge completed OCR parts into one Markdown file."
    )
    parser.add_argument(
        "--progress",
        default=os.path.join("output_parts", "progress.json"),
        help="Tracker path (default: output_parts/progress.json).",
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help="Optional final Markdown filename override.",
    )
    args = parser.parse_args()

    success = merge_parts(args.progress, args.output_name)
    sys.exit(0 if success is not False else 1)
