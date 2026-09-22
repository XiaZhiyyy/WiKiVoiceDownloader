"""Application entry. Resolve output paths relative to this project or the EXE."""
import sys

def run():
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.")
        return 1
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        from wiki_voice_downloader.cli import main
    except ModuleNotFoundError as exc:
        print(f"Missing dependency: {exc.name}. Run setup.bat or pip install -r requirements.txt")
        return 1
    return main()

if __name__ == "__main__":
    raise SystemExit(run())
