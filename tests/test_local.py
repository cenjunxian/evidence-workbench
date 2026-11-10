"""补充本地入口、DOCX结构定位及现有跟进记录的无模型检查。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient
from online_server import create_online
from consumer_server import parse_upload
from run_local import DEFAULTS, create_local, load_settings

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "tests" / "_tmp"
TMP.mkdir(exist_ok=True)


def synthetic_docx(*, revision=False):
    """只在内存组装最小文档，不复用任何实际上传。"""
    types = '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'''
    rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    content = "<w:ins><w:r><w:t>修订</w:t></w:r></w:ins>" if revision else "<w:r><w:t>拟扣清洁费260元，凭证另行提供。</w:t></w:r>"
    doc = f'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>合成材料</w:t></w:r></w:p><w:p>{content}</w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>待核记录</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in (("[Content_Types].xml", types), ("_rels/.rels", rels), ("word/document.xml", doc)):
            archive.writestr(name, text)
    return stream.getvalue()


class LocalTests(unittest.TestCase):
    def test_docx_structure(self):
        result = parse_upload(synthetic_docx(), "合成.docx")
        self.assertEqual([s["location"] for s in result["segments"]],
                         ["正文第1段", "正文第2段", "正文表1／第1行第1格／第1段"])
        self.assertTrue(all(s["page"] is None for s in result["segments"]))
        self.assertIn("260元", result["segments"][1]["text"])

    def test_docx_unaccepted_revision_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            parse_upload(synthetic_docx(revision=True), "修订.docx")
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("修订", raised.exception.detail)

    def test_invalid_docx_is_rejected(self):
        with self.assertRaises(HTTPException):
            parse_upload(b"not-a-docx", "错误.docx")

    def test_launcher_requires_own_code_and_loopback(self):
        with tempfile.TemporaryDirectory(dir=TMP) as tmp, patch.dict("os.environ", {}, clear=True):
            p = Path(tmp) / "config.json"
            p.write_text((ROOT / "config.example.json").read_text(), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(p)
            values = {**DEFAULTS, "AIC_ONLINE_ACCESS_CODE": "synthetic-local-test-only",
                      "AIC_ONLINE_DATA": str(Path(tmp) / "data")}
            p.write_text(json.dumps(values), encoding="utf-8")
            settings = load_settings(p)
            self.assertEqual(settings["AIC_ONLINE_MODEL_ENABLED"], "0")
            app = create_local(settings)
            self.assertFalse(app.state.boundary.calls_enabled())
            values["AIC_ONLINE_ORIGIN"] = "http://0.0.0.0:8000"
            p.write_text(json.dumps(values), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(p)

    def test_model_enable_requires_key_and_future_deadline(self):
        with tempfile.TemporaryDirectory(dir=TMP) as tmp, patch.dict("os.environ", {}, clear=True):
            p = Path(tmp) / "config.json"
            values = {**DEFAULTS, "AIC_ONLINE_ACCESS_CODE": "synthetic-local-test-only",
                      "AIC_ONLINE_MODEL_ENABLED": "1"}
            p.write_text(json.dumps(values), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(p)
            values.update(AIC_ONLINE_DEEPSEEK_KEY="synthetic-never-used", AIC_ONLINE_CALLS_CLOSE_AT="2000-01-01T20:00:00+08:00")
            p.write_text(json.dumps(values), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(p)

    def test_real_routes_with_model_disabled(self):
        with tempfile.TemporaryDirectory(dir=TMP) as tmp:
            origin = "http://127.0.0.1:8000"
            code = "synthetic-local-test-only"
            app = create_online(Path(tmp) / "data", origin, code, test=True, allow_model=False)
            headers = {"X-Demo-Request": "1", "Origin": origin}
            with TestClient(app, base_url=origin) as client:
                self.assertEqual(client.get("/api/research/consultations").status_code, 401)
                self.assertEqual(client.post("/online-login", json={"code": code}, headers=headers).status_code, 200)
                for path in ("/", "/research-app.js", "/research-style.css", "/health/live"):
                    self.assertEqual(client.get(path).status_code, 200)
                for path in ("/example", "/materials/", "/online_server.py", "/config.local.json"):
                    self.assertEqual(client.get(path).status_code, 404)
                page = client.get("/").text
                self.assertIn("question-first-local-2-height", page)
                self.assertNotIn('href="/materials/"', page)
                created = client.post("/api/research/consultations", json={}, headers=headers)
                self.assertEqual(created.status_code, 200)
                cid = created.json()["id"]
                params = {"cid": cid, "name": "合成.docx"}
                uploaded = client.post("/api/research/upload", params=params, content=synthetic_docx(), headers=headers)
                self.assertEqual(uploaded.status_code, 200)
                self.assertEqual(uploaded.json()["file"]["segments"][1]["location"], "正文第2段")
                response = client.post("/api/research/goal", params={"cid": cid},
                    json={"text": "核对清洁项目与凭证", "status": "confirmed",
                          "expected_revision": uploaded.json()["state"]["revision"]}, headers=headers)
                self.assertEqual(response.status_code, 200)
                # 只记录承诺，不制造退款到账。
                response = client.post("/api/research/response", params={"cid": cid},
                    json={"text": "对方回复：我会补发清洁项目清单和凭证。", "status": "received", "waiting_for": "",
                          "expected_revision": response.json()["revision"]}, headers=headers)
                self.assertEqual(response.status_code, 200)
                state = client.get("/api/research/state", params={"cid": cid}).json()
                self.assertTrue(state["responses"])
                self.assertEqual(state["outcomes"], [])
                self.assertFalse(state["calls_enabled"])
                self.assertEqual(app.state.boundary.state["jobs"], {})


if __name__ == "__main__":
    unittest.main()
