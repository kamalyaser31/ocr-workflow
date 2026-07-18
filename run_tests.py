import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfWriter

PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / ".agents" / "skills" / "ocr-transcription" / "scripts"


def load_script(module_name: str, filename: str):
    module_path = SCRIPTS_DIR / filename
    module_spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


split_pdf_module = load_script("split_pdf_module", "splitt_pdf.py")
validate_chunk_module = load_script("validate_chunk_module", "validate_chunk.py")
merge_parts_module = load_script("merge_parts_module", "merge_parts.py")


def assert_raises(expected_exception, operation):
    try:
        operation()
    except expected_exception:
        return
    raise AssertionError(f"Expected {expected_exception.__name__} to be raised.")


def create_pdf(pdf_path: Path, page_count: int) -> None:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as pdf_file:
        writer.write(pdf_file)


def write_progress(progress_path: Path, chunks: list, **overrides) -> None:
    progress = {
        "final_filename": "book.md",
        "is_page_selection": False,
        "is_split": len(chunks) > 1,
        "chunks": chunks,
    }
    progress.update(overrides)
    progress_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")


def completed_chunk(part: int, page: int) -> dict:
    return {
        "part": part,
        "filename": f"book_part_{part}.pdf",
        "start_page": page,
        "end_page": page,
        "status": "completed",
        "output_file": f"part_{part}_output.md",
    }


def in_temporary_project(test_operation) -> None:
    previous_directory = Path.cwd()
    with tempfile.TemporaryDirectory() as temp_directory:
        project_dir = Path(temp_directory)
        os.chdir(project_dir)
        try:
            test_operation(project_dir)
        finally:
            os.chdir(previous_directory)


def test_page_ranges_keep_only_document_intersection() -> None:
    cases = {
        "200-300": [],
        "175-200": [175, 176, 177],
        "0-3": [1, 2, 3],
        "5,8,10-12": [5, 8, 10, 11, 12],
    }
    for page_expression, expected_pages in cases.items():
        actual_pages = split_pdf_module.parse_pages(page_expression, 177)
        assert actual_pages == expected_pages


def test_invalid_selection_creates_no_workspace() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        output_dir = project_dir / "output_parts"
        create_pdf(pdf_path, 2)
        assert_raises(
            ValueError,
            lambda: split_pdf_module.split_pdf(str(pdf_path), str(output_dir), "bad"),
        )
        assert not output_dir.exists()

    in_temporary_project(scenario)


def test_split_refuses_stale_workspace() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        output_dir = project_dir / "output_parts"
        output_dir.mkdir()
        sentinel_path = output_dir / "part_1_temp.md"
        sentinel_path.write_text("earlier run", encoding="utf-8")
        create_pdf(pdf_path, 2)
        assert_raises(
            RuntimeError,
            lambda: split_pdf_module.split_pdf(str(pdf_path), str(output_dir)),
        )
        assert sentinel_path.read_text(encoding="utf-8") == "earlier run"

    in_temporary_project(scenario)


def test_split_writes_canonical_selected_page_state() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        output_dir = project_dir / "output_parts"
        create_pdf(pdf_path, 12)
        split_pdf_module.split_pdf(str(pdf_path), str(output_dir), "5,8,10-12")
        progress = json.loads(
            (output_dir / "progress.json").read_text(encoding="utf-8")
        )
        assert progress["final_filename"] == "book_p5_8_10-12.md"
        assert progress["total_selected_pages"] == 5
        assert [chunk["start_page"] for chunk in progress["chunks"]] == [
            5,
            8,
            10,
        ]

    in_temporary_project(scenario)


def test_split_rejects_nonpositive_chunk_size() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        create_pdf(pdf_path, 2)
        assert_raises(
            ValueError,
            lambda: split_pdf_module.split_pdf(
                str(pdf_path), str(project_dir / "output_parts"), None, 0
            ),
        )

    in_temporary_project(scenario)


def test_marker_validation_rejects_prefixed_content() -> None:
    valid_text = "--- Page 1 ---\nText\n--- Page 2 ---\nText"
    prefixed_text = f"Unexpected\n{valid_text}"
    valid_check = validate_chunk_module._check_page_markers(valid_text, 1, 2)
    prefixed_check = validate_chunk_module._check_page_markers(prefixed_text, 1, 2)
    assert valid_check[0] is True
    assert prefixed_check[0] is False


def test_validation_commits_state_before_removing_raw_file() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "output_parts"
        output_dir.mkdir()
        progress_path = output_dir / "progress.json"
        temp_path = output_dir / "part_1_temp.md"
        chunk = completed_chunk(1, 1)
        chunk["status"] = "pending"
        chunk["output_file"] = ""
        write_progress(progress_path, [chunk])
        temp_path.write_text("--- Page 1 ---\nUnique text", encoding="utf-8")

        counts = validate_chunk_module.run_validation(
            [1], str(progress_path), str(output_dir)
        )
        saved_progress = json.loads(progress_path.read_text(encoding="utf-8"))
        assert counts == (1, 0, 0)
        assert saved_progress["chunks"][0]["status"] == "completed"
        assert (output_dir / "part_1_output.md").is_file()
        assert not temp_path.exists()

    in_temporary_project(scenario)


