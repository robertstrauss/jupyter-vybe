#!/usr/bin/env python3
"""
Python client runner for TurboFieldfare inference.
Supports both TurboFieldfareServer (OpenAI-compatible HTTP API) and TurboFieldfareCLI.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

def run_server_completion(host, model_name, prompt, completion_mode, max_tokens, temperature):
    """Executes completion via TurboFieldfareServer HTTP API."""
    if completion_mode == "raw":
        url = f"{host.rstrip('/')}/v1/completions"
        payload = {
            "model": model_name,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
    else:
        url = f"{host.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if completion_mode == "raw":
                            chunk = data["choices"][0].get("text", "")
                        else:
                            chunk = data["choices"][0].get("delta", {}).get("content", "")
                        if chunk:
                            sys.stdout.write(chunk)
                            sys.stdout.flush()
                    except json.JSONDecodeError:
                        continue
    except urllib.error.URLError as e:
        print(f"error: failed to communicate with TurboFieldfare server at {host}: {e}", file=sys.stderr)
        return False
    return True

def run_cli_completion(cli_bin, model_path, prompt, completion_mode, max_tokens, temperature):
    """Executes completion directly via TurboFieldfareCLI binary."""
    cmd = [cli_bin, "--model", model_path, "--max-new", str(max_tokens), "--temperature", str(temperature)]

    if completion_mode == "raw":
        cmd.extend(["--prompt", prompt])
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            print(f"error: TurboFieldfare CLI binary '{cli_bin}' not found.", file=sys.stderr)
            print("Run ./setup_turbofieldfare.sh to build the release binary or set TURBOFIELDFARE_BIN.", file=sys.stderr)
            return False
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read()
            print(f"\nerror: TurboFieldfareCLI failed: {err}", file=sys.stderr)
            if "missing manifest.json" in err or "model.gturbo directory" in err:
                print("\nHINT: The Gemma 4 model weights must be downloaded and repacked into .gturbo format first.", file=sys.stderr)
                print("To download and repack the model (~15 GB download), run:", file=sys.stderr)
                print("  $HOME/.turbofieldfare/turbo-fieldfare/.build/release/TurboFieldfareRepack --output scratch/gemma4.gturbo --overwrite\n", file=sys.stderr)
            return False
    else:
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tf:
            json.dump([{"role": "user", "content": prompt}], tf)
            temp_file_path = tf.name

        try:
            cmd.extend(["--messages-file", temp_file_path])
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            except FileNotFoundError:
                print(f"error: TurboFieldfare CLI binary '{cli_bin}' not found.", file=sys.stderr)
                print("Run ./setup_turbofieldfare.sh to build the release binary or set TURBOFIELDFARE_BIN.", file=sys.stderr)
                return False
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
            proc.wait()
            if proc.returncode != 0:
                err = proc.stderr.read()
                print(f"\nerror: TurboFieldfareCLI failed: {err}", file=sys.stderr)
                if "missing manifest.json" in err or "model.gturbo directory" in err:
                    print("\nHINT: The Gemma 4 model weights must be downloaded and repacked into .gturbo format first.", file=sys.stderr)
                    print("To download and repack the model (~15 GB download), run:", file=sys.stderr)
                    print("  $HOME/.turbofieldfare/turbo-fieldfare/.build/release/TurboFieldfareRepack --output scratch/gemma4.gturbo --overwrite\n", file=sys.stderr)
                return False
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    return True

def resolve_cli_bin():
    if "TURBOFIELDFARE_BIN" in os.environ:
        return os.environ["TURBOFIELDFARE_BIN"]
    default_home = os.path.expanduser("~/.turbofieldfare/turbo-fieldfare/.build/release/TurboFieldfareCLI")
    if os.path.isfile(default_home) and os.access(default_home, os.X_OK):
        return default_home
    return "TurboFieldfareCLI"

def resolve_model_path():
    if "TURBOFIELDFARE_MODEL" in os.environ:
        return os.environ["TURBOFIELDFARE_MODEL"]
    if os.path.exists("scratch/gemma4.gturbo"):
        return "scratch/gemma4.gturbo"
    default_home = os.path.expanduser("~/.turbofieldfare/turbo-fieldfare/scratch/gemma4.gturbo")
    if os.path.exists(default_home):
        return default_home
    return "scratch/gemma4.gturbo"

def main():
    host = os.environ.get("TURBOFIELDFARE_HOST", "http://127.0.0.1:8080")
    cli_bin = resolve_cli_bin()
    model_path = resolve_model_path()
    mode = os.environ.get("TURBOFIELDFARE_RUN_MODE", "auto")
    completion_mode = os.environ.get("COMPLETION_MODE", "chat").lower()
    max_tokens = int(os.environ.get("MAX_TOKENS", "1024"))
    temperature = float(os.environ.get("TEMPERATURE", "0.2"))
    prompt = os.environ.get("FULL_PROMPT", "")

    if not prompt:
        print("error: prompt is empty", file=sys.stderr)
        sys.exit(1)

    # Check if server is running when mode is 'auto' or 'server'
    server_available = False
    if mode in ("auto", "server"):
        try:
            health_req = urllib.request.Request(f"{host.rstrip('/')}/health", method="GET")
            with urllib.request.urlopen(health_req, timeout=1.0) as resp:
                if resp.status == 200:
                    server_available = True
        except Exception:
            server_available = False

    if mode == "server" or (mode == "auto" and server_available):
        success = run_server_completion(host, model_path, prompt, completion_mode, max_tokens, temperature)
        if not success and mode == "auto":
            print("falling back to CLI mode...", file=sys.stderr)
            run_cli_completion(cli_bin, model_path, prompt, completion_mode, max_tokens, temperature)
    else:
        run_cli_completion(cli_bin, model_path, prompt, completion_mode, max_tokens, temperature)

    print()

if __name__ == "__main__":
    main()
