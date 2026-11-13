# E-commerce Product Management System User Guide

Applies to version 1.6.0 (Midoo for Windows)

[简体中文](USER_GUIDE.zh-CN.md) · [Project home](../README.en.md) · [License](../LICENSE)

This guide is for users whose use is permitted by the license. Source is public, but commercial use is restricted. If using someone else's published copy for a business or shop, obtain the necessary permission first. The publisher retains its own rights in code it owns.

## Contents

1. [Requirements and desktop window](#requirements-and-desktop-window)
2. [Installation, updates, and uninstalling](#installation-updates-and-uninstalling)
3. [First login and team accounts](#first-login-and-team-accounts)
4. [Image import and product records](#image-import-and-product-records)
5. [Categories and operational queues](#categories-and-operational-queues)
6. [Platforms and SKC / SKU variants](#platforms-and-skc--sku-variants)
7. [Local image duplicate search](#local-image-duplicate-search)
8. [Prices and browser-assisted collection](#prices-and-browser-assisted-collection)
9. [AI services and attachments](#ai-services-and-attachments)
10. [Activity, Trash, and permissions](#activity-trash-and-permissions)
11. [Backup, recovery, and migration](#backup-recovery-and-migration)
12. [Run and build from source](#run-and-build-from-source)
13. [Troubleshooting](#troubleshooting)
14. [Privacy, license, and reporting](#privacy-license-and-reporting)

The interface is currently primarily Chinese. This guide includes the original labels where they help identify a control; it does not claim a fully localized English interface.

## Requirements and desktop window

- Windows 10 / 11, 64-bit. The complete packaged release does not require Python or Node.js.
- The independent window requires Microsoft Edge WebView2 Runtime. If unavailable or unable to start, the application explains the issue and uses the compatible service manager to open the workspace in the system browser. Obtain WebView2 only from Microsoft.
- Browser-assisted collection separately requires an official Microsoft Edge or Google Chrome installation. The program includes its driver but does not automatically download a complete browser.
- Local visual retrieval runs on the CPU. The interface reports index preparation; performance depends on the host and image count.
- The basic product library and local image search can work offline. AI, real storefront pages, and public reference FX each need their own network access.

Open the **电商产品管理系统** desktop shortcut for the independent window. If the port already hosts the same application, the desktop connects without stopping or taking ownership of that service. Stopping a service you started affects local-network teammates and automatic backups; read the closing prompt.

Use `Midoo.exe --service-manager` for the compatible service manager. It shows sharing addresses and a browser access button. The default port is `8765`, and the default local URL is `http://127.0.0.1:8765`.

## Installation, updates, and uninstalling

### First installation

1. Download `EcommerceProductManager-Setup-1.6.0.exe` from [Releases](https://github.com/myflww/ecommerce-product-management-system/releases).
2. Run Setup and confirm installation in its standard window. No PowerShell execution-policy change is needed.
3. Installation is per-user under `%LOCALAPPDATA%\Programs\EcommerceProductManager`, without administrator rights.
4. Open **电商产品管理系统** from the desktop or Start menu.

The package contains no publisher's business library, account, or API key. An independent first run creates your own empty library and does not scan private folders. Alternatively, download `EcommerceProductManager-1.6.0-Windows.zip`, extract all files, and run `Midoo.exe` as a portable application. Do not run it from an archive preview. The ZIP's `Install-Desktop.cmd` and `tools/Install-Desktop.ps1` provide a fallback installation method where scripts are allowed.

### Use an existing library

Do not re-import a library or substitute an empty folder. Back up first, then explicitly select its existing path, for example:

```powershell
.\EcommerceProductManager-Setup-1.6.0.exe --install --data-dir "D:\Midoo\data"
```

The generated `desktop-config.json` is machine-local data-location configuration, not a file to publish. A startup argument such as `Midoo.exe --data-dir "D:\Midoo\data"` overrides that configuration, and `MIDOO_DATA_DIR` has highest priority. The authoritative location is shown under **设置与备份 → 局域网与备份** (Settings and backup → LAN and backup).

### Update

1. Export a complete backup from the old version and keep another copy on a different device.
2. Normally close the old application and the service whose executable will be replaced. Closing a browser tab does not necessarily stop a service. A desktop window connected to someone else's running service does not stop that process when closed.
3. Run the new Setup to update application files, retaining the existing `data` folder and local data-location configuration. Portable users should extract the new version while explicitly keeping their old library.
4. Start the updated app and verify its version, product count, images, SKUs, baseline prices, and current prices. Until index preparation finishes, do not treat a missing search result as evidence that no duplicate exists.

Do not overwrite an active database manually. Keep release files distinct from your saved data. If files are in use, normally close the relevant service before updating rather than forcing an overwrite.

### Uninstall

Normally close the installed application, then select Uninstall in the Start menu's Ecommerce Product Manager folder, or run `Uninstall.exe` from the installation directory and confirm in its native window. It removes the current user's application files and matching shortcuts while retaining product data. An externally selected library is not deleted as an installation file. Export a backup and record the actual data path before uninstalling.

The command-line form is `Uninstall.exe --uninstall`. `Uninstall-Desktop.cmd` / `tools/Uninstall-Desktop.ps1` remain fallback entry points where scripts are allowed. Without a matching installation marker, the uninstaller does not scan for or remove unrelated programs.

If you intend to destroy the library or dedicated browser sign-in data, first verify that necessary backups exist and handle those files separately. Removing the application and permanently deleting business records are different operations.

## First login and team accounts

1. Create an administrator name and password when opening an empty library. Passwords need at least eight characters; there is no preset account.
2. Add teammates under **设置与备份 → 团队账号** (Settings and backup → Team accounts). Each person should use a separate account.
3. Administrators manage the team, AI providers, and backups. Operators can maintain products they created or currently own.
4. Change your own password under **我的账号** (My account). Never include it in a public issue or screenshot.

Share the LAN address shown in settings or the compatible manager. Teammates need a trusted common network and access to the host's service port. The program does not change firewall rules, create internet port forwarding, or configure startup automatically.

Local browser-assistance rights are separate from ordinary LAN access. Being able to view product records or edit a product does not let a remote teammate control the host browser or clear its shop profile.

## Image import and product records

**批量入库** (Batch import) accepts image selection, product folders, drag and drop, and pasted screenshots. A folder can hold multiple views of one product. Loose images start as separate groups. Before committing, rename, merge, or split groups and add specifications, notes, a category, and platform information.

Current limits are 300 images and 250 MB per batch, 20 MB per image, and 30 images per product. Split larger imports into batches. Keep clear original main images, detail views, and specification text; a similar background is not a product fact.

Each product has an automatically generated company code, name, owner, specifications, notes, and images. Exact-file duplicate notices do not automatically merge products: the same image may occur in records for different bundles and needs human review.

**AI 命名** (AI naming) is optional. It offers a suggestion that changes the name only after adoption. Manual naming and normal import work without AI configuration.

## Categories and operational queues

Custom categories, such as pillar candles, wax sheets, crystals, or molds, are product metadata independent of operational queues. Combine category or uncategorized filters with keywords, platform, and platform state.

| Queue | Rule |
| --- | --- |
| 待完善 — Needs details | No platform records, or a non-delisted SKU without a baseline price |
| 待定价 — Needs pricing | No platform records, or a non-delisted SKU without a baseline price |
| 准备上架 — Preparing to list | No platform records, or a SKU manually set to preparing |
| 有平台在售 — Listed on a platform | At least one SKU manually confirmed listed, including one with incomplete pricing |
| 全部已下架 — All delisted | Platform records exist and every one is explicitly manually delisted |

Queues may overlap. Old and new products without platform records can appear in all three preparation queues. Product totals are unique; do not add queue counts to calculate the number of products. A product may have both listed and preparing SKUs.

Human status is authoritative. Missing prices, a collected quote, or a changed URL do not overturn a listing decision. Unconfirmed listing is not the same as explicit delisting.

## Platforms and SKC / SKU variants

Open product **编辑** (Edit), then **平台与上架资料** (Platform and listing records). Rows are grouped by product link / SKC. A group shares platform, store, SKC, storefront URL, and region. Each SKU retains its own identifier, exact specification, baseline price, currency, and manual state.

- One product can be sold on multiple platforms or through multiple stores on one platform.
- One SKC / URL can contain several SKUs. Identifier leading zeros are preserved as text.
- A new SKU starts as preparing and does not copy another SKU's baseline or current price.
- The green toggle means manually confirmed listed; turning it off means manual delisting. Confirm the actual operational state before changing it.
- Removing a SKU or a group first changes the edit draft. Undo before saving if needed. Saving removes only those platform records, not the whole company product or original images.

Changing a platform, store, SKC / SKU, URL, specification, region, or currency may invalidate the current quote's target; past history remains. Editing only baseline price or manual listing state is not a new collection of the storefront price.

## Local image duplicate search

1. Open **以图查重** (Image duplicate search) and add main or detail images of the proposed product, up to five query images.
2. Check local index readiness. Until ready, only identical-file and previously human-confirmed matches are available.
3. Run normal search and inspect the small set of recommended candidates. Expand weaker candidates when needed.
4. Compare matched images, specifications, quantity, packaging, and owner before choosing same, uncertain, or different.

By default, at most three recommendations are shown, with additional candidates available explicitly. The interface does not present an uncalibrated retrieval score as an identical-SKU probability. Related colors or bundle sizes can be retrieved together; backgrounds, occlusion, and different views can also cause misses.

Human feedback applies to later review of the same query image; it does not automatically train the entire model. Rebuilding derived embeddings must not delete original images or business records. The application displays a limited state when the model is unavailable.

With administrator permission, **AI 查重** (AI review) compares images for up to eight locally retrieved candidates. It does not search every external marketplace or overwrite human decisions. Model suggestions, visual resemblance, and actual SKU identity are different kinds of evidence.

## Prices and browser-assisted collection

### Baseline, current price, and ratio

The **价格变化** (Price changes) control on each card opens a compact table. Baselines are entered manually. Current storefront prices come from an explicit collection or manual entry and are stored separately.

For the same currency, ratio is `current price / baseline price × 100%`. A baseline of 20 and current price of 15 gives 75%, not a 75% reduction. Unsaved drafts show a pending state and hide the old ratio. A missing baseline or unavailable conversion yields —. A price increase may produce a ratio above 100%.

Different currencies use an available ECB reference rate and show **≈**, the approximate converted amount, rate, and date. Original currency values and price history are unchanged. Cache is normally reused for 24 hours. On failure, a dated cache no older than seven days may be used, with five-minute failure backoff. If no usable rate exists, the application does not assume 1:1 or roll back a saved manual price. Reference rates are not actual bank execution, marketplace settlement, or after-fee rates.

### Explicit collection

1. In the product editor, enter the exact SKU, baseline and currency, storefront URL, and target region.
2. Open the price popover and click row **采价** (Collect) or product **采集价格** (Collect prices).
3. A public page may directly provide a verifiable exact-SKU offer. Multiple variants, ranges, crossed-out prices, shipping, or unverified quotes are not a confirmed price for that target.
4. Failures retain the last valid price with a reason and timestamps. Do not mistake a retained old record for a newly collected quote.

**Daily background price collection and automatic listing-state queries remain disabled in this version, including old saved schedules.** Starting the app, saving a URL, or changing manual state does not automatically visit a shop. On-demand public FX refresh is not background shop collection.

### Browser assistance

From the host's local address, enter the SKU's **浏览器** (Browser) collection section. The application opens an isolated dedicated Edge / Chrome environment. The user signs in normally or completes any required site verification, chooses the actual region and variant, then returns to read a quote preview.

When several prices are present, the lowest is not chosen automatically. Select the actual offer and confirm the product, variant, region, and price basis, including wholesale versus retail, list price, and coupons. A preview does not save a price. Tasks last about 15 minutes; previews last about two minutes, after which a fresh read is required.

Persistent profiles are separated by application user, platform, store, host, and browser. Closing a collection window can preserve this dedicated environment's sign-in, without copying everyday browser cookies. Expiry, risk checks, or regional changes may still require verification. Close related collection tasks before clearing this store's sign-in profile. LAN users cannot remotely control those windows or clear host profiles.

Collection is not guaranteed for script-only, restricted, or verification pages. Real Temu or other shop accounts, every SKU, and every region have not been end-to-end validated. The application does not promise perpetual login or successful extraction from arbitrary pages.

### Manual current-price entry

Without a usable link or quote, type an amount and its actual currency into the SKU's current-price cell, then click ✓. Only that row's current price changes; baselines and other SKUs remain. Old drafts, network responses, and browser previews cannot overwrite a changed SKU context. On conflict, review the SKU and enter the value again.

## AI services and attachments

### Configure and authorize

Under **设置与备份 → AI 模型服务** (Settings and backup → AI providers), an administrator selects a provider and enters a valid base URL, an available model ID, and API key, then enables it and chooses a default. Do not put keys in URLs, notes, source, screenshots, or public issues.

Windows keys are protected with the current Windows user's DPAPI. Existing keys are not returned to the page; a blank key preserves the saved one. Changing the base URL clears the old key to prevent accidental forwarding to another endpoint. Moving to another computer or Windows account normally requires re-entry.

Product summaries, company-knowledge excerpts, and images are separately authorized. Confirm that the exact model supports images before enabling vision; models from the same provider can differ. Local features remain available without AI or data-sharing permission. Connection testing is itself a real API request.

### Available AI operations

- Naming: suggest a short internal name from the submitted image or actual text. Adoption is explicit; filenames are not product facts.
- Candidate review: provide reasons and differences for local candidates. Model suggestions and human confirmation remain separate.
- Chat: answer manually entered questions, or use relevant product and company-knowledge excerpts when permitted. Chat does not automatically modify, merge, list, reprice, or delete products.
- Reasoning options: show verified official choices for the exact provider and model. Unknown models use the default rather than guessed parameters. Higher effort can increase latency and token usage.

These operations share provider settings and a daily request cap. The cap is not a monetary budget; actual charges depend on the provider account. Explicitly submitted data goes to the selected model provider, and answers need verification.

### Persistent company knowledge and chat modes

Under **AI 管家 → 公司知识** (AI assistant → Company knowledge), create a title, category, and text entry, or import TXT, MD, CSV, JSON, PDF, DOCX, and XLSX. Import uses attachment extraction limits: up to six files, 10 MB each, 20 MB combined, and 16,000 extracted characters per document. Only extracted text is retained, not the original file or complete formatting. A partial-content indicator does not mean the whole document was read. Keep original files elsewhere.

Signed-in members can search local knowledge. Administrators or an entry's creator can edit, deactivate, and restore it. Management, viewing, and local retrieval do not require a cloud AI key. Inactive entries are excluded from normal retrieval. Knowledge text is stored in the local database and is retained in complete database backups; never publish the library or its backups to GitHub.

Chat has three modes:

- **智能助手 — Assistant:** combine permitted, relevant company excerpts with general discussion.
- **公司知识 — Company knowledge:** prioritize internally retrieved sources and explain missing evidence.
- **通用对话 — General:** do not read the company catalog or knowledge base; user-submitted attachments can still be analyzed.

Product and document-sharing permissions are independent. Retrieval supplies a bounded set of relevant records or excerpts, not a claim to have read the entire library. `[P1]`, `[K1]`, and source cards refer to actual items retrieved for that question. Product-image cards are rendered locally; displaying them does not mean the model has visually analyzed the original image.

### Explicit public web search

An administrator must configure and enable an available official DeepSeek search service and permit web search. The chat model may be selected separately. Before sending, the user checks **联网** (Web) and fills the separate **公开搜索关键词** (Public search query) field, up to 128 characters.

Only that public query goes to the search service. Internal retrieved excerpts and the full conversation are not automatically included. Do not enter business secrets, credentials, or private personal data in that field. Actual results are supplied to the chat model; `[W1]` references can be checked. Failed or empty searches are reported rather than presented as successful browsing.

A web-enabled question consumes two quota units: one search and one model request. Separate search fees and account availability depend on the provider. Web search is not the storefront browser and does not guarantee shop login or exact-SKU price collection.

### Chat attachments

Images, TXT, MD, CSV, JSON, PDF, DOCX, and XLSX are supported. Choose, drag, or paste screenshots, then preview or remove attachments before sending. Failed sends preserve drafts and attachments for retry.

| Limit | Current value |
| --- | --- |
| Attachments per send | Up to six |
| Individual file | Up to 10 MB |
| Combined files per send | Up to 20 MB |
| Extracted document text | Up to 16,000 characters; partial extraction is indicated |
| PDF | Up to 30 pages; scans may need to be submitted as images |
| XLSX | First three sheets, up to 200 rows and 40 columns each |
| Multi-turn references | Roughly the latest five turns; up to 12 old and new attachments and 100,000 characters combined |

Documents are reduced to bounded text on the host. Full layout, embedded images, and every sheet are not sent. Macros are not executed and formulas are not calculated; existing cached values or uncomputed markers are shown. Chat attachments do not automatically become product images. References are held in the host's current-login-session memory for about 60 minutes and can also expire on restart, clearing chat, or capacity limits. Keep original files for later re-upload.

## Activity, Trash, and permissions

Recent activity combines actor and start / end date filters. Dates cover complete calendar days in Beijing time. Today, last seven days, and last 30 days shortcuts are available, with 40 records per page. Filtering applies to the entire log, not just the visible page.

Cards support direct editing, one-item removal, and selection of maintainable products on the current page for batch movement to Trash. Batch removal checks every target before committing rather than intentionally partially succeeding. Trash images and details require maintenance permission. Restoration retains company codes, images, and platform / SKU records.

Administrators, a product's creator, or its current owner can maintain it. Do not replace separate member accounts with a shared administrator password. Browser assistance additionally requires requests from the host's local address; product-editing permission over the LAN does not relax this restriction.

## Backup, recovery, and migration

By default, `data` next to the application holds the database, originals, thumbnails, backups, and optional encrypted AI configuration. Dedicated shop browser profiles are local and are excluded from existing backup exports.

While running, the service attempts one local automatic backup per day and retains the seven most recent automatic backups. Manual backups are not automatically pruned. Download complete ZIP backups through settings and keep another copy elsewhere. Backups on the same disk do not protect against disk failure.

To recover:

1. Normally close the relevant service and retain a complete copy of the current data directory.
2. Extract the backup into a new empty directory. Confirm `library.db`, `images`, and `thumbnails` are at the same level.
3. Point the application at that restored directory, or use it as `data` next to the executable.
4. Sign in with an account from that backup and check counts, originals, categories, SKUs, price history, and ownership.
5. On a different computer or Windows account, reconfigure AI keys. Dedicated shop profiles do not migrate through these backups; sign in normally again.

Never upload backups, databases, encrypted configuration, or browser profiles to a public repository. Encrypted configuration is still private internal data.

## Run and build from source

The Windows development baseline is Python 3.13. Runtime and desktop requirements are pinned in `requirements.txt`; test requirements are in `requirements-dev.txt`.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools\download_model.py
.\.venv\Scripts\python.exe desktop.py
```

Start a service without the desktop window:

```powershell
.\.venv\Scripts\python.exe desktop.py --serve --host 0.0.0.0 --port 8765
```

Use `--host 127.0.0.1` for local-only development. `MIDOO_DATA_DIR`, `MIDOO_HOST`, and `MIDOO_PORT` set data and service parameters; the active launcher determines which settings apply.

Prepare the local model according to [`models/dinov2-small/README.md`](../models/dinov2-small/README.md). Large weights are not committed to the source repository. The explicit `tools/download_model.py` setup step fetches the pinned ONNX revision and verifies SHA-256. Source users can also obtain `dinov2-small-onnx-model.zip` from Releases and place the verified file as described in the model documentation; Setup and the complete portable archive already include it. Do not rename an unrelated model as a substitute. Normal inference does not send images to a cloud service or automatically download weights.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
Get-ChildItem tests\*_ui_smoke.cjs | ForEach-Object { node $_.FullName }
.\.venv\Scripts\python.exe -m pip install pyinstaller
powershell -NoProfile -File .\tools\build_windows.ps1
```

The build script checks the model checksum and uses `Midoo.spec` to create `dist/windows/Midoo.exe`. Frontend checks require Node.js; normal packaged use does not. Validate the final executable against a separate temporary library. A public archive contains application files, required model assets, and documentation—not working data. Do not rely on `.gitignore` as the only release-privacy check.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Desktop window cannot start | Check WebView2 and try the compatible service manager; obtain runtimes from official sources |
| A new installation shows an empty library | Check its displayed data path; do not re-import over the old library; select it with `-DataDir` |
| Host works but teammates cannot connect | Check the shared network, host uptime, correct IP / port, and company firewall policy |
| Port belongs to another program | Identify whether it is another Midoo instance; do not kill an unknown process; use another `MIDOO_PORT` if needed |
| No duplicate candidates | Confirm import and model / index readiness; add clear main or detail images and review manually |
| AI controls are unavailable | Check default provider, usable model, key, enabled state, vision / summary permission, and quota |
| Direct price collection fails | Script or login pages may lack a static quote; use host-local browser assistance or enter a verified price |
| Shop requests login again | Dedicated profiles retain only sessions the platform permits; expiry and risk checks still need the user |
| Cross-currency ratio shows — | Check currencies, baseline, and a usable ECB reference cache; retain original prices while addressing connectivity or supported currencies |
| An old price remains with a failure notice | The last valid record is deliberately retained; compare last success and last attempt times |

## Privacy, license, and reporting

Application source is under the [PolyForm Noncommercial License 1.0.0](../LICENSE), without a default commercial grant to third parties. The complete license controls over this summary. Dependencies, DINOv2 model, and weights retain their own licenses; see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Include version, reproduction steps, and redacted errors in reports. Do not submit product databases, user tables, complete backups, API keys, cookies, shop profiles, customer data, or sign-in-token URLs. Use the repository's private vulnerability-reporting channel if enabled; otherwise begin with a contact request that contains no sensitive details.

Routine validation uses temporary databases, synthetic offline pages, simulated sign-in, and mocked AI. It validates software behavior but does not establish that every real shop, account, or paid model has been end-to-end tested.
