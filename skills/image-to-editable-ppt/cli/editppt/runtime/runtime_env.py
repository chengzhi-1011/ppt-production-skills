#!/usr/bin/env python3
import argparse
import json
import os
import importlib.util
import sys
import stat
from pathlib import Path

try:
    from .api_config import BaseUrlError, validate_base_url
except ImportError:
    from api_config import BaseUrlError, validate_base_url


DEFAULT_CONFIG_HOME = "~/.editppt"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
CONFIG_FIELDS = ("OPENAI_BASE_URL", "IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL")
SECRET_ENV_FIELDS = ("OPENAI_API_KEY", "PADDLE_OCR_TOKEN")
STATUS_FIELDS = SECRET_ENV_FIELDS + CONFIG_FIELDS
PADDLE_TOKEN_APPLY_URL = "https://aistudio.baidu.com/account/accessToken"


def cli_reinstall_hint():
    return "`uv tool install --force <path-to-image-to-editable-ppt>/cli`"


def runtime_home():
    return Path(os.getenv("EDITPPT_CONFIG_HOME", DEFAULT_CONFIG_HOME)).expanduser()


def config_path(home=None):
    return (home or runtime_home()) / "config.yaml"


def read_config_file(path):
    path = Path(path).expanduser()
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(
            f"PyYAML is required to read editppt config. Reinstall with {cli_reinstall_hint()}."
        ) from exc
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid config file: {path}")
    return data


def write_config_file(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(
            f"PyYAML is required to write editppt config. Reinstall with {cli_reinstall_hint()}."
        ) from exc
    data = {key: values[key] for key in CONFIG_FIELDS if values.get(key)}
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=True)
    except OSError:
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=True)
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def config(args):
    home = runtime_home()
    raw_values = read_config_file(config_path(home))
    values = {key: raw_values[key] for key in CONFIG_FIELDS if raw_values.get(key)}
    before = dict(values)
    removed_secret_fields = sorted(key for key in SECRET_ENV_FIELDS if raw_values.get(key))
    if args.base_url is not None:
        values["OPENAI_BASE_URL"] = args.base_url.strip()
    if args.clear_base_url:
        values.pop("OPENAI_BASE_URL", None)
    if args.model is not None:
        values["IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL"] = args.model.strip()
    if values.get("OPENAI_BASE_URL"):
        try:
            values["OPENAI_BASE_URL"] = validate_base_url(values["OPENAI_BASE_URL"])
        except BaseUrlError as exc:
            raise SystemExit(f"Invalid OPENAI_BASE_URL: {exc}") from exc
    changed = sorted(key for key in CONFIG_FIELDS if before.get(key) != values.get(key))
    if changed or removed_secret_fields or not config_path(home).exists():
        write_config_file(config_path(home), values)
        state = "updated" if raw_values else "created"
    else:
        state = "unchanged"
    print(f"config={state} path={config_path(home)}")
    if changed:
        print(f"changed={', '.join(changed)}")
    else:
        print("changed=<none>")
    if removed_secret_fields:
        print(f"removed_plaintext_secret_fields={', '.join(removed_secret_fields)}")
    for key in CONFIG_FIELDS:
        print(f"{key}={values.get(key) or '<unset>'}")
    for key in SECRET_ENV_FIELDS:
        print(f"{key}={'set in environment' if os.getenv(key) else '<unset>'}")
    return 0


def current_python_has_module(module):
    return importlib.util.find_spec(module) is not None


