import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
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
pdf_to_images_module = load_script("pdf_to_images_module", "pdf_to_images.py")
convert_to_docx_module = load_script("convert_to_docx_module", "convert_to_docx.py")
pdf_inspect_module = load_script("pdf_inspect_module", "pdf_inspect.py")



def assert_raises(expected_exception, operation):
    try:
        operation()
    except expected_exception:
        return
    raise AssertionError(f"Expected {expected_exception.__name__} to be raised.")


def create_pdf(
    pdf_path: Path, page_count: int, width: int = 72, height: int = 72
) -> None:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=width, height=height)
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


def run_script(script_name: str, arguments: list[str], cwd: Path):
    """Run one project CLI from the requested workspace."""
    command = [
        sys.executable,
        "-B",
        str(SCRIPTS_DIR / script_name),
        *arguments,
    ]
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def image_render_request(
    project_dir: Path,
    pdf_path: Path,
    pages: list[int],
    *,
    pages_str: str | None = None,
    pages_per_file: int = 20,
):
    """Build a real image-render request for a temporary project."""
    return pdf_to_images_module.ImageRenderRequest(
        input_pdf=pdf_path,
        output_dir=project_dir / "output_images",
        parts_dir=project_dir / "output_parts",
        pages=tuple(pages),
        pages_str=pages_str,
        pages_per_file=pages_per_file,
    )


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


def test_split_preserves_every_nonempty_workspace() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        create_pdf(pdf_path, 2)
        preserved_files = {
            "orphan": ("part_1_temp.md", "earlier run"),
            "corrupt": ("progress.json", "{not-json"),
            "completed": (
                "progress.json",
                json.dumps(
                    {
                        "chunks": [
                            {
                                "part": 1,
                                "status": "completed",
                            }
                        ]
                    }
                ),
            ),
        }
        for case_name, (filename, content) in preserved_files.items():
            output_dir = project_dir / case_name / "output_parts"
            output_dir.mkdir(parents=True)
            sentinel_path = output_dir / filename
            sentinel_path.write_text(content, encoding="utf-8")
            assert_raises(
                RuntimeError,
                lambda output_dir=output_dir: split_pdf_module.split_pdf(
                    str(pdf_path), str(output_dir)
                ),
            )
            assert sentinel_path.read_text(encoding="utf-8") == content

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


def test_external_pdf_split_outputs_stay_in_cwd() -> None:
    def scenario(project_dir: Path) -> None:
        source_dir = project_dir / "external_source"
        workspace_dir = project_dir / "workspace"
        source_dir.mkdir()
        workspace_dir.mkdir()
        pdf_path = source_dir / "book.pdf"
        create_pdf(pdf_path, 2)

        completed_process = run_script(
            "splitt_pdf.py",
            [str(pdf_path), "--pages", "1-2"],
            workspace_dir,
        )

        assert completed_process.returncode == 0, completed_process.stderr
        assert (workspace_dir / "output_parts" / "progress.json").is_file()
        assert list((workspace_dir / "output_parts").glob("book_part_*.pdf"))
        assert not (source_dir / "output_parts").exists()

    in_temporary_project(scenario)


def test_pdf_to_images_renders_fixed_jpegs_and_resumes() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        create_pdf(pdf_path, 3)
        request = image_render_request(project_dir, pdf_path, [1, 2, 3])
        pdf_to_images_module.render_images(request)
        image_paths = sorted(request.output_dir.glob("chunk_*/*.jpg"))
        assert len(image_paths) == 3
        for image_path in image_paths:
            with Image.open(image_path) as image:
                assert image.format == "JPEG"
                assert image.mode == "RGB"
                assert image.size == (200, 200)
        first_bytes = [path.read_bytes() for path in image_paths]
        progress = json.loads(
            (request.parts_dir / "progress.json").read_text(encoding="utf-8")
        )
        assert progress["pipeline"] == "images"
        assert progress["dpi"] == 200
        assert progress["max_long_edge"] == 2400
        assert progress["full_page_image_coverage"] == 0.9
        assert progress["image_extension"] == ".jpg"
        assert progress["image_format"] == "JPEG"
        assert progress["jpeg_quality"] == 92
        assert "project_root" not in progress
        assert progress["total_selected_pages"] == 3
        pdf_to_images_module.render_images(request)
        assert [path.read_bytes() for path in image_paths] == first_bytes

    in_temporary_project(scenario)


