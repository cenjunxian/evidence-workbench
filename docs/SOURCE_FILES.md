# 源码范围与导出差异

基线为 `question-first-local-2-height` 对应的 `civil-release` 工作台源码。当前前端不是早期 `contest/frontend` 原型。未复制原 Git 历史或公司业务模块。

## 导入范围

- 根目录：`online_server.py`、`consumer_server.py`、`consumer_parse.py`、`requirements.txt`。
- 当前前端：`consumer_web/index.html`、`consumer_web/app.js`、`consumer_web/style.css`。
- 后端依赖：`backend/app.py`、`evidence_support.py`、`legal_return.py`、`model.py`、`model_dispatch.py`、`model_reasoning.py`、`model_reasoning_v2.py`、`pdf_pages.py`、`real_validation_window.py`、`retrieval.py`、`textile_guidance.py`、`workspace.py`。
- 启动依赖夹具：`samples/textile-manifest.json`、`samples/pdf/原创虚构家纺演示材料.pdf`、`samples/legal/return-law-2020.json`、`samples/legal/return-law-2026-research.json`。保留原夹具，未新做演示素材。
- 适配既有合成测试 `tests/test_civil.py`，补充本地入口及 DOCX 测试 `tests/test_local.py`。

## 为独立运行进行的调整

1. 首页只移除通向旧 `/example` 与比赛 `/materials/` 的两个链接，其余布局、CSS与业务脚本不变。
2. `/example` 明确返回404，替代访问未导出的早期前端构建产物。
3. 添加 `run_local.py` 与配置示例：只允许 `127.0.0.1` 的本机开发入口，默认关闭模型；启用时必须自备密钥及未来截止时间。原部署入口仍保留，不提供生产配置。
4. 从原锁文件提取运行依赖，移除不参与当前检查的 pytest、ruff 及其独有依赖，保留运行依赖精确版本。
5. 测试路径改为独立仓库，临时文件仅放测试目录且不入 Git；测试内容、模拟失败及断言保留。

没有包含早期前端的 `dist`、旧 npm 锁文件、OCR worker/权重、安装环境、真实文档、数据库、日志、配置、比赛报告、视频、PPT、截图、网盘说明或内部交接。`pdf_pages.py` 作为被引用模块保留，其历史可选OCR分支不是当前工作台的功能，也未安装可选OCR依赖。

## 许可边界

九份导出时字节一致且在旧清单中的后端文件（Git 仅统一文本换行为 LF）保留既有 MIT 版权声明；其余原创代码、前端、测试、配置与文档在本次独立发布中另行确认 MIT 授权，见根目录 `LICENSE` 与 `LICENSE.md`。合成夹具及原创整理字段另获随仓库原样再分发许可，不把旧许可扩大到比赛交付或第三方法规。
