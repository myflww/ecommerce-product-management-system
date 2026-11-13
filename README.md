# 电商产品管理系统

**E-commerce Product Management System · Midoo**

[English](README.en.md) · [中文使用手册](docs/USER_GUIDE.zh-CN.md) · [English user guide](docs/USER_GUIDE.en.md) · [许可证](LICENSE)

Windows 桌面商品工作台：将商品图片、平台档案、SKC / SKU、人工上下架状态和价格记录保存在本机，供同一局域网内的授权成员协作。桌面版使用独立窗口，安装后可从桌面和开始菜单打开。

**版本：1.6.0 · Windows 10 / 11 64 位。** 软件界面目前以中文为主；本仓库提供完整中英文说明，英文文档不代表界面已全部英文化。

> 本项目公开源码，采用 **PolyForm Noncommercial License 1.0.0**，限制第三方商业使用。它属于 source-available（源码可见）项目，不属于 OSI 定义下允许商业用途的开源软件。商业用途须另行取得权利人授权，具体以 [LICENSE](LICENSE) 为准。第三方依赖和模型分别保留各自许可证。

## 下载与开始使用

从 [Releases](https://github.com/myflww/ecommerce-product-management-system/releases) 下载 `EcommerceProductManager-Setup-1.6.0.exe`。

1. 双击 Setup 安装程序，按窗口提示安装到当前用户的 `%LOCALAPPDATA%\Programs\EcommerceProductManager`。无需管理员权限或 PowerShell 执行策略调整，会创建名为「电商产品管理系统」的桌面与开始菜单快捷方式。
2. 打开快捷方式。桌面窗口使用 Microsoft Edge WebView2；缺少运行环境时，会提示并退回兼容的服务管理器，由浏览器打开工作台。
3. 首次使用创建自己的管理员名称和密码，没有预设账号。
4. 在「批量入库」导入图片或商品文件夹，再通过「编辑」补充分类、平台和 SKU。
5. 在「以图查重」放入准备上新的图片，核对库内疑似同款。

也可下载 `EcommerceProductManager-1.6.0-Windows.zip`，完整解压后直接运行 `Midoo.exe`，或在允许脚本的环境中使用 `Install-Desktop.cmd`。打包版无需 Python。WebView2 桌面窗口与浏览器辅助采价是两项不同能力：后者另需主机安装官方 Microsoft Edge 或 Google Chrome。

已有库升级时，先备份并正常关闭自有服务，保留原 `data` 文件夹。需要让新安装指向其他位置的现有库，可运行：

```powershell
.\EcommerceProductManager-Setup-1.6.0.exe --install --data-dir "D:\Midoo\data"
```

`desktop-config.json` 是安装时生成的本机配置，不属于公开发布内容。程序不会自动扫描私人目录寻找旧库。不要用新建空库覆盖旧库；也可用 `Midoo.exe --data-dir "D:\Midoo\data"` 显式启动原库，环境变量 `MIDOO_DATA_DIR` 优先级最高。详细步骤见[安装、升级和卸载](docs/USER_GUIDE.zh-CN.md#安装升级和卸载)。

卸载时，正常关闭已安装程序，再从开始菜单选择 Uninstall，或双击安装目录中的 `Uninstall.exe`。原生卸载窗口移除应用与快捷方式，保留商品资料；`Uninstall-Desktop.cmd` 仅作为脚本环境下的备用方式。

## 能做什么

| 工作内容 | 支持的操作 |
| --- | --- |
| 商品图片库 | 多图商品、文件夹批量导入、分组合并 / 拆分、编号、负责人、规格、备注和自定义分类 |
| 本地查重 | DINOv2 视觉语义召回、原图一致检查、命中图片与依据、人工同款 / 不同款 / 待核实记录 |
| 平台与 SKU | 一个商品维护多个平台、店铺、链接 / SKC 分组；每个 SKU 独立记录规格、原价、币种和人工上下架状态 |
| 运营队列 | 待完善、待定价、准备上架、有平台在售、全部已下架；待处理队列可重叠，商品总数去重 |
| 价格比较 | 紧凑价格表、单 SKU 或本商品手动采价、手填前端价、时间与失败提示、价格历史 |
| 浏览器辅助 | 专用持久采价环境、页面报价预览、用户选择并核对后确认；登录资料与日常浏览器隔离 |
| 跨币种参考 | 按 ECB 参考汇率显示约合原币种金额、比例、汇率日期，保留两端原始金额 |
| 可选 AI 与知识 | 商品命名、图片复核、图片 / 文档聊天、持久公司知识、商品图片卡片、官方思考选项及显式联网搜索 |
| 团队与恢复 | 管理员 / 运营账号、活动筛选与分页、可恢复回收站、完整数据备份 |

### 三个需要先了解的边界

- **人工确认优先。** 上下架由运营手动决定。填写链接、录入价格或采集成功都不会自动上架 / 下架。删除 SKU 可在商品编辑草稿保存前撤销；商品移入回收站后可恢复。
- **查重是内部候选检索。** 图片相近不等于同一 SKU，也不保证找全重复或预测平台核价。尺寸、数量、材质和包装仍需核对；软件不会搜索全网商品库。
- **当前没有后台每日采价。** 价格读取由用户主动点击触发，旧版保存的每日计划也不会执行。动态网页、登录、验证码、地区和账号差异可能阻止取价；AI API 不能凭一个链接保证读到页面。

## 商品、价格与币种

商品自定义分类与运营队列相互独立。尚无平台档案的商品进入三个待处理入口；已有 SKU 中，准备上架的记录进入准备队列，未下架且缺原价的记录进入待定价和待完善。任一 SKU 被运营确认上架，商品就可进入在售队列；只有全部已登记记录都明确下架，才是全部已下架。

价格弹窗按平台 / SKU 显示原价格、前端价格和比例。原价 20 美元、前端价 15 美元显示 **75%**，表示前端价为原价的 75%，不是降价 75%。价格高于原价时比例可超过 100%。

两端币种不同时，仅在有可用 ECB 参考汇率时换算比较，并用「≈」标记。它不是银行成交、平台结算或扣除手续费后的金额。缓存通常复用 24 小时；网络失败时可使用不超过 7 天且标明日期的缓存，无法换算时显示原因，不按 1:1 猜测。汇率查询不向 ECB 发送商品、金额、SKU 或账号资料。

浏览器辅助仅允许公司主机本机操作。用户在专用浏览器正常登录、选择实际 SKU 和地区，返回软件读取报价，再核对原价 / 现价、供货 / 零售等口径并确认。局域网同事不能远程接管这个窗口。专用登录资料不进入公开发布包或现有备份导出。完整说明见 [PRICE_TRACKING.md](PRICE_TRACKING.md)。

## AI 与资料发送

AI 默认未配置。图片入库与本地查重不需要云端密钥，也不会自动产生 AI 请求。

管理员可配置 DeepSeek、通义千问、OpenAI、Kimi、豆包、智谱、Gemini、自定义兼容服务或 Claude。应填写账号实际可用的模型 ID 与服务地址；未知模型只使用默认思考设置，不发送猜测参数。

Windows 密钥在服务端使用当前系统账号的 DPAPI 加密，不回显到页面。管理员分别控制商品摘要、知识片段与视觉资料发送。发送聊天、连接测试、AI 命名、AI 复核或获准的页面文字提取会请求所选服务商，并计入共享每日次数上限；次数上限不是金额限额。

聊天附件支持图片及 TXT、MD、CSV、JSON、PDF、DOCX、XLSX。文件在本机提取受限文字后发送，图片需要视觉授权；不会自动入库、运行宏或执行表格公式。提取长度、页数、表格范围及多轮引用均有限制，见[使用手册](docs/USER_GUIDE.zh-CN.md#ai-服务与附件)。本仓库不包含可用 API 密钥。

「AI 管家 → 公司知识」可手写或导入团队文档，将提取文字持久保存于本机；原始文件不保存。没有 AI 密钥时仍可管理和查阅知识。对话支持智能助手、公司知识、通用对话三种模式，只在获准的模式中发送相关内部片段，并提供商品 / 知识出处。商品原图卡片由界面本地显示，不等于自动把原图交给模型识别。

可选联网需要管理员开启并配置可用的官方 DeepSeek 搜索服务。用户每次明确勾选联网，并单独填写公开搜索关键词；内部资料和历史不会被自动拼成搜索词。联网结果会交给当前聊天模型作答并列出来源，一次联网问答占用两次请求额度，可能另计搜索费用。不要在公开关键词中填写机密，搜索失败或无结果不代表取得了实时事实。

## 局域网协作与数据

默认本机地址为 `http://127.0.0.1:8765`。团队成员使用设置中显示的主机局域网地址和各自账号登录。主机与共享服务需要保持运行；退出自己启动的服务后，团队访问与自动备份随之停止。桌面窗口连接已经运行的服务时，不接管其所有权。

默认数据目录是 `Midoo.exe` 旁的 `data`，也可由本机配置或 `MIDOO_DATA_DIR` 指定；以「设置与备份 → 局域网与备份」显示的位置为准。软件不自动开放防火墙或公网端口，不应直接暴露到互联网。

备份包含商品、图片和账号数据库，可能包含加密 AI 配置，应按私密资料保管。服务运行期间每天尝试创建一次本地自动备份，保留最近 7 份自动备份；同一硬盘备份不能替代异地副本。跨电脑或 Windows 账号迁移后通常需要重新填写 AI 密钥。不要把 `data`、备份、登录 profile、密码、API 配置或真实商品图提交到 GitHub。

## 从源码运行

开发环境以 Windows 64 位、Python 3.13 为基线。仓库中的版本固定在依赖文件内。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools\download_model.py
.\.venv\Scripts\python.exe desktop.py
```

本地视觉模型需保留在 `models/dinov2-small`。源码仓库不提交大体积权重；上述显式准备命令下载固定公开模型并验证 SHA256，源码用户也可从 Releases 获取 `dinov2-small-onnx-model.zip`。Setup 和完整便携桌面包已内置权重，无需另下模型包。来源见该目录的 [README](models/dinov2-small/README.md)，不要任意替换模型；程序正常运行时不会自动下载权重。

仅启动共享服务或兼容管理器：

```powershell
.\.venv\Scripts\python.exe desktop.py --serve --host 0.0.0.0 --port 8765
.\.venv\Scripts\python.exe desktop.py --service-manager
```

运行检查与打包：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
Get-ChildItem tests\*_ui_smoke.cjs | ForEach-Object { node $_.FullName }
.\.venv\Scripts\python.exe -m pip install pyinstaller
powershell -NoProfile -File .\tools\build_windows.ps1
```

构建脚本核对模型 SHA256，并通过 `Midoo.spec` 生成 `dist/windows/Midoo.exe`。Node.js 用于前端检查，不是打包版的运行前提。发布前应对最终 EXE 做隔离库验证；常规测试使用临时库、合成页面和模拟 AI，不证明所有真实平台或账号都可正常采价，也不代表已做付费 API 联调。

## 仓库与问题反馈

- `app.py` / `backend.py`：本地服务、鉴权和商品资料。
- `desktop.py` / `desktop_shell.py`：桌面窗口与服务管理。
- `static/`：本地界面资源。
- `price_*` / `browser_price_*` / `exchange_rates.py`：价格、浏览器辅助和参考汇率。
- `ai_service.py` / `ai_capabilities.py` / `chat_attachments.py`：可选模型与附件处理。
- `knowledge_store.py` / `chat_memory.py` / `web_grounding.py`：本地公司知识、会话附件引用与显式公开搜索。
- `models/`：本地检索模型说明与第三方许可。
- `tools/`：当前用户安装与卸载脚本。
- `docs/` / `tests/`：中英文手册与验证用例。

提交问题时附软件版本、复现步骤和去除敏感信息的错误内容。不要公开上传数据库、备份、API 密钥、Cookie、完整店铺登录链接或客户 / 团队资料。安全问题请优先通过 GitHub 的私密安全报告渠道（若仓库启用）联系维护者；没有该渠道时，先提交不含细节的联系请求。

## 许可与第三方内容

应用源码采用 [PolyForm Noncommercial License 1.0.0](LICENSE)。允许的学习、研究、修改与再分发须遵守许可证；商业使用请先联系权利人取得单独授权。发布者对其拥有版权的代码保留自己的使用权，这不构成对第三方的商业授权。

DINOv2 原模型和对应权重按其 Apache-2.0 许可提供；其他依赖仍按各自许可证使用。应用的非商业限制不改写这些第三方组件本身的许可。来源与固定模型版本见 [模型说明](models/dinov2-small/README.md)，其他声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
