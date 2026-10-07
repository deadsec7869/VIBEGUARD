import os
import subprocess
import time
import socket
from urllib.parse import urlparse
import urllib.request
import urllib.error

class TargetProcessManager:
    def __init__(self, cmd: str, cwd: str, target_url: str):
        self.cmd = cmd
        self.cwd = cwd
        self.target_url = target_url
        self.process = None

    def _is_healthy(self) -> bool:
        # Check HTTP response or socket connectivity
        try:
            req = urllib.request.Request(self.target_url, headers={"User-Agent": "VibeGuard-HealthCheck"})
            with urllib.request.urlopen(req, timeout=1) as resp:
                return resp.status < 500
        except urllib.error.HTTPError as e:
            # Server responded with valid HTTP code (e.g. 200, 302, 401, 404)
            return e.code < 500
        except Exception:
            pass

        # Socket fallback
        u = urlparse(self.target_url)
        port = u.port or (443 if u.scheme == "https" else 80)
        host = u.hostname or "localhost"
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except Exception:
            return False

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, wait_healthy: bool = True, timeout: int = 15) -> bool:
        if self.is_running():
            self.stop()
        
        # On Windows, use shell=True for node/npx commands if passed as string
        shell = os.name == 'nt'
        
        self.process = subprocess.Popen(
            self.cmd, 
            cwd=self.cwd, 
            shell=shell, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        
        if wait_healthy:
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.process.poll() is not None:
                    raise RuntimeError(f"Target process exited unexpectedly with code {self.process.poll()}")
                if self._is_healthy():
                    return True
                time.sleep(0.5)
            self.stop()
            raise RuntimeError(f"Target process did not become healthy at {self.target_url} within {timeout}s")
        return True

    def stop(self):
        if self.process and self.process.poll() is None:
            pid = self.process.pid
            if os.name == 'nt':
                # On Windows, kill process tree for only this specific PID
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(pid)], 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL
                )
            else:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        self.process = None

    def restart(self, wait_healthy: bool = True, timeout: int = 15) -> bool:
        self.stop()
        return self.start(wait_healthy=wait_healthy, timeout=timeout)
