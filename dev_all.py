import socket
import os

def find_free_port(start_port):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                port += 1

if __name__ == "__main__":
    backend_port = find_free_port(8000)
    frontend_port = find_free_port(3000)
    
    print(f"🚀 Starting Soupre Backend on port {backend_port}...")
    print(f"🚀 Starting Soupre Frontend on port {frontend_port}...")
    
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_URL"] = f"http://localhost:{backend_port}"
    env["PORT"] = str(frontend_port)
    env["BACKEND_PORT"] = str(backend_port)
    env["FORCE_COLOR"] = "1"
    
    cmd = [
        "npx", "concurrently", 
        "-c", "green,magentaBright", 
        "-n", "backend,frontend", 
        "npm run dev:backend", 
        "npm run dev:frontend"
    ]
    
    os.execvpe(cmd[0], cmd, env)
