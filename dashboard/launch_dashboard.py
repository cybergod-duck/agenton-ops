import os
import sys
import time
import webbrowser
import subprocess
from pathlib import Path

# Set stdout encoding
sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = Path(r"C:\BC RESEARCH\AI_FACTORY\AgentOn")
API_SCRIPT = ROOT_DIR / "dashboard" / "dashboard_api.py"

def main():
    print("=" * 60)
    print("🚀 Starting Sovereign Earn Stack Dashboard...")
    print("=" * 60)

    # 1. Start the Flask API server
    print(f"Running Flask API server from: {API_SCRIPT}")
    
    # We run it as a subprocess so we can capture stdout and keep running
    p = subprocess.Popen(
        [sys.executable, str(API_SCRIPT)],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding='utf-8',
        errors='ignore'
    )

    # 2. Wait a brief moment and open the browser
    time.sleep(2.0)
    url = "http://localhost:5050"
    print(f"Opening dashboard in your web browser: {url}")
    webbrowser.open(url)

    print("\nPress Ctrl+C to stop the dashboard server.")
    print("Server Logs:")
    print("-" * 60)

    try:
        # Stream the logs to stdout
        while True:
            line = p.stdout.readline()
            if not line and p.poll() is not None:
                break
            if line:
                print(line.strip())
    except KeyboardInterrupt:
        print("\nStopping dashboard server...")
    finally:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
        print("Dashboard stopped.")

if __name__ == "__main__":
    main()