def test_pdf_to_images_caps_long_edge_without_changing_aspect_ratio() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "wide.pdf"
        create_pdf(pdf_path, 1, width=1440, height=720)
        request = image_render_request(project_dir, pdf_path, [1])

        pdf_to_images_module.render_images(request)

        image_path = next(request.output_dir.glob("chunk_*/*.jpg"))
        with Image.open(image_path) as image:
            assert image.size == (2400, 1200)

    in_temporary_project(scenario)


def test_full_page_scan_prevents_upscale_and_preserves_overlay() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "scan.pdf"
        scan_bytes = BytesIO()
        Image.new("RGB", (100, 100), "white").save(scan_bytes, format="JPEG")
        with pdf_to_images_module.fitz.open() as document:
            page = document.new_page(width=72, height=72)
            page.insert_image(page.rect, stream=scan_bytes.getvalue())
            page.draw_rect(
                pdf_to_images_module.fitz.Rect(0, 0, 8, 8),
                color=(0, 0, 0),
                fill=(0, 0, 0),
                overlay=True,
            )
            document.save(pdf_path)
        request = image_render_request(project_dir, pdf_path, [1])

        pdf_to_images_module.render_images(request)

        image_path = next(request.output_dir.glob("chunk_*/*.jpg"))
        with Image.open(image_path) as image:
            assert image.size == (100, 100)
            assert sum(image.getpixel((5, 5))) < 100

    in_temporary_project(scenario)


def test_external_pdf_image_outputs_stay_in_cwd() -> None:
    def scenario(project_dir: Path) -> None:
        source_dir = project_dir / "external_source"
        workspace_dir = project_dir / "workspace"
        source_dir.mkdir()
        workspace_dir.mkdir()
        pdf_path = source_dir / "book.pdf"
        create_pdf(pdf_path, 2)

        completed_process = run_script(
            "pdf_to_images.py",
            [str(pdf_path)],
            workspace_dir,
        )

        assert completed_process.returncode == 0, completed_process.stderr
        assert (workspace_dir / "output_parts" / "progress.json").is_file()
        assert len(list((workspace_dir / "output_images").glob("chunk_*/*.jpg"))) == 2
        assert not (source_dir / "output_parts").exists()
        assert not (source_dir / "output_images").exists()

    in_temporary_project(scenario)


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


def test_merge_ignores_untrusted_project_root() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "workspace" / "output_parts"
        output_dir.mkdir(parents=True)
        progress_path = output_dir / "progress.json"
        outside_root = project_dir / "outside"
        chunk = completed_chunk(1, 1)
        write_progress(
            progress_path,
            [chunk],
            project_root=str(outside_root),
        )
        (output_dir / chunk["output_file"]).write_text(
            "--- Page 1 ---\nUnique text",
            encoding="utf-8",
        )

        assert merge_parts_module.merge_parts(str(progress_path)) is True
        assert (project_dir / "workspace" / "md" / "book.md").is_file()
        assert not (outside_root / "md" / "book.md").exists()

    in_temporary_project(scenario)


def test_image_merge_removes_tracked_jpegs() -> None:
    def scenario(project_dir: Path) -> None:
        output_dir = project_dir / "output_parts"
        image_dir = project_dir / "output_images" / "chunk_1"
        output_dir.mkdir()
        image_dir.mkdir(parents=True)
        progress_path = output_dir / "progress.json"
        chunk = completed_chunk(1, 1)
        write_progress(
            progress_path,
            [chunk],
            pipeline="images",
            source_file=str(project_dir / "book.pdf"),
            image_extension=".jpg",
        )
        (output_dir / chunk["output_file"]).write_text(
            "--- Page 1 ---\nUnique text", encoding="utf-8"
        )
        image_path = image_dir / "book_p001.jpg"
        image_path.write_bytes(b"rendered image")

        assert merge_parts_module.merge_parts(str(progress_path)) is True
        assert not image_path.exists()
        assert not (project_dir / "output_images").exists()

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


def test_convert_to_docx_conversion() -> None:
    def scenario(project_dir: Path) -> None:
        md_path = project_dir / "input.md"
        docx_path = project_dir / "output.docx"
        md_path.write_text(
            "---\ntitle: Literal source line\n---\nBody text.",
            encoding="utf-8",
        )

        pandoc_bin = convert_to_docx_module.find_pandoc()
        if pandoc_bin is None:
            success = convert_to_docx_module.markdown_to_docx(
                str(md_path), str(docx_path)
            )
            assert success is False
            assert not docx_path.exists()
        else:
            success = convert_to_docx_module.markdown_to_docx(
                str(md_path), str(docx_path)
            )
            assert success is True
            assert docx_path.is_file()
            with zipfile.ZipFile(docx_path) as docx_archive:
                document_xml = docx_archive.read("word/document.xml").decode("utf-8")
            assert "title: Literal source line" in document_xml

    in_temporary_project(scenario)


