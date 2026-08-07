from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from PIL import Image


CLI_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = CLI_ROOT / "editppt" / "runtime"
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RUNTIME_DIR))

import configure_image_backend  # noqa: E402
import api_config  # noqa: E402
import deck_text_hints  # noqa: E402
import image_gen  # noqa: E402
import paddle_text_hints  # noqa: E402
import runtime_env  # noqa: E402
from _input_normalization import render_pdf_pages  # noqa: E402


class PublicBackendTests(unittest.TestCase):
    def test_custom_base_url_rejects_embedded_secrets(self):
        unsafe_urls = (
            "https://user:password@example.test/v1",
            "https://example.test/v1?api_key=must-not-be-logged",
            "https://example.test/v1#secret",
            "http://example.test/v1",
        )
        for unsafe_url in unsafe_urls:
            with self.subTest(url=unsafe_url), self.assertRaises(api_config.BaseUrlError):
                api_config.validate_base_url(unsafe_url)

    def test_custom_base_url_allows_https_and_loopback_http(self):
        self.assertEqual(
            api_config.validate_base_url("https://EXAMPLE.test/v1/"),
            "https://example.test/v1",
        )
        self.assertEqual(
            api_config.validate_base_url("http://127.0.0.1:8080/v1/"),
            "http://127.0.0.1:8080/v1",
        )

    def test_config_does_not_persist_unsafe_base_url(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = argparse.Namespace(
                base_url="https://user:password@example.test/v1?key=secret",
                clear_base_url=False,
                model=None,
            )
            with mock.patch.dict(
                os.environ,
                {"EDITPPT_CONFIG_HOME": str(root)},
                clear=False,
            ), self.assertRaises(SystemExit):
                runtime_env.config(args)
            self.assertFalse((root / "config.yaml").exists())

    def test_dry_run_ignores_legacy_login_file(self):
        legacy_env_name = "CODEX_" + "AUTH_FILE"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy_auth = root / "legacy.json"
            legacy_auth.write_text(
                json.dumps({"tokens": {"access_token": "must-not-be-read"}}),
                encoding="utf-8",
            )
            argv = [
                "editppt image",
                "generate",
                "--prompt",
                "blue circle",
                "--dry-run",
            ]
            with mock.patch.dict(
                os.environ,
                {
                    "EDITPPT_CONFIG_HOME": str(root / "config"),
                    "OPENAI_API_KEY": "",
                    legacy_env_name: str(legacy_auth),
                },
                clear=False,
            ), mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(image_gen.main(), 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["backend"], "openai-compatible-api")
            self.assertNotIn("auth_file", payload)

    def test_non_dry_run_requires_environment_api_key(self):
        with tempfile.TemporaryDirectory() as temp:
            argv = ["editppt image", "generate", "--prompt", "blue circle"]
            with mock.patch.dict(
                os.environ,
                {"EDITPPT_CONFIG_HOME": temp, "OPENAI_API_KEY": ""},
                clear=False,
            ), mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    image_gen.main()
            self.assertEqual(raised.exception.code, 1)

    def test_doctor_ignores_and_reports_legacy_plaintext_secret(self):
        legacy_key_name = "OPENAI_" + "API_KEY"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.mkdir(exist_ok=True)
            (root / "config.yaml").write_text(
                yaml.safe_dump({legacy_key_name: "must-not-be-used"}),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"EDITPPT_CONFIG_HOME": str(root), legacy_key_name: ""},
                clear=False,
            ):
                status = runtime_env.collect_status(check_api=True)
            self.assertFalse(status["image_backend"]["ready"])
            self.assertIn(legacy_key_name, status["deprecated_plaintext_secret_fields"])
            self.assertFalse(any("codex" in key.lower() for key in status))

    def test_config_scrubs_legacy_plaintext_secret_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "OPENAI_" + "API_KEY": "remove-me",
                        "PADDLE_" + "OCR_TOKEN": "remove-me-too",
                        "IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL": "gpt-image-2",
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(base_url=None, clear_base_url=False, model=None)
            with mock.patch.dict(os.environ, {"EDITPPT_CONFIG_HOME": str(root)}, clear=False), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runtime_env.config(args), 0)
            cleaned = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(cleaned, {"IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL": "gpt-image-2"})

    def test_backend_contract_requires_api_key(self):
        args = argparse.Namespace(
            backend_id="editppt-image-cli",
            tool_name="editppt image",
            tool_call="editppt image generate/edit",
            fallback_command="editppt image",
            runtime_home="~/.editppt",
            model="gpt-image-2",
            input_context_policy="task-local inputs only",
        )
        contract = configure_image_backend.backend_contract(args)
        self.assertTrue(contract["requires_openai_api_key"])
        self.assertNotIn("oauth", contract["handoff_rule"].lower())

    def test_paddle_cli_rejects_secret_command_line_argument(self):
        with tempfile.TemporaryDirectory() as temp:
            page_dir = Path(temp)
            Image.new("RGB", (32, 24), "white").save(page_dir / "source.png")
            argv = ["paddle_text_hints", str(page_dir), "--" + "token", "must-not-be-used"]
            with mock.patch.dict(os.environ, {"PADDLE_OCR_TOKEN": ""}, clear=False), mock.patch.object(
                sys, "argv", argv
            ), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    paddle_text_hints.main()
            self.assertEqual(raised.exception.code, 2)

    def test_generate_and_edit_use_configured_sdk_client(self):
        png_payload = base64.b64encode(b"generated-image").decode("ascii")
        generate_call = mock.Mock(
            return_value=SimpleNamespace(data=[SimpleNamespace(b64_json=png_payload)])
        )
        edit_call = mock.Mock(
            return_value=SimpleNamespace(data=[SimpleNamespace(b64_json=png_payload)])
        )
        client = SimpleNamespace(images=SimpleNamespace(generate=generate_call, edit=edit_call))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generate_out = root / "generated.png"
            generate_args = argparse.Namespace(
                prompt="blue circle",
                prompt_file=None,
                model="gpt-image-2",
                size="auto",
                quality="auto",
                out=str(generate_out),
                force=False,
                dry_run=False,
                timeout=17,
            )
            with mock.patch.object(image_gen, "_create_client", return_value=client) as create_client:
                image_gen._generate(generate_args)
            create_client.assert_called_once_with(17)
            generate_call.assert_called_once_with(
                model="gpt-image-2",
                prompt="blue circle",
                size="auto",
                quality="auto",
            )
            self.assertEqual(generate_out.read_bytes(), b"generated-image")

            source = root / "source.png"
            Image.new("RGB", (32, 24), "white").save(source)
            edit_out = root / "edited.png"
            edit_args = argparse.Namespace(
                prompt="remove text",
                prompt_file=None,
                image=[str(source)],
                mask=None,
                model="gpt-image-2",
                size="auto",
                quality="auto",
                out=str(edit_out),
                force=False,
                dry_run=False,
                timeout=23,
            )
            with mock.patch.object(image_gen, "_create_client", return_value=client) as create_client:
                image_gen._edit(edit_args)
            create_client.assert_called_once_with(23)
            self.assertEqual(edit_call.call_count, 1)
            edit_request = edit_call.call_args.kwargs
            self.assertEqual(edit_request["prompt"], "remove text")
            self.assertTrue(edit_request["image"].closed)
            self.assertEqual(edit_out.read_bytes(), b"generated-image")


