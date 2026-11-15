# E-commerce Product Management System


**电商产品管理系统 · Midoo**

[简体中文](README.md) · [English user guide](docs/USER_GUIDE.en.md) · [中文使用手册](docs/USER_GUIDE.zh-CN.md) · [License](LICENSE)

A Windows desktop workspace for a local product image library, platform records, SKC / SKU variants, manually confirmed listing states, and price records. Authorized teammates on the same local network can share the host's library. The desktop edition opens in its own window and provides desktop and Start menu shortcuts.

**Version: 1.8.0 · Windows 10 / 11, 64-bit.** The application interface is primarily Chinese. This repository provides complete Chinese and English documentation; English documentation does not imply that every interface label is translated.

> The source is publicly available under the **PolyForm Noncommercial License 1.0.0**. Third-party commercial use is restricted. This is a **source-available** project, not OSI-defined open-source software that permits commercial use. Commercial use requires separate permission from the rights holder. The [LICENSE](LICENSE) controls; third-party dependencies and model weights retain their own licenses.

## Download and start

Download `EcommerceProductManager-Setup-1.8.0.exe` from [Releases](https://github.com/myflww/ecommerce-product-management-system/releases).

1. Run the Setup executable and follow its window. It installs for the current user in `%LOCALAPPDATA%\Programs\EcommerceProductManager`, without administrator rights or PowerShell execution-policy changes, and creates desktop and Start menu shortcuts named **电商产品管理系统**.
2. Open the shortcut. The desktop window uses Microsoft Edge WebView2. If the required desktop environment is unavailable, the application explains the issue and falls back to the compatible service manager, which opens the workspace in a browser.
3. Create your own account and password in an empty library; it becomes the sole owner (老板). There are no preset credentials. Upgrading an existing library recognizes its earliest-created user as owner without recreating accounts.
4. Open **批量入库** (Batch import) to add images or product folders, then **编辑** (Edit) to enter categories, platforms, and SKUs.
5. Open **以图查重** (Image duplicate search) and compare proposed products with the library's candidates.

Alternatively, download `EcommerceProductManager-1.8.0-Windows.zip`, extract all files, and run `Midoo.exe` without installing. `Install-Desktop.cmd` remains available where PowerShell scripts are allowed. The packaged application does not require Python. The WebView2 desktop window and browser-assisted price collection are separate features: price collection also requires an official Microsoft Edge or Google Chrome installation on the host.

When upgrading an existing library, create a backup, close the service normally, and retain the original `data` folder. To point a new installation at an existing library elsewhere:

```powershell
.\EcommerceProductManager-Setup-1.8.0.exe --install --data-dir "D:\Midoo\data"
```

`desktop-config.json` is generated local configuration, not part of the public release. The application does not scan private folders for libraries. Do not replace a populated library with an empty folder. `Midoo.exe --data-dir "D:\Midoo\data"` can also explicitly open an existing library; `MIDOO_DATA_DIR` has highest priority. See [installation, updates, and uninstalling](docs/USER_GUIDE.en.md#installation-updates-and-uninstalling).

The 1.8 installers generate `Launch-Desktop.vbs` and invoke Windows Script Host explicitly for desktop shortcuts, the first launch after installation, and the Start menu uninstall entry. The launcher configures process environment and points `TEMP` / `TMP` at the dedicated runtime directory only for this software launch, without changing parent-process, user, or system environment variables. The default runtime directory is `.midoo-runtime` under the parent of the selected `data` directory. Updates preserve the runtime location recorded in the installation marker; Setup accepts `--runtime-dir`, and the fallback PowerShell installer accepts `-RuntimeDir`. Uninstalling retains product data and the runtime directory.

This helps installed shortcuts run when data is on another drive and C: has limited space. **Setup itself, a directly launched `Midoo.exe`, and a directly launched `Uninstall.exe` remain PyInstaller single-file entry points: their initial extraction still needs free space on the inherited temporary drive. This does not resolve every full-C: scenario.** If an organization disables Windows Script Host, contact its administrator or use an existing permitted entry point. See [runtime directory and temporary-drive space](docs/USER_GUIDE.en.md#runtime-directory-and-temporary-drive-space).

To uninstall, normally close the installed application and prefer the Start menu Uninstall entry, which uses the configured runtime directory. You can also run `Uninstall.exe` directly in the installation directory, subject to the temporary-drive space requirement above. The native uninstaller removes application files and shortcuts while retaining product data. `Uninstall-Desktop.cmd` is a fallback for environments that allow scripts.

## Features

| Workflow | Available operations |
| --- | --- |
| Product image library | Multiple images per product; folder import; merge / split draft groups; company codes, owners, specifications, notes, and custom categories |
| Local duplicate search | DINOv2 visual retrieval, exact-image matching, matched images and evidence, and human same / different / uncertain decisions |
| Platforms and SKUs | Multiple platforms, stores, and link / SKC groups per product; independent variant specifications, baseline prices, currencies, and manual listing states |
| Operational queues | Needs details, needs pricing, preparing to list, at least one listed platform, and all explicitly delisted; queues may overlap while product totals stay unique |
| Price comparison | Compact price table, manual collection for one SKU or a product, manual current-price entry, timestamps, errors, and history |
| Browser assistance | A dedicated persistent collection environment, quote previews, and explicit user selection and confirmation; separate from everyday browser profiles |
| Currency reference | Approximate conversion to the baseline currency using ECB reference rates, with rate dates and original amounts retained |
| Optional AI and knowledge | Naming, candidate-image review, image / document chat, persistent company knowledge, product-image cards, documented reasoning options, and explicit web search |
| Enterprise knowledge and nebula | Current products and documents, explicit links, immutable versions, real relationship maps, and no natural decay |
| Announcements | Word-style rich text and attachments, separate draft / public versions, revision-specific read marks |
| Stores and merges | Same-record store / state filters, persistent merge with conflict-protected undo, Temu current-page extension / offline import, and a dedicated-browser trial |
| Team and recovery | Sole owner, administrators / employees, per-member capabilities, activity, Trash, and authorized backups |

### Important behavior

- **Human listing decisions remain authoritative.** Entering a link, setting a price, or collecting a quote never automatically lists or delists a SKU. Variant deletions can be undone in the edit draft before saving; products moved to Trash can be restored.
- **Duplicate search retrieves internal candidates.** Similar appearance is not proof of an identical SKU. Verify size, material, count, and packaging. The application does not search the entire web, guarantee all duplicates are found, or predict a marketplace's pricing review.
- **Background daily price collection is disabled.** Collection happens only after an explicit user action. Old saved daily schedules do not run. JavaScript-only pages, authentication, verification, geography, and account-specific pricing may prevent collection. An AI API cannot guarantee page access from a URL alone.

## Products, prices, and currencies

Custom product categories are independent of operational queues. A product with no platform records appears in all three preparation queues. A SKU marked as preparing to list contributes to the preparing queue; a non-delisted SKU without a baseline price contributes to needs pricing and needs details. Any SKU manually marked listed makes its product eligible for the listed queue. Only products whose recorded SKUs are all explicitly delisted appear as fully delisted.

The price popover shows baseline, current storefront price, and their ratio per platform / SKU. A USD 20 baseline and USD 15 current price display **75%**: the current price is 75% of baseline, not a 75% reduction. Ratios can exceed 100% after a price increase.

For different currencies, comparison is available only with a usable ECB reference rate and is marked **≈**. It is not a bank execution rate, marketplace settlement amount, or a net amount after fees. Rates are normally cached for 24 hours. On a network failure, a dated cache no older than seven days may be used; otherwise the application explains why conversion is unavailable rather than assuming 1:1. FX requests do not send products, prices, SKUs, or account details to the ECB.

Browser assistance is available only from the host's local address. Sign in normally in the dedicated browser, select the actual variant and region, read the page back into Midoo, then confirm the correct quote and price basis. Local-network users cannot remotely control this browser. Dedicated shop profiles are excluded from the public release and existing backup exports. See [PRICE_TRACKING.md](PRICE_TRACKING.md) and the [English guide](docs/USER_GUIDE.en.md#prices-and-browser-assisted-collection).

## AI and transmitted data

AI is initially unconfigured. Image import and local duplicate search do not need a cloud key and do not automatically generate model requests.

The owner or a member granted `settings.ai` can configure DeepSeek, Qwen, OpenAI, Kimi, Doubao, Zhipu, Gemini, a compatible custom provider, or Claude. Enter the model ID and base URL actually available to the account. Unrecognized models use default reasoning settings instead of guessed parameters.

On Windows, the host encrypts stored keys with the current Windows account's DPAPI. Existing keys are not returned to the interface. Members with model-configuration permission separately authorize product summaries, knowledge excerpts, and visual-data sharing; ordinary administrators do not receive this permission by default. AI use and web search also require `ai.use` and `ai.web`, respectively. Sending chat, testing a connection, generating a name, reviewing candidates, or permitted page-text extraction calls the selected provider and counts toward a shared daily request cap. A request cap is not a monetary budget.

Chat accepts images and TXT, MD, CSV, JSON, PDF, DOCX, and XLSX files. Documents are locally reduced to bounded text before transmission; image sending needs visual permission. Attachments are not automatically imported as products, macros are not run, and spreadsheet formulas are not executed. Extraction, page count, table ranges, and history references are limited; see the [user guide](docs/USER_GUIDE.en.md#ai-services-and-attachments). The repository contains no working API key.

Under **AI 管家 → 公司知识** (AI assistant → Company knowledge), the owner or an administrator with the relevant capabilities can write or import team documents. Extracted text is stored persistently on the host; original files are not retained. Knowledge can be managed and read without an AI key. Chat offers assistant, company-knowledge, and general modes, includes internal excerpts only when permitted, and provides product / document references. Product image cards are displayed locally by the interface; displaying them does not automatically send originals for model vision.

Optional web search requires an available official DeepSeek search configuration enabled by a member with model-configuration permission, plus the user’s web-search capability. For each search, the user explicitly enables it and enters a separate public query. Internal documents and history are not automatically turned into search terms. Results are passed to the selected chat model with source links. A web-enabled question consumes two request units and may incur separate search charges. Do not put confidential information into public queries. Failure or empty results are not evidence of verified current facts.

## Accounts, announcements, and enterprise knowledge

Each workspace has one **老板 (owner)**. The first account in an empty library becomes owner. An upgrade recognizes the earliest-created legacy user, with internal ID as the tie-breaker, retaining existing accounts and passwords. Under **设置与备份 → 团队账号**, the owner sets each member’s role, disabled state, and product, knowledge, announcement, AI, and backup capabilities. Member management and permanent product / knowledge cleanup are owner-only and cannot be delegated. This version provides no ownership transfer. The server checks current permissions; disabling an account invalidates its sessions.

Administrators are a separate role. With account-creation permission, they can add employees, but cannot create administrators or authorize other members. Defaults include catalog maintenance, merges, knowledge creation / editing, and announcement editing / publication. Model configuration, full backup export, knowledge deactivation / restoration, and announcement archiving / restoration require separate grants. Employees normally maintain products they created or own. The owner may adjust non-reserved capabilities, but employees can never maintain enterprise knowledge, even if they created an old entry.

The sidebar’s **公司公告** (Company announcements) shows active public notices, images, and attachments with an explicit read mark. Authorized editors use Word-style paragraph, size, color, list, table, link, image, and file tools. Save a draft, then publish with publication permission. Editing a published notice changes only its working draft; ordinary readers still receive the previous published snapshot and its attachments. Republishing replaces the public revision and creates a new unread state. Withdrawn, expired, or archived notices leave the ordinary list. A read mark records an explicit action for that public revision, not comprehension or agreement. Editing, publication / withdrawal, and archiving / restoration have separate capabilities.

Enterprise knowledge comprises current products and team documents. Maintainers can link a document to up to 30 products. Changes, deactivation, restoration, and merge-related link changes append immutable versions. Only authorized maintainers can inspect old versions; old versions do not enter current AI retrieval. Editing sends a version number to protect colleagues’ work. Company records do not decay or clear automatically with age, access frequency, or temporary chat-attachment expiry. Clearing chat does not clear company knowledge.

**AI 管家 → 知识星云** (AI assistant → Knowledge nebula) maps actual products, documents, categories, SKUs, and explicit links. Search and filter locally, pan or zoom, pause motion, or use the keyboard-accessible list to open sources. Asking from the map fills a chat draft and does not send automatically. Position, brightness, and edges are not model reasoning, authority, or memory strength. Bounded large-library views show total and displayed counts; omitted nodes remain stored. Browsing the map and knowledge does not itself call a model. See [ENTERPRISE_KNOWLEDGE.md](ENTERPRISE_KNOWLEDGE.md), the bundled Chinese technical reference.

## Store filters, persistent merges, and trial import

Platform, store, and listing-state filters match the same platform record. A listed-state filter for one store will not match merely because another store lists the same company product.

Select 2–50 existing products, choose **合并同款** (Merge), select a main product, and review before confirming. Its name, owner, category, specifications, and notes remain. Images, platform / store / SKU records, manual states, provenance, and price histories move to it without automatically coalescing similar SKUs. Source products enter Trash; persistent journals retain their previous metadata and ownership. **最近合并** (Recent merges) remains available after restart. Undo needs merge, product-edit, and source-restore permission; affected knowledge links also require knowledge-edit permission and append versions. Later product, SKU, price, provenance, or knowledge-version / link changes stop direct undo to protect subsequent work. Active merge sources cannot be restored individually, and neither active sources nor their main product can be purged. A merged product may have at most 30 images and 100 platform records.

### Current-page capture extension

Version 1.8 adds a local Edge / Chrome extension for users to manually capture **Temu** pages they can already view in their everyday signed-in browser. Users load the extension themselves: read the current page → check and confirm store attribution → local queue → export offline JSON → import preview in Midoo → choose a new product or merge target → confirm. Reading, queueing, or exporting does not directly change the catalog. This does not repair dedicated-browser sign-in redirects.

The extension requests only `activeTab`, `scripting`, and `storage`. It does not read cookies, passwords, or account data, connect to the local API, browse in the background, or scan a whole store / every SKU. Its queue stays in extension-local storage and exports stay on the computer. Each batch allows up to 20 pages, 20 products, and an 8 MiB JSON file. Store pages provide overviews of currently loaded products; detail pages may also omit variants and independent quotes. Missing fields stay incomplete, and one variant's price is not copied to others. Importing separately downloads and checks main images; a failed preview is not a successful import.

Live Temu coverage of every SKU and independent quote remains unverified. Synthetic offline-page tests do not establish live capture success. See the [user guide](docs/USER_GUIDE.en.md#current-page-capture-extension) and [PAGE_CAPTURE_EXTENSION.md](PAGE_CAPTURE_EXTENSION.md) for manual setup, privacy, and review steps.

### Dedicated-browser trial

The dedicated-browser collection branch retained in the import interface is an **experimental Temu-only adapter**. A dedicated visible browser on the host lets users sign in normally and explicitly read pages. Review up to 20 items per batch and choose create, merge into an existing product, or skip. Only public evidence with verified store attribution qualifies. Recommendations, advertisements, starting prices, and browser-plugin prices are not complete store / variant evidence. Public product / variant IDs remain separate from merchant SKC / SKU. Missing fields are marked; repeated sources do not overwrite human baselines, current prices, metadata, or states. Import permission is required, plus create permission for new products or target-edit permission for existing ones.

**Real Temu sign-in in the dedicated browser has encountered redirect loops. Successful authenticated main-image and full-variant collection has not been demonstrated. No full-store or all-variant extraction is claimed or guaranteed.** If sign-in loops, close collection and use the current-page extension where the everyday browser can display the product, or continue with image batch import; repeated registration is unnecessary. Existing multi-platform SKUs, manual prices, and browser-assisted collection remain available. Store-import `browser-store-profiles`, previews, samples, and images stay private on the host, excluded from GitHub, ordinary backups, and public packages. See [STORE_IMPORT.md](STORE_IMPORT.md), the bundled Chinese technical reference.

## Local network and data

The default local URL is `http://127.0.0.1:8765`. Teammates use the host's LAN address shown in settings and sign in with separate application accounts. The host and its service must remain running. Stopping a service you started stops shared access and automatic backups. Connecting a desktop window to an already running service does not transfer ownership of that service.

Data defaults to the `data` directory next to `Midoo.exe`, unless local configuration or `MIDOO_DATA_DIR` points elsewhere. The authoritative location is shown under **设置与备份 → 局域网与备份** (Settings and backup → LAN and backup). The program does not change firewall rules or expose public ports. Do not expose its internal HTTP service directly to the internet.

Members with `data.backup` can export full backups; ordinary administrators do not receive it by default. Backups contain products, images, accounts and permissions, announcement text / images / attachments / read marks, knowledge documents / versions / links, merge journals, and potentially encrypted AI configuration. Treat them as private. While running, the service attempts one local automatic backup per day and retains the seven most recent automatic backups. A backup on the same disk is not protection against disk failure. Moving to another computer or Windows account normally requires re-entering AI keys. Never upload `data`, databases, company knowledge, announcement images / attachments, backups, `browser-store-profiles` or other browser profiles, passwords, API configuration, any original imported images, storefront samples, real page DOM, exported capture JSON, or QA images to GitHub. Cookies and other account data must also remain private. Public source and packages exclude these records.

## Run from source

The development baseline is 64-bit Windows and Python 3.13. Dependency versions are pinned in the requirements files.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools\download_model.py
.\.venv\Scripts\python.exe desktop.py
```

The local vision model belongs under `models/dinov2-small`. Large weights are not committed to the source repository: the explicit setup command above downloads the pinned public model and verifies SHA-256. Source users can alternatively obtain `dinov2-small-onnx-model.zip` from Releases. Setup and the complete portable archive already include the weights and need no separate model download. See the model's [README](models/dinov2-small/README.md) for provenance. Do not substitute an arbitrary model. Normal runtime does not download weights automatically.

Start only the service, or open the compatible service manager:

```powershell
.\.venv\Scripts\python.exe desktop.py --serve --host 0.0.0.0 --port 8765
.\.venv\Scripts\python.exe desktop.py --service-manager
```

Run validation and build on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
Get-ChildItem tests\*_ui_smoke.cjs | ForEach-Object { node $_.FullName }
.\.venv\Scripts\python.exe -m pip install pyinstaller
powershell -NoProfile -File .\tools\build_windows.ps1
```

The build script verifies the model checksum and uses `Midoo.spec` to produce `dist/windows/Midoo.exe`. Planned 1.8.0 assets go under `dist/release/1.8.0/`: versioned Setup, Windows ZIP, model ZIP, and a checksum file. The final asset manifest controls; this plan does not mean the build or release is complete. `ENTERPRISE_KNOWLEDGE.md`, `STORE_IMPORT.md`, and `PAGE_CAPTURE_EXTENSION.md` will be bundled. Node.js is used by frontend checks, not required by the packaged app. Validate the final executable against an isolated library before releasing. Routine tests use temporary databases, synthetic pages, and mocked AI; they do not establish compatibility with every real shop or account and do not imply paid API testing.

## Repository layout and reporting issues

- `app.py` / `backend.py`: local service, authentication, and product records.
- `desktop.py` / `desktop_shell.py`: desktop window and service management.
- `static/`: local interface assets.
- `price_*` / `browser_price_*` / `exchange_rates.py`: prices, browser assistance, and reference FX.
- `ai_service.py` / `ai_capabilities.py` / `chat_attachments.py`: optional models and document extraction.
- `permissions.py` / `announcements.py`: sole ownership, per-member capabilities, and rich-text announcements.
- `knowledge_store.py` / `knowledge_graph.py` / `chat_memory.py` / `web_grounding.py`: enterprise knowledge, immutable versions, recorded-relationship maps, session references, and public search.
- `product_merge.py` / `store_import_*`: persistent product merges and experimental Temu import.
- `browser-extension/` / `page_capture_import.py` / `page_capture_package.py`: current-page extension, offline-file validation, and the fixed-file download archive.
- `ENTERPRISE_KNOWLEDGE.md` / `STORE_IMPORT.md` / `PAGE_CAPTURE_EXTENSION.md`: bundled Chinese technical references; the bilingual guides explain primary operations.
- `models/`: vision-model provenance and third-party licenses.
- `tools/`: per-user install and uninstall scripts.
- `docs/` / `tests/`: bilingual documentation and validation cases.

For a bug report, include the application version, reproduction steps, and redacted error information. Do not publish a database, backup, API key, cookie, complete shop sign-in URL, or private customer / team information. Use GitHub private vulnerability reporting if enabled; otherwise start with a contact request that contains no exploit or private-data details.

## License and third-party content

Application source is under the [PolyForm Noncommercial License 1.0.0](LICENSE). Learning, research, modifications, and redistribution must comply with that license. Obtain separate permission before commercial use. The publisher retains its own rights in code it owns; that does not grant commercial permission to third parties.

The original DINOv2 model and corresponding weights retain their Apache-2.0 license. Other dependencies retain their respective licenses. The application's noncommercial restriction does not replace those third-party licenses. See [model provenance](models/dinov2-small/README.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