def collect_status(check_api=False):
    home = runtime_home()
    raw_config_values = read_config_file(config_path(home))
    values = {key: raw_config_values[key] for key in CONFIG_FIELDS if raw_config_values.get(key)}
    for key in STATUS_FIELDS:
        if os.getenv(key):
            values[key] = os.environ[key]
    if values.get("OPENAI_BASE_URL"):
        try:
            values["OPENAI_BASE_URL"] = validate_base_url(values["OPENAI_BASE_URL"])
        except BaseUrlError as exc:
            raise SystemExit(f"Invalid OPENAI_BASE_URL: {exc}") from exc
    dependencies = {
        module: current_python_has_module(module)
        for module in ("pypdfium2", "PIL", "openai", "yaml", "numpy", "requests")
    }
    api_key = values.get("OPENAI_API_KEY", "")
    api_ready = bool(api_key)
    image_backend_ready = api_ready
    ok = all(dependencies.values()) and (image_backend_ready if check_api else True)
    return {
        "ok": ok,
        "config_home": str(home),
        "config_file": str(config_path(home)),
        "config_exists": config_path(home).exists(),
        "cli_python": sys.executable,
        "dependencies": dependencies,
        "config_keys": sorted(key for key in CONFIG_FIELDS if raw_config_values.get(key)),
        "deprecated_plaintext_secret_fields": sorted(
            key for key in SECRET_ENV_FIELDS if raw_config_values.get(key)
        ),
        "api_backend": {
            "ready": api_ready,
            "OPENAI_API_KEY": "set" if api_key else "unset",
            "OPENAI_BASE_URL": values.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL": values.get(
                "IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL",
                DEFAULT_IMAGE_MODEL,
            ),
        },
        "image_backend": {
            "ready": image_backend_ready,
            "selection": "openai-compatible-api" if api_ready else "missing",
        },
        "text_hints": {
            "selection": "paddleocr-vl" if values.get("PADDLE_OCR_TOKEN") else "builtin-ink",
            "paddle_token": "set" if values.get("PADDLE_OCR_TOKEN") else "unset",
            "apply_url": PADDLE_TOKEN_APPLY_URL,
            "configure_command": "set PADDLE_OCR_TOKEN securely in the process environment",
        },
        "network_approval": {
            "commands": [
                "editppt prepare / editppt run hints when PADDLE_OCR_TOKEN is set",
                "editppt image generate/edit",
            ],
            "justification": (
                "User-requested image-to-editable-ppt conversion; uploads are limited to "
                "task-local page images, prompts, masks, and references required by OCR/image backends."
            ),
            "paddle_rejection_guidance": (
                "If PaddleOCR approval is rejected, ask the user to authorize OCR explicitly; "
                "explain that OCR corrects text boxes, font sizes, and size groups so reconstructed "
                "PPT text sizing stays stable."
            ),
        },
        "next": "no action needed" if ok else (
            "set OPENAI_API_KEY securely in the process environment" if check_api and not image_backend_ready
            else cli_reinstall_hint().strip("`")
        ),
    }


def doctor(args):
    status = collect_status(check_api=args.check_api)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["ok"] else 1

    api = status["api_backend"]
    image_backend = status["image_backend"]
    print(f"config home: {status['config_home']}")
    print(f"cli python: {status['cli_python']}")
    print(f"config file: {status['config_file']} ({'exists' if status['config_exists'] else 'missing'})")
    if status["deprecated_plaintext_secret_fields"]:
        print(
            "warning: config file contains deprecated plaintext secret fields; "
            "run `editppt config` once to remove them"
        )
    print(f"OPENAI_API_KEY={api['OPENAI_API_KEY']}")
    print(f"OPENAI_BASE_URL={api['OPENAI_BASE_URL']}")
    print(f"IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL={api['IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL']}")
    print(f"image backend={image_backend['selection']}")
    hints = status["text_hints"]
    print(f"text hints={hints['selection']} (PADDLE_OCR_TOKEN {hints['paddle_token']})")
    print(
        "network approval: in restricted agents, request approval up front for OCR/image backend "
        "commands; conversion uploads are limited to task-local page images, prompts, masks, "
        "and references."
    )
    print(
        "paddle approval rejection: ask the user to authorize OCR and explain that it corrects "
        "text boxes, font sizes, and size groups so reconstructed PPT text sizing stays stable."
    )
    if hints["paddle_token"] == "unset":
        print(
            "text hints: ASK THE USER once — a PaddleOCR token makes text hints content-aware "
            "(recognized text + cleaner blocks) and can improve text fidelity. The user should "
            "review the provider's current quota, pricing, privacy, and service terms. They can apply at "
            "{url} and set PADDLE_OCR_TOKEN securely in the process environment; or they can choose "
            "to continue with the offline detector. "
            "Wait for their choice before reconstructing pages, then do not ask again.".format(
                url=hints["apply_url"], cmd=hints["configure_command"]
            )
        )
    for module, module_ok in status["dependencies"].items():
        print(f"python import {module}: {'ok' if module_ok else 'missing'}")
    if not all(status["dependencies"].values()):
        print(f"dependency install hint: run {cli_reinstall_hint()}.")
    if args.check_api:
        if image_backend["ready"]:
            print("image backend check: configured (network probe not performed by doctor)")
        else:
            print("image backend check: OPENAI_API_KEY is missing")
    print(f"next: {status['next']}")
    return 0 if status["ok"] else 1


def main():
    parser = argparse.ArgumentParser(
        prog="editppt",
        description="Manage editppt image API configuration",
    )
    sub = parser.add_subparsers(required=True)
    doc = sub.add_parser("doctor", help="Check Python dependencies and image API config")
    doc.add_argument("--check-api", action="store_true", help="Require API credentials to be configured.")
    doc.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    doc.add_argument("--timeout", type=int, default=30, help="Reserved timeout value for future network probes.")
    doc.set_defaults(func=doctor)
    cfg = sub.add_parser("config", help="Write or update ~/.editppt/config.yaml")
    cfg.add_argument(
        "--base-url",
        help=(
            "OpenAI-compatible base URL. Remote endpoints require HTTPS; credentials, query "
            "parameters, and fragments are rejected."
        ),
    )
    cfg.add_argument("--clear-base-url", action="store_true", help="Remove OPENAI_BASE_URL from the config file.")
    cfg.add_argument("--model", help="Default image model for the configured API.")
    cfg.set_defaults(func=config)
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