class PdfRegressionTests(unittest.TestCase):
    def test_multi_page_pdf_rendering_preserves_order_and_scale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = Image.new("RGB", (64, 48), "red")
            second = Image.new("RGB", (40, 30), "blue")
            pdf_path = root / "input.pdf"
            first.save(pdf_path, format="PDF", save_all=True, append_images=[second], resolution=72.0)
            outputs = render_pdf_pages(pdf_path, root / "pages", dpi=144)
            self.assertEqual(len(outputs), 2)
            with Image.open(outputs[0]) as rendered_first, Image.open(outputs[1]) as rendered_second:
                self.assertEqual(rendered_first.size, (128, 96))
                self.assertEqual(rendered_second.size, (80, 60))
                self.assertGreater(rendered_first.getpixel((10, 10))[0], rendered_first.getpixel((10, 10))[2])
                self.assertGreater(rendered_second.getpixel((10, 10))[2], rendered_second.getpixel((10, 10))[0])

    def test_non_pdf_ocr_submits_lossless_source_images_individually(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page_dirs = []
            for index, size in enumerate(((80, 60), (48, 96)), start=1):
                page_dir = root / f"page_{index:03d}"
                page_dir.mkdir()
                Image.new("RGB", size, "white").save(page_dir / "source.png")
                page_dirs.append(page_dir)
            with mock.patch(
                "paddle_text_hints.submit_and_fetch",
                side_effect=[[{"page": 1}], [{"page": 2}]],
            ) as submit, mock.patch(
                "paddle_text_hints.build_page_hints",
                side_effect=lambda page_dir, payload: {"page_dir": page_dir.name, "payload": payload},
            ):
                results = deck_text_hints.paddle_pages(
                    root,
                    {"input_type": "images"},
                    page_dirs,
                    token="test-token",
                    timeout=30,
                )
            self.assertEqual(submit.call_count, 2)
            self.assertEqual(
                [call.args[0] for call in submit.call_args_list],
                [page_dir / "source.png" for page_dir in page_dirs],
            )
            self.assertEqual([results[page]["payload"]["page"] for page in page_dirs], [1, 2])


class RepositoryBoundaryTests(unittest.TestCase):
    def test_private_backend_markers_are_absent(self):
        needles = [
            "chatgpt.com/" + "backend-api",
            ".codex/" + "auth.json",
            "CODEX_" + "AUTH_FILE",
            "CODEX_" + "IMAGES_BASE_URL",
            "ChatGPT-" + "Account-ID",
            "codex-" + "oauth",
            "codex " + "login",
            "--" + "token",
        ]
        extensions = {".py", ".ps1", ".md", ".toml", ".yaml", ".yml"}
        for path in REPOSITORY_ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions or ".git" in path.parts:
                continue
            content = path.read_text(encoding="utf-8-sig", errors="replace").lower()
            for needle in needles:
                self.assertNotIn(needle.lower(), content, f"private backend marker in {path}")


if __name__ == "__main__":
    unittest.main()
