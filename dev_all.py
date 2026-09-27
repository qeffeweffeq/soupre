import socket
import subprocess
import os
import sys
import threading

def find_free_port(start_port):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                port += 1

def run_process(cmd, env, prefix):
    process = subprocess.Popen(cmd, shell=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        print(f"[{prefix}] {line}", end='')
    process.wait()

if __name__ == "__main__":
    backend_port = find_free_port(8000)
    frontend_port = find_free_port(3000)
    
    print(f"🚀 Starting Soupre Backend on port {backend_port}...")
    print(f"🚀 Starting Soupre Frontend on port {frontend_port}...")
    
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_URL"] = f"http://localhost:{backend_port}"
    env["PORT"] = str(frontend_port)
    env["FORCE_COLOR"] = "1"
    
    backend_cmd = f"cd backend && fish -c 'source venv/bin/activate.fish && uvicorn main:app --port {backend_port} --reload --use-colors'"
    frontend_cmd = f"cd frontend && npm run dev"
    
    t1 = threading.Thread(target=run_process, args=(backend_cmd, env, "backend"))
    t2 = threading.Thread(target=run_process, args=(frontend_cmd, env, "frontend"))
    
    t1.start()
    t2.start()
    
    try:
        t1.join()
        t2.join()
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
