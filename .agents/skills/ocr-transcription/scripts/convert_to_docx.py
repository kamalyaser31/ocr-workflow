import os
import sys
import subprocess
import shutil


def find_pandoc():
    """Find pandoc in PATH or common installation locations."""
    # Check standard PATH
    pandoc_path = shutil.which("pandoc")
    if pandoc_path:
        return "pandoc"

    # Environment-based paths work even before a restarted shell sees PATH.
    program_files = os.environ.get("ProgramFiles", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
    local_app_data = os.environ.get("LOCALAPPDATA", "")

    possible_paths = []
    if program_files:
        possible_paths.append(os.path.join(program_files, "Pandoc", "pandoc.exe"))
    if program_files_x86:
        possible_paths.append(os.path.join(program_files_x86, "Pandoc", "pandoc.exe"))
    if local_app_data:
        possible_paths.append(os.path.join(local_app_data, "Pandoc", "pandoc.exe"))

    possible_paths.append(os.path.expandvars("%LOCALAPPDATA%\\Pandoc\\pandoc.exe"))

    for path in possible_paths:
        if path and os.path.exists(path):
            return path

    return None


def has_arabic(md_path: str) -> bool:
    """Detect if the input file contains any Arabic characters, raising read errors."""
    with open(md_path, "r", encoding="utf-8") as markdown_file:
        while True:
            chunk = markdown_file.read(65536)
            if not chunk:
                break
            for char in chunk:
                code_point = ord(char)
                if (
                    0x0600 <= code_point <= 0x06FF
                    or 0x0750 <= code_point <= 0x077F
                    or 0x08A0 <= code_point <= 0x08FF
                    or 0xFB50 <= code_point <= 0xFDFF
                    or 0xFE70 <= code_point <= 0xFEFF
                ):
                    return True
    return False


def markdown_to_docx(md_path: str, docx_path: str) -> bool:
    """Converts a Markdown file into a Word Document (.docx) using Pandoc."""
    if not os.path.exists(md_path):
        print(f"Error: Markdown file not found at {md_path}")
        return False

    if os.path.exists(docx_path):
        raise FileExistsError(
            f"Output docx file already exists and will not be overwritten: {docx_path}"
        )

    output_dir = os.path.dirname(docx_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    pandoc_bin = find_pandoc()
    if not pandoc_bin:
        print(
            "Error: Pandoc is required but was not found. "
            "No installation was attempted.",
            file=sys.stderr,
        )
        print(
            "Install it from https://pandoc.org/installing.html or run: "
            "winget install JohnMacFarlane.Pandoc",
            file=sys.stderr,
        )
        return False

    pandoc_command = [pandoc_bin, md_path, "-o", docx_path]

    if has_arabic(md_path):
        pandoc_command.extend(["-M", "dir=rtl"])
        print("Arabic text detected. Enabled Right-to-Left (RTL) formatting.")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    reference_doc_path = os.path.join(script_dir, "reference.docx")
    if os.path.exists(reference_doc_path):
        pandoc_command.extend(["--reference-doc", reference_doc_path])
        print(f"Using reference document for styling: {reference_doc_path}")

    print(f"Executing: {' '.join(pandoc_command)}")
    try:
        completed_process = subprocess.run(
            pandoc_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if completed_process.returncode == 0:
            print(f"Success: Converted '{md_path}' to '{docx_path}' " "using Pandoc.")
            return True
        else:
            print(
                f"Pandoc error (Code {completed_process.returncode}): "
                f"{completed_process.stderr}"
            )
            return False
    except (OSError, FileNotFoundError) as error:
        print(f"Error executing Pandoc conversion: {error}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # Force UTF-8 for stdout to prevent Windows encoding errors
    if sys.stdout.encoding != "utf-8":
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    import argparse

    parser = argparse.ArgumentParser(
        description="Convert Markdown to Word Document (.docx) using Pandoc."
    )
    parser.add_argument("input_md", help="Path to the input Markdown file.")
    parser.add_argument("output_docx", help="Path to the output Word Document (.docx).")

    args = parser.parse_args()
    success = markdown_to_docx(args.input_md, args.output_docx)
    sys.exit(0 if success else 1)
