import sys
from splitt_pdf import split_pdf

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python init_workflow.py <input_pdf> [output_dir] [pages_per_file]")
        sys.exit(1)
        
    input_pdf = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "output_parts"
    pages_per_file = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    
    split_pdf(input_pdf, output_dir, pages_per_file)
