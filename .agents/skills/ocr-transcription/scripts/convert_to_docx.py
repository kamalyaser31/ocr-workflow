import os
import sys
import subprocess
import shutil

# Force UTF-8 for stdout to prevent Windows encoding errors
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def find_pandoc():
    """Finds the pandoc executable in PATH or default installation locations."""
    # Check standard PATH
    pandoc_path = shutil.which("pandoc")
    if pandoc_path:
        return "pandoc"
    
    # Check typical Windows installation directories
    possible_paths = [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Pandoc", "pandoc.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Pandoc", "pandoc.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Pandoc", "pandoc.exe"),
        os.path.expandvars("%LOCALAPPDATA%\\Pandoc\\pandoc.exe")
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
            
    return None

def install_pandoc():
    """Attempts silent installation of Pandoc via winget."""
    print("Pandoc was not found. Attempting silent installation via winget...")
    cmd = [
        "winget", "install", 
        "-e", "--id", "JohnMacFarlane.Pandoc", 
        "--silent", 
        "--accept-source-agreements", 
        "--accept-package-agreements"
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            print("Pandoc installed successfully via winget.")
            return True
        else:
            print(f"Winget install returned non-zero code ({result.returncode}): {result.stderr}")
    except Exception as e:
        print(f"Failed to execute winget command: {e}")
    return False

def has_arabic(md_path):
    """Detects if the input file contains any Arabic characters."""
    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read(10000)  # Scan the first 10k characters
            return any(ord(char) in range(0x0600, 0x06FF) for char in content)
    except Exception:
        return False

def markdown_to_docx(md_path: str, docx_path: str) -> bool:
    """Converts a Markdown file into a Word Document (.docx) using Pandoc."""
    if not os.path.exists(md_path):
        print(f"Error: Markdown file not found at {md_path}")
        return False
        
    output_dir = os.path.dirname(docx_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        
    # Locate Pandoc
    pandoc_bin = find_pandoc()
    if not pandoc_bin:
        # Try installing silently
        installed = install_pandoc()
        if installed:
            pandoc_bin = find_pandoc()
            
    if not pandoc_bin:
        print("Error: Pandoc is required but could not be found or installed.")
        print("Please install Pandoc manually from https://pandoc.org/installing.html or run:")
        print("  winget install JohnMacFarlane.Pandoc")
        return False
        
    # Build Pandoc command
    cmd = [pandoc_bin, md_path, "-o", docx_path]
    
    # Configure RTL if Arabic text is detected
    if has_arabic(md_path):
        cmd.extend(["-M", "dir=rtl"])
        print("Arabic text detected. Enabled Right-to-Left (RTL) formatting.")
        
    print(f"Executing: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            print(f"Success: Converted '{md_path}' to '{docx_path}' using Pandoc.")
            return True
        else:
            print(f"Pandoc error (Code {result.returncode}): {result.stderr}")
            return False
    except Exception as e:
        print(f"Error executing Pandoc conversion: {e}")
        return False

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Convert Markdown to Word Document (.docx) using Pandoc.")
    parser.add_argument("input_md", help="Path to the input Markdown file.")
    parser.add_argument("output_docx", help="Path to the output Word Document (.docx).")
    
    args = parser.parse_args()
    success = markdown_to_docx(args.input_md, args.output_docx)
    sys.exit(0 if success else 1)
