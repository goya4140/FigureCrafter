from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Any

from .common import write_json
from .schemas import omit_null_fields


class ModelRequestError(RuntimeError):
    """An invalid request must not be disguised as content fallback."""


class ModelExecutionBlocked(RuntimeError):
    """Account/service execution failure requires a checkpointed stop."""


def _usage_limit_reached(stdout: str, stderr: str) -> bool:
    diagnostic = (stdout + "\n" + stderr).casefold()
    return any(marker in diagnostic for marker in (
        "you've hit your usage limit", "usage limit reached",
        "rate limit reset required", "insufficient_quota",
    ))


def _run_model_process(arguments: list[str], input_text: str, timeout: float, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Keep the CLI wrapper and its model child in one reapable process group."""
    process = subprocess.Popen(
        arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=environment, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # The wrapper can exit while a descendant remains with closed pipes.
        # Always clear the process group, even if communicate() returned.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _codex_tier_arguments(model: dict[str, Any]) -> list[str]:
    """Pass the requested tier explicitly because each call ignores user config."""
    tier = model.get("service_tier")
    enabled = model.get("fast_mode")
    if tier is None and enabled is None:
        return []
    if tier == "fast" and enabled is True:
        return ["-c", 'service_tier="fast"', "-c", "features.fast_mode=true"]
    raise ValueError("Fast mode requires model.service_tier='fast' and model.fast_mode=true")


def _external_arguments(
    model: dict[str, Any],
    *,
    stage: str,
    prompt_file: Path,
    schema_file: Path,
    image_file: Path,
) -> list[str]:
    """Build a provider-neutral command from an explicit placeholder contract."""
    command = model.get("command")
    if isinstance(command, str) and command:
        arguments = [command]
    elif isinstance(command, list) and command and all(isinstance(item, str) and item for item in command):
        arguments = list(command)
    else:
        raise ModelRequestError("external-json backend requires model.command as a string or string array")
    raw_arguments = model.get("arguments", [])
    if not isinstance(raw_arguments, list) or not all(isinstance(item, str) for item in raw_arguments):
        raise ModelRequestError("external-json model.arguments must be a string array")
    values = {
        "stage": stage,
        "prompt_file": str(prompt_file.resolve()),
        "schema_file": str(schema_file.resolve()),
        "image_file": str(image_file.resolve()),
    }
    try:
        return arguments + [item.format(**values) for item in raw_arguments]
    except KeyError as exc:
        raise ModelRequestError(f"unknown external-json argument placeholder: {exc.args[0]}") from exc


def _model_environment(model: dict[str, Any], backend: str) -> dict[str, str]:
    """Keep credentials out of Codex calls and explicitly allow them for external adapters."""
    if backend == "codex-cli":
        return {key: value for key, value in os.environ.items() if key not in ("OPENAI_API_KEY", "OPENAI_BASE_URL")}
    allowed = set(model.get("environment_allowlist", []))
    inherited = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    }
    for key in allowed:
        if key in os.environ:
            inherited[key] = os.environ[key]
    return inherited


def invoke_json(
    stage: str,
    prompt: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    image: str | Path,
    config: dict[str, Any],
    directory: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call a tool-free model. Content failures return an empty proposal for deterministic fallback."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "input.json", payload)
    write_json(directory / "schema.json", schema)
    text = prompt + "\nUse null for optional fields that do not apply.\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)
    (directory / "prompt.txt").write_text(text)
    model = config.get("model", {})
    backend = model.get("backend", "codex-cli")
    response = directory / "response.txt"
    if backend == "codex-cli":
        command = model.get("command", "codex")
        arguments = [
            command, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
            "--model", model.get("name", "gpt-5.6-terra"),
            "--output-schema", str((directory / "schema.json").resolve()),
            "-c", "model_reasoning_effort=" + json.dumps(model.get("effort", "medium")),
            "-c", "web_search=\"disabled\"",
            "-c", "project_doc_max_bytes=0",
            "-c", "developer_instructions=\"Use only the attached image and supplied JSON. Use no tools. Return only the requested JSON.\"",
            "-i", str(Path(image).resolve()),
        ]
        arguments += _codex_tier_arguments(model)
        for feature in ("shell_tool", "unified_exec", "multi_agent", "apps", "plugins", "hooks", "browser_use"):
            arguments += ["-c", f"features.{feature}=false"]
    elif backend == "external-json":
        arguments = _external_arguments(
            model,
            stage=stage,
            prompt_file=directory / "prompt.txt",
            schema_file=directory / "schema.json",
            image_file=Path(image),
        )
    else:
        raise ModelRequestError(f"unsupported model backend: {backend}")
    metadata = {
        "stage": stage, "status": "running", "fallback_used": False,
        "backend": backend, "model_name": model.get("name"),
        "service_tier_requested": model.get("service_tier", "standard"),
        "fast_mode_requested": model.get("fast_mode") is True,
    }
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="decomposer-model-") as workspace:
            final_arguments = (
                arguments + ["-C", workspace, "-o", str(response.resolve()), "-"]
                if backend == "codex-cli" else arguments
            )
            completed = _run_model_process(
                final_arguments, text, model.get("timeout_seconds", 1800),
                _model_environment(model, backend),
            )
        if backend == "codex-cli":
            (directory / "events.jsonl").write_text(completed.stdout)
        else:
            response.write_text(completed.stdout)
        (directory / "stderr.log").write_text(completed.stderr)
        if completed.returncode:
            if _usage_limit_reached(completed.stdout, completed.stderr):
                raise ModelExecutionBlocked(f"{stage}: Codex usage limit reached; inspect stderr.log")
            if model.get("service_tier") == "fast":
                raise ModelExecutionBlocked(
                    f"{stage}: Fast-tier Codex call exited {completed.returncode}; inspect stderr.log"
                )
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") in ("error", "turn.failed") and "invalid_json_schema" in line:
                    raise ModelRequestError(f"{stage}: invalid_json_schema; inspect events.jsonl")
            raise RuntimeError(f"model command exited {completed.returncode}")
        raw = response.read_text().strip()
        if raw.startswith("```") and raw.endswith("```"):
            raw = "\n".join(raw.splitlines()[1:-1])
        proposal = omit_null_fields(json.loads(raw))
        metadata["status"] = "complete"
    except ModelExecutionBlocked as exc:
        metadata.update(
            status="execution_blocked", fallback_used=False, error=str(exc),
            seconds=round(time.monotonic() - started, 3),
        )
        write_json(directory / "metadata.json", metadata)
        raise
    except ModelRequestError as exc:
        metadata.update(
            status="request_failed", fallback_used=False, error=str(exc),
            seconds=round(time.monotonic() - started, 3),
        )
        write_json(directory / "metadata.json", metadata)
        raise
    except Exception as exc:
        proposal = {}
        metadata.update(status="degraded", fallback_used=True, error=f"{type(exc).__name__}: {exc}")
    metadata["seconds"] = round(time.monotonic() - started, 3)
    write_json(directory / "metadata.json", metadata)
    return proposal, metadata


def fixture_or_model(
    stage: str,
    prompt: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    image: str | Path,
    config: dict[str, Any],
    directory: str | Path,
    fixture_dir: str | Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if fixture_dir:
        fixture = Path(fixture_dir) / f"{stage}.json"
        if fixture.exists():
            proposal = json.loads(fixture.read_text())
            metadata = {"stage": stage, "status": "fixture", "fallback_used": False, "source": str(fixture)}
            Path(directory).mkdir(parents=True, exist_ok=True)
            write_json(Path(directory) / "input.json", payload)
            write_json(Path(directory) / "response.json", proposal)
            write_json(Path(directory) / "metadata.json", metadata)
            return proposal, metadata
    import hashlib
    identity={'model':config.get('model',{}),'stage':stage,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'image_sha256':hashlib.sha256(Path(image).read_bytes()).hexdigest(),'input':payload,'schema':schema}
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    ip=directory/'call-identity.json';mp=directory/'metadata.json';rp=directory/'response.txt'
    # Import only already-completed upstream requests with identical model,
    # source pixels, prompt, input and output schema. Never import refinement.
    if not mp.exists() and stage in {'recognition','representation'} and directory.parent.name=='calls':
        import shutil
        for source_case in config.get('engineering',{}).get('reuse_upstream_from_cases',[]):
            source=Path(source_case)/'calls'/directory.name
            try:
                md=json.loads((source/'metadata.json').read_text())
                if md.get('status')!='complete' or md.get('fallback_used'):continue
                if md.get('model_name')!=config['model']['name']:continue
                old_input=json.loads((source/'input.json').read_text());old_schema=json.loads((source/'schema.json').read_text())
                old_prompt=(source/'prompt.txt').read_text().split('\nUse null for optional fields that do not apply.\nINPUT:\n',1)[0]
                old_cfg=json.loads((Path(source_case)/'config.json').read_text())['model'] if (Path(source_case)/'config.json').exists() else None
                if old_cfg!=config['model']:continue
                image_hash=hashlib.sha256((Path(source_case)/'input-normalized.png').read_bytes()).hexdigest()
                original={'model':old_cfg,'stage':stage,'prompt_sha256':hashlib.sha256(old_prompt.encode()).hexdigest(),'image_sha256':image_hash,'input':old_input,'schema':old_schema}
                if original!=identity:continue
                raw=(source/'response.txt').read_text().strip()
                if raw.startswith('```') and raw.endswith('```'):raw='\n'.join(raw.splitlines()[1:-1])
                proposal=omit_null_fields(json.loads(raw))
                shutil.copytree(source,directory,dirs_exist_ok=True)
                write_json(ip,identity)
                provenance={'imported_completed_model_call':True,'source_call':str(source.resolve()),'source_case':str(Path(source_case).resolve()),'request_identity_verified':True,'historical_successful_model_seconds':md.get('seconds'),'events_sha256':hashlib.sha256((source/'events.jsonl').read_bytes()).hexdigest() if (source/'events.jsonl').exists() else None,'new_model_invocations':0}
                write_json(directory/'checkpoint-import.json',provenance)
                return proposal,{**md,'checkpoint_reused':True}
            except (OSError,ValueError,KeyError):continue

    if config.get('engineering',{}).get('resume_complete_calls',True) and ip.exists() and mp.exists() and rp.exists():
        previous=json.loads(mp.read_text())
        if json.loads(ip.read_text())==identity and previous.get('status')=='complete' and not previous.get('fallback_used'):
            write_json(directory/'resume-read.json',{'reused_completed_call':True,'identity_checked':True})
            raw=rp.read_text().strip()
            if raw.startswith('```') and raw.endswith('```'):raw='\n'.join(raw.splitlines()[1:-1])
            return omit_null_fields(json.loads(raw)),previous
        raise ValueError('Existing model call identity/status differs: use a fresh attempt directory')
    write_json(ip,identity)
    result = invoke_json(stage, prompt, payload, schema, image, config, directory)
    events = Path(directory) / 'events.jsonl'
    transient = events.exists() and ('Selected model is at capacity' in events.read_text() or 'rate_limit_exceeded' in events.read_text())
    if result[1].get('fallback_used') and transient and config.get('engineering',{}).get('transient_retries',1)>0 and stage not in config.get('engineering',{}).get('retry_already_used_stages',[]):
        import shutil
        archive=Path(directory)/'attempt-001';archive.mkdir(exist_ok=False)
        for file in list(Path(directory).iterdir()):
            if file.is_file():shutil.move(str(file),archive/file.name)
        time.sleep(10)
        write_json(ip,identity)
        result=invoke_json(stage,prompt,payload,schema,image,config,directory)
        write_json(Path(directory)/'retry-provenance.json',{'attempts':2,'reason':'explicit_capacity_or_rate_limit','first_attempt':str(archive),'model_unchanged':True})
    return result