def test_convert_to_docx_raises_on_overwrite() -> None:
    def scenario(project_dir: Path) -> None:
        md_path = project_dir / "input.md"
        docx_path = project_dir / "output.docx"
        md_path.write_text("Some markdown content.", encoding="utf-8")
        docx_path.write_text("Existing word file.", encoding="utf-8")

        assert_raises(
            FileExistsError,
            lambda: convert_to_docx_module.markdown_to_docx(
                str(md_path), str(docx_path)
            ),
        )

    in_temporary_project(scenario)


def test_pdf_to_images_preserves_invalid_progress() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        create_pdf(pdf_path, 1)
        invalid_states = {
            "corrupt": "{not-json",
            "conflicting": json.dumps(
                {
                    "source_file": str(pdf_path.resolve()),
                    "pipeline": "images",
                    "dpi": 144,
                    "total_selected_pages": 1,
                    "chunks": [
                        {
                            "part": 1,
                            "start_page": 1,
                            "end_page": 1,
                        }
                    ],
                }
            ),
        }
        for case_name, state_text in invalid_states.items():
            case_dir = project_dir / case_name
            parts_dir = case_dir / "output_parts"
            parts_dir.mkdir(parents=True)
            progress_path = parts_dir / "progress.json"
            progress_path.write_text(state_text, encoding="utf-8")
            request = pdf_to_images_module.ImageRenderRequest(
                input_pdf=pdf_path,
                output_dir=case_dir / "output_images",
                parts_dir=parts_dir,
                pages=(1,),
            )
            assert_raises(
                RuntimeError,
                lambda request=request: pdf_to_images_module.render_images(request),
            )
            assert progress_path.read_text(encoding="utf-8") == state_text

    in_temporary_project(scenario)


def test_custom_page_range_single_chunk_cleanup() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        output_dir = project_dir / "output_parts"
        create_pdf(pdf_path, 10)

        split_pdf_module.split_pdf(
            str(pdf_path), str(output_dir), "1-2", pages_per_file=20
        )
        progress_path = output_dir / "progress.json"

        assert progress_path.is_file()
        progress_data = json.loads(progress_path.read_text(encoding="utf-8"))

        assert progress_data["is_split"] is True
        assert len(progress_data["chunks"]) == 1

        part_1_temp = output_dir / "part_1_temp.md"
        part_1_temp.write_text(
            "--- Page 1 ---\nContent 1\n--- Page 2 ---\nContent 2", encoding="utf-8"
        )

        passed, failed, skipped = validate_chunk_module.run_validation(
            [1], str(progress_path), str(output_dir)
        )
        assert passed == 1 and failed == 0 and skipped == 0

        assert merge_parts_module.merge_parts(str(progress_path)) is True

        assert not progress_path.exists()
        assert not list(output_dir.glob("book_part_*.pdf"))
        assert not list(output_dir.glob("part_*_output.md"))

    in_temporary_project(scenario)


def test_validation_resume_all_completed_chunks() -> None:
    def scenario(project_dir: Path) -> None:
        progress_path = project_dir / "output_parts" / "progress.json"
        progress_path.parent.mkdir(exist_ok=True)

        chunks = [
            {
                "part": 1,
                "filename": "book_part_1.pdf",
                "start_page": 1,
                "end_page": 1,
                "status": "completed",
                "output_file": "part_1_output.md",
            },
            {
                "part": 2,
                "filename": "book_part_2.pdf",
                "start_page": 2,
                "end_page": 2,
                "status": "pending",
                "output_file": "",
            },
        ]
        write_progress(progress_path, chunks)

        part_1_output = progress_path.parent / "part_1_output.md"
        part_1_output.write_text("--- Page 1 ---\nContent 1", encoding="utf-8")

        part_2_temp = progress_path.parent / "part_2_temp.md"
        part_2_temp.write_text("--- Page 2 ---\nContent 2", encoding="utf-8")

        passed, failed, skipped = validate_chunk_module.run_validation(
            [1, 2], str(progress_path), str(progress_path.parent)
        )

        assert passed == 2
        assert failed == 0
        assert skipped == 0

    in_temporary_project(scenario)


