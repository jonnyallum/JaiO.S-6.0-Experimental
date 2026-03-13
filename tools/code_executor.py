"""
Sandboxed Code Executor — runs Python snippets in a subprocess jail.
Used by data_analyst, business_intelligence, and pipeline eval steps.
"""
import subprocess
import tempfile
import logging
import os

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 10_000


def execute_python(code: str, timeout: int = TIMEOUT_SECONDS) -> dict:
    """
    Execute Python code in a sandboxed subprocess.
    Returns {"stdout": str, "stderr": str, "returncode": int, "error": str|None}.
    """
    if not code or not code.strip():
        return {"stdout": "", "stderr": "", "returncode": -1, "error": "Empty code"}

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, dir="/tmp"
        ) as f:
            f.write(code)
            tmp_path = f.name

        result = subprocess.run(
            ["python3", "-u", tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
                "HOME": "/tmp",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

        stdout = result.stdout[:MAX_OUTPUT_CHARS]
        stderr = result.stderr[:MAX_OUTPUT_CHARS]
        log.info("code_executor.done", returncode=result.returncode, stdout_len=len(stdout))
        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "error": None if result.returncode == 0 else f"Exit code {result.returncode}",
        }
    except subprocess.TimeoutExpired:
        log.warning("code_executor.timeout", timeout=timeout)
        return {"stdout": "", "stderr": "", "returncode": -1, "error": f"Timeout after {timeout}s"}
    except Exception as e:
        log.error("code_executor.error", error=str(e))
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def execute_and_summarize(code: str) -> str:
    """Execute code and return a formatted text summary for agent consumption."""
    r = execute_python(code)
    if r["error"]:
        return f"[Code execution failed: {r['error']}]\n{r['stderr']}"
    output = r["stdout"].strip()
    if not output:
        return "[Code executed successfully but produced no output]"
    return f"Code execution result:\n```\n{output}\n```"
