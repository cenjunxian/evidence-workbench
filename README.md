# 循证文档研究工作台

把材料里的原句、用户陈述和待核问题放在同一份工作记录中，再组织沟通稿并记录后续回应。适合先整理一般消费、租赁押金等民事问题的材料，不代替法律专业判断。

## 已实现的功能

- 读取文字 PDF、UTF-8 TXT、DOCX 正文及表格，显示提取范围和未读取部分。
- 依据对话及材料整理待核记录；用户可确认、有分歧或保留未知，修正后保留旧记录和结果版本。
- 对实际带引文的结果回查来源，显示 PDF 页、TXT 行或 DOCX 结构位置，并匹配原句。
- 编辑、保存、恢复上一份沟通稿，导出 Markdown 或 PDF。
- 分开记录目标、行动、对方回应和实际处理结果；各咨询及访客会话隔离。

代码基线是 `question-first-local-2-height`。当前前端是 `consumer_web/` 中的原生 HTML、CSS、JavaScript，后端是 Python / FastAPI，使用 Pydantic、httpx、pypdf、ReportLab 和 NumPy。前端不需要 npm 安装或构建。模型使用 DeepSeek 托管 API，仓库不包含本地模型或权重。

## 本机运行

建议 Python 3.12。以下在仓库根目录执行；虚拟环境和自己的配置不提交 Git。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Copy-Item config.example.json config.local.json
```

编辑 `config.local.json`，把 `AIC_ONLINE_ACCESS_CODE` 改成自己新建的 12—128 字符访问码，再运行：

```powershell
.\.venv\Scripts\python.exe run_local.py
```

用自己的浏览器打开 `http://127.0.0.1:8000`，输入上述本地访问码。启动器只绑定回环地址、单进程运行，HTTP Cookie 例外仅用于本地开发；不能直接用于公网部署。无需连接原项目服务器、比赛网站或评审账号。

其他系统可使用 `.venv/bin/python` 执行同样的安装和启动命令；本轮不把这些系统标为已验证。

### 模型配置

默认 `AIC_ONLINE_MODEL_ENABLED` 为 `0`：可以进入工作台、创建咨询、读取材料和记录进展；模型整理操作会被拒绝，不会展示预填的生成结果。

需要真实整理时，由运行者自行配置：

- `AIC_ONLINE_MODEL_ENABLED`：改为 `1`。
- `AIC_ONLINE_DEEPSEEK_KEY`：自己的 DeepSeek API 密钥。
- `AIC_ONLINE_CALLS_CLOSE_AT`：自己确定的未来关闭时间，ISO 8601 格式，必须包含时区。

以上字段也可由同名环境变量覆盖配置。`AIC_ONLINE_DATA` 为新建的本地数据目录；相对路径以仓库根目录解析。`AIC_ONLINE_ORIGIN` 须与本机访问地址一致。现有后端固定请求 `https://api.deepseek.com` 的 `deepseek-v4-flash`；请自行确认账号支持该模型及服务条款。仓库不带密钥、评审访问码或免费额度，也不回退到其他人的服务。材料和问题在启用模型后会发送给该服务。

## 使用限制

单文件不超过 1 MiB，最多 3 份、合计 2 MiB；单份最多 8000 字符、总计 12000 字符；文字 PDF 最多 10 页。工作台没有 OCR，不支持扫描件和旧 `.doc`；DOCX 定位是正文/表格结构，不是原排版页码，未处理修订会拒绝读取。

访客记录按登录起 24 小时清理；最多 12 条咨询、每咨询 6 轮输入、每访客 25 个模型任务，任务间隔 10 秒并限制并发。及时导出需要保留的内容，不能把本地文件存储理解为永久备份或跨设备同步。

引文匹配只说明原句存在，不证明事实真实或法律适用。普通回复与行动不保证逐条附有直接引文。进度短语可能因否定或转述误判；对方承诺与实际处理结果应分别核对。历史法律资料保留其年份与适用未核状态，不宣称适用于当前个案。

## 目录与检查

- `online_server.py`：会话隔离、鉴权、额度与运行边界。
- `consumer_server.py` / `consumer_parse.py`：咨询、文档解析、来源核验、稿件及跟进。
- `consumer_web/`：当前工作台前端。
- `backend/`：工作台依赖的模型、检索及历史兼容模块。
- `samples/`：现有后端启动仍依赖的最小合成夹具与历史法律资料，不含真实咨询。
- `tests/`：合成数据及模拟模型测试，不需要真实模型账号。

```powershell
.\.venv\Scripts\python.exe tests/test_civil.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_local*.py" -v
node --check consumer_web/app.js
```

独立导出没有包含早期 Vue 前端构建产物或比赛材料；首页中通向这两类文件的旧链接已移除，旧 `/example` 入口不提供。历史兼容模块不等于当前工作台支持 OCR。具体核对范围见 [本地检查记录](docs/VALIDATION.md)。

## 许可

本独立仓库的原创代码、测试、配置示例和文档使用 [MIT 许可证](LICENSE)。既有代码的版权声明继续保留；必要合成夹具允许随仓库原样再分发，法规原文和第三方依赖分别遵循其来源与条款。具体对象见 [许可范围](LICENSE.md) 和 [第三方说明](THIRD_PARTY.md)。这不授权原项目的其他业务代码、比赛报告、视频、截图、商标或私人数据。