def test_validation_counts_missing_parts_as_skipped() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "output_parts"
        output_dir.mkdir()
        progress_path = output_dir / "progress.json"
        chunk = completed_chunk(1, 1)
        chunk["status"] = "pending"
        chunk["output_file"] = ""
        write_progress(progress_path, [chunk])
        counts = validate_chunk_module.run_validation(
            [1], str(progress_path), str(output_dir)
        )
        assert counts == (0, 0, 1)

    in_temporary_project(scenario)


def test_validation_cli_missing_progress_exits_with_failure() -> None:
    command = [
        sys.executable,
        "-B",
        str(SCRIPTS_DIR / "validate_chunk.py"),
        "1",
        "--progress",
        "missing-progress.json",
    ]
    completed_process = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed_process.returncode != 0


def test_merge_aborts_when_validated_part_is_missing() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "output_parts"
        output_dir.mkdir()
        progress_path = output_dir / "progress.json"
        write_progress(progress_path, [completed_chunk(1, 1)])
        assert merge_parts_module.merge_parts(str(progress_path)) is False
        assert progress_path.exists()
        assert not (project_dir / "md" / "book.md").exists()

    in_temporary_project(scenario)


def test_merge_rejects_output_path_escape() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "output_parts"
        output_dir.mkdir()
        progress_path = output_dir / "progress.json"
        chunk = completed_chunk(1, 1)
        write_progress(progress_path, [chunk])
        (output_dir / chunk["output_file"]).write_text(
            "--- Page 1 ---\nUnique text", encoding="utf-8"
        )
        outside_path = project_dir / "outside.md"
        outside_path.write_text("preserve me", encoding="utf-8")
        assert (
            merge_parts_module.merge_parts(str(progress_path), "..\\outside.md")
            is False
        )
        assert outside_path.read_text(encoding="utf-8") == "preserve me"

    in_temporary_project(scenario)


def test_merge_preserves_existing_final_output() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "output_parts"
        output_dir.mkdir()
        progress_path = output_dir / "progress.json"
        chunk = completed_chunk(1, 1)
        write_progress(progress_path, [chunk])
        (output_dir / chunk["output_file"]).write_text(
            "--- Page 1 ---\nNew text", encoding="utf-8"
        )
        final_path = project_dir / "md" / "book.md"
        final_path.parent.mkdir()
        final_path.write_text("existing text", encoding="utf-8")
        assert merge_parts_module.merge_parts(str(progress_path)) is False
        assert final_path.read_text(encoding="utf-8") == "existing text"
        assert progress_path.exists()

    in_temporary_project(scenario)


def test_merge_allows_identical_page_contents() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "output_parts"
        output_dir.mkdir()
        progress_path = output_dir / "progress.json"
        chunks = [completed_chunk(1, 1), completed_chunk(2, 2)]
        write_progress(progress_path, chunks)
        for chunk in chunks:
            page_number = chunk["start_page"]
            (output_dir / chunk["output_file"]).write_text(
                f"--- Page {page_number} ---\nRepeated",
                encoding="utf-8",
            )
        assert merge_parts_module.merge_parts(str(progress_path)) is True
        final_path = project_dir / "md" / "book.md"
        assert final_path.is_file()
        assert final_path.read_text(encoding="utf-8").count("Repeated") == 2
        assert not progress_path.exists()

    in_temporary_project(scenario)


def test_merge_finalizes_complete_unique_run() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "output_parts"
        output_dir.mkdir()
        progress_path = output_dir / "progress.json"
        chunks = [completed_chunk(1, 1), completed_chunk(2, 2)]
        write_progress(progress_path, chunks)
        for chunk in chunks:
            page_number = chunk["start_page"]
            (output_dir / chunk["output_file"]).write_text(
                f"--- Page {page_number} ---\nUnique {page_number}",
                encoding="utf-8",
            )
        assert merge_parts_module.merge_parts(str(progress_path)) is True
        final_path = project_dir / "md" / "book.md"
        assert final_path.is_file()
        assert "Unique 1" in final_path.read_text(encoding="utf-8")
        assert not progress_path.exists()
        assert not list(output_dir.glob("part_*_output.md"))

    in_temporary_project(scenario)


TESTS = [
    test_page_ranges_keep_only_document_intersection,
    test_invalid_selection_creates_no_workspace,
    test_split_refuses_stale_workspace,
    test_split_writes_canonical_selected_page_state,
    test_split_rejects_nonpositive_chunk_size,
    test_marker_validation_rejects_prefixed_content,
    test_validation_commits_state_before_removing_raw_file,
    test_validation_counts_missing_parts_as_skipped,
    test_validation_cli_missing_progress_exits_with_failure,
    test_merge_aborts_when_validated_part_is_missing,
    test_merge_rejects_output_path_escape,
    test_merge_preserves_existing_final_output,
    test_merge_allows_identical_page_contents,
    test_merge_finalizes_complete_unique_run,
]


def main() -> int:
    failures = []
    for test_function in TESTS:
        try:
            test_function()
            print(f"PASS: {test_function.__name__}")
        except Exception as error:
            failures.append((test_function.__name__, error))
            print(f"FAIL: {test_function.__name__}: {error}", file=sys.stderr)
    print(f"\n{len(TESTS) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
