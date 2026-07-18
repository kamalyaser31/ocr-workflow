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
        possible_paths.append(
            os.path.join(program_files, "Pandoc", "pandoc.exe")
        )
    if program_files_x86:
        possible_paths.append(
            os.path.join(program_files_x86, "Pandoc", "pandoc.exe")
        )
    if local_app_data:
        possible_paths.append(
            os.path.join(local_app_data, "Pandoc", "pandoc.exe")
        )

    possible_paths.append(
        os.path.expandvars("%LOCALAPPDATA%\\Pandoc\\pandoc.exe")
    )

    for path in possible_paths:
        if path and os.path.exists(path):
            return path

    return None


def has_arabic(md_path):
    """Detects if the input file contains any Arabic characters."""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read(10000)  # Scan the first 10k characters
            return any(ord(char) in range(0x0600, 0x06FF) for char in content)
    except (OSError, UnicodeDecodeError):
        return False


def markdown_to_docx(md_path: str, docx_path: str) -> bool:
    """Converts a Markdown file into a Word Document (.docx) using Pandoc."""
    if not os.path.exists(md_path):
        print(f"Error: Markdown file not found at {md_path}")
        return False

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

    # Build Pandoc command
    cmd = [pandoc_bin, md_path, "-o", docx_path]

    # Configure RTL if Arabic text is detected
    if has_arabic(md_path):
        cmd.extend(["-M", "dir=rtl"])
        print("Arabic text detected. Enabled Right-to-Left (RTL) formatting.")

    # Check for reference template document for styling
    script_dir = os.path.dirname(os.path.abspath(__file__))
    reference_doc_path = os.path.join(script_dir, "reference.docx")
    if os.path.exists(reference_doc_path):
        cmd.extend(["--reference-doc", reference_doc_path])
        print(f"Using reference document for styling: {reference_doc_path}")

    print(f"Executing: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode == 0:
            print(
                f"Success: Converted '{md_path}' to '{docx_path}' "
                "using Pandoc."
            )
            return True
        else:
            print(f"Pandoc error (Code {result.returncode}): {result.stderr}")
            return False
    except Exception as e:
        print(f"Error executing Pandoc conversion: {e}")
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
    parser.add_argument(
        "output_docx", help="Path to the output Word Document (.docx)."
    )

    args = parser.parse_args()
    success = markdown_to_docx(args.input_md, args.output_docx)
    sys.exit(0 if success else 1)
