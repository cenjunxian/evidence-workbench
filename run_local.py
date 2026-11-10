"""仅在本机回环地址运行工作台，不复用部署配置或模型账户。"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    "AIC_ONLINE_DATA": "./data",
    "AIC_ONLINE_ORIGIN": "http://127.0.0.1:8000",
    "AIC_ONLINE_ACCESS_CODE": "",
    "AIC_ONLINE_MODEL_ENABLED": "0",
    "AIC_ONLINE_CALLS_CLOSE_AT": "",
    "AIC_ONLINE_DEEPSEEK_KEY": "",
}


def load_settings(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or set(config) - DEFAULTS.keys():
        raise ValueError("配置含未知字段，请参照 config.example.json")
    if any(not isinstance(v, str) for v in config.values()):
        raise ValueError("配置值必须是字符串")
    values = {k: os.environ.get(k, config.get(k, v)) for k, v in DEFAULTS.items()}
    origin = urlsplit(values["AIC_ONLINE_ORIGIN"])
    if (origin.scheme != "http" or origin.hostname != "127.0.0.1"
            or origin.path or origin.query or origin.fragment
            or origin.username or origin.password):
        raise ValueError("本地启动器仅允许 http://127.0.0.1:端口")
    if not 1 <= (origin.port or 80) <= 65535:
        raise ValueError("端口无效")
    code = values["AIC_ONLINE_ACCESS_CODE"]
    if not 12 <= len(code) <= 128 or code == "REPLACE_WITH_YOUR_OWN_ACCESS_CODE":
        raise ValueError("请设置自己新建的12至128字符本地访问码")
    if values["AIC_ONLINE_MODEL_ENABLED"] not in ("0", "1"):
        raise ValueError("模型开关只接受0或1")
    if values["AIC_ONLINE_MODEL_ENABLED"] == "1":
        if not values["AIC_ONLINE_DEEPSEEK_KEY"]:
            raise ValueError("启用模型前须配置自己的DeepSeek密钥")
        deadline = datetime.fromisoformat(values["AIC_ONLINE_CALLS_CLOSE_AT"])
        if deadline.tzinfo is None or deadline.timestamp() <= datetime.now().timestamp():
            raise ValueError("启用模型前须设置带时区的未来关闭时间")
    data = Path(values["AIC_ONLINE_DATA"])
    values["AIC_ONLINE_DATA"] = str(data if data.is_absolute() else ROOT / data)
    return values


def create_local(values):
    from online_server import create_online
    enabled = values["AIC_ONLINE_MODEL_ENABLED"] == "1"
    closing = values["AIC_ONLINE_CALLS_CLOSE_AT"]
    close_at = datetime.fromisoformat(closing).timestamp() if enabled else None
    return create_online(
        values["AIC_ONLINE_DATA"], values["AIC_ONLINE_ORIGIN"],
        values["AIC_ONLINE_ACCESS_CODE"], test=True,
        key=values["AIC_ONLINE_DEEPSEEK_KEY"] if enabled else "",
        allow_model=enabled, close_at=close_at,
    )


def main():
    parser = argparse.ArgumentParser(description="本机运行循证文档研究工作台")
    parser.add_argument("--config", default=str(ROOT / "config.local.json"))
    args = parser.parse_args()
    values = load_settings(args.config)
    origin = urlsplit(values["AIC_ONLINE_ORIGIN"])
    import uvicorn
    uvicorn.run(create_local(values), host="127.0.0.1", port=origin.port or 80,
                workers=1, access_log=False)


if __name__ == "__main__":
    main()
