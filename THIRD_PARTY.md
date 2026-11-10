# 第三方及来源说明

本仓库只分发应用源码、依赖清单和必要的最小合成夹具，不附 Python wheel、虚拟环境、node_modules、字体或模型权重。

## 应用代码

部分后端继承 HAMMURA-AI 竞赛代码。版权声明为 `Copyright (c) 2026 HAMMURA-AI contest maintainers`，已获 MIT 范围及原文见 `LICENSE.md`、`licenses/legacy-MIT.txt`。新增工作台与历史后端补充模块已在本次独立发布中另行取得 MIT 授权，没有扩大旧清单的历史范围。

## Python 依赖

直接运行依赖来自原工作台 `requirements.txt`，解析后的精确传递版本见 `requirements.lock.txt`。依赖由使用者从 PyPI 安装，本仓库不再分发它们的源码或二进制。

|依赖|用途|主许可证|
|---|---|---|
|FastAPI / Pydantic / pydantic-core|接口与数据校验|MIT|
|Starlette / Uvicorn|ASGI 运行|BSD-3-Clause|
|httpx / httpcore|HTTP 与测试内存传输|BSD-3-Clause|
|pypdf|文字 PDF 解析|BSD-3-Clause|
|ReportLab|PDF 导出|BSD-3-Clause|
|NumPy|历史检索模块的数组计算|BSD-3-Clause，另含随包组件条款|
|python-multipart|历史接口上传依赖|Apache-2.0|

其余传递依赖及安装元数据记录见 `docs/dependency-licenses.json`。这里的主许可证不是对完整发行包中所有组件的概括性重新授权。NumPy wheel 可能含 OpenBLAS、LAPACK、GCC runtime 等各自条款；ReportLab wheel 带有字体及相应许可。本应用使用 `STSong-Light` CID 映射，没有复制或分发这些字体文件。未来若再分发依赖二进制，需随目标平台发行包保留全部版权、许可和通知。

## 前端

当前 `consumer_web/` 不加载第三方 JavaScript、远程字体或 npm 包。原生 SVG 图标随源文件保留；形状简单并不足以证明原创来源，其公开授权与当前工作台前端一起纳入本次明确确认的 MIT 范围；没有另行复制第三方图标库。不将早期 Vue/Vite/PDF.js 构建链写成当前前端依赖；这些旧构建产物未导出。

## 运行夹具与法律资料

`samples/pdf/原创虚构家纺演示材料.pdf` 与 `samples/textile-manifest.json` 是既有后端构造时校验的合成夹具，不是真实合同或比赛报告，也不预填新的咨询。该文件与旧清单中的 `synthetic_textile_normal.pdf` 字节不同，不能继承该文件的分发范围；本次另行确认允许随本仓库原样再分发，不由旧许可推定。

`samples/legal/` 保存国家市场监督管理总局公开规章的引用、来源网址及历史核对说明。法规原文不作为项目原创内容授权；2020 文本及修订差异不等于 2026 个案适用已核。结构、摘要等原创整理部分允许随仓库原样再分发，不引入额外法律数据库资料。

DeepSeek 是外部托管服务，不是随仓库分发的开源模型。使用者须使用自己的账号，遵守服务条款并自行承担调用成本。