def test_convert_to_docx_atomic_cleanup_on_failure() -> None:
    def scenario(project_dir: Path) -> None:
        md_path = project_dir / "input.md"
        docx_path = project_dir / "output.docx"
        preexisting_path = project_dir / "output.tmp.docx"
        md_path.write_text("Some content.", encoding="utf-8")
        preexisting_path.write_text("preserve me", encoding="utf-8")
        partial_outputs = []

        def fail_after_partial_output(command, **_kwargs):
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text("partial output", encoding="utf-8")
            partial_outputs.append(output_path)
            return SimpleNamespace(returncode=1, stderr="forced failure")

        original_which = convert_to_docx_module.shutil.which
        original_run = convert_to_docx_module.subprocess.run
        convert_to_docx_module.shutil.which = lambda _name: "pandoc"
        convert_to_docx_module.subprocess.run = fail_after_partial_output
        try:
            success = convert_to_docx_module.markdown_to_docx(
                str(md_path), str(docx_path)
            )
            assert success is False
            assert not docx_path.exists()
            assert preexisting_path.read_text(encoding="utf-8") == "preserve me"
            assert len(partial_outputs) == 1
            assert not partial_outputs[0].exists()
        finally:
            convert_to_docx_module.shutil.which = original_which
            convert_to_docx_module.subprocess.run = original_run

    in_temporary_project(scenario)


def test_pdf_inspect_missing_binary() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        create_pdf(pdf_path, 2)
        
        # Mock find_detect_pdf to return None
        original_find = pdf_inspect_module.find_detect_pdf
        pdf_inspect_module.find_detect_pdf = lambda: None
        try:
            assert_raises(
                FileNotFoundError,
                lambda: pdf_inspect_module.run_inspection(
                    pdf_path,
                    project_dir / "output_parts",
                    "full",
                    None,
                    False,
                )
            )
        finally:
            pdf_inspect_module.find_detect_pdf = original_find

    in_temporary_project(scenario)


def test_pdf_inspect_invalid_strategy() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        create_pdf(pdf_path, 2)
        
        assert_raises(
            ValueError,
            lambda: pdf_inspect_module.run_inspection(
                pdf_path,
                project_dir / "output_parts",
                "invalid-strategy-name",
                None,
                False,
            )
        )

    in_temporary_project(scenario)


def test_pdf_inspect_cli_missing_binary() -> None:
    def scenario(project_dir: Path) -> None:
        pdf_path = project_dir / "book.pdf"
        create_pdf(pdf_path, 2)
        
        # Override PATH in env to ensure detect-pdf is missing
        env_override = os.environ.copy()
        env_override["PATH"] = ""
        # Also clean USERPROFILE and LOCALAPPDATA to prevent cargo fallback checks
        env_override["USERPROFILE"] = ""
        env_override["LOCALAPPDATA"] = ""
        
        # Run script via CLI
        command = [
            sys.executable,
            "-B",
            str(SCRIPTS_DIR / "pdf_inspect.py"),
            str(pdf_path),
        ]
        completed = subprocess.run(
            command,
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env_override,
        )
        assert completed.returncode == 1
        assert "not found on PATH" in completed.stderr
        assert "cargo install pdf-inspector" in completed.stderr

    in_temporary_project(scenario)


TESTS = [
    test_pdf_inspect_missing_binary,
    test_pdf_inspect_invalid_strategy,
    test_pdf_inspect_cli_missing_binary,
    test_page_ranges_keep_only_document_intersection,
    test_invalid_selection_creates_no_workspace,
    test_split_preserves_every_nonempty_workspace,
    test_split_writes_canonical_selected_page_state,
    test_external_pdf_split_outputs_stay_in_cwd,
    test_pdf_to_images_renders_fixed_jpegs_and_resumes,
    test_pdf_to_images_caps_long_edge_without_changing_aspect_ratio,
    test_full_page_scan_prevents_upscale_and_preserves_overlay,
    test_external_pdf_image_outputs_stay_in_cwd,
    test_pdf_to_images_preserves_invalid_progress,
    test_validation_commits_state_before_removing_raw_file,
    test_validation_cli_missing_progress_exits_with_failure,
    test_merge_aborts_when_validated_part_is_missing,
    test_merge_rejects_output_path_escape,
    test_merge_ignores_untrusted_project_root,
    test_image_merge_removes_tracked_jpegs,
    test_merge_preserves_existing_final_output,
    test_merge_allows_identical_page_contents,
    test_convert_to_docx_conversion,
    test_convert_to_docx_raises_on_overwrite,
    test_custom_page_range_single_chunk_cleanup,
    test_validation_resume_all_completed_chunks,
    test_convert_to_docx_atomic_cleanup_on_failure,
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
