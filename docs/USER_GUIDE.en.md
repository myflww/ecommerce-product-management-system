# E-commerce Product Management System User Guide

Applies to version 1.7.0 (Midoo for Windows)

[简体中文](USER_GUIDE.zh-CN.md) · [Project home](../README.en.md) · [License](../LICENSE)

This guide is for users whose use is permitted by the license. Source is public, but commercial use is restricted. If using someone else's published copy for a business or shop, obtain the necessary permission first. The publisher retains its own rights in code it owns.

## Contents

1. [Requirements and desktop window](#requirements-and-desktop-window)
2. [Installation, updates, and uninstalling](#installation-updates-and-uninstalling)
3. [First login and team accounts](#first-login-and-team-accounts)
4. [Image import and product records](#image-import-and-product-records)
5. [Categories and operational queues](#categories-and-operational-queues)
6. [Platforms and SKC / SKU variants](#platforms-and-skc--sku-variants)
7. [Store filters and persistent product merges](#store-filters-and-persistent-product-merges)
8. [Trial storefront import](#trial-storefront-import)
9. [Local image duplicate search](#local-image-duplicate-search)
10. [Prices and browser-assisted collection](#prices-and-browser-assisted-collection)
11. [Company announcements](#company-announcements)
12. [AI services and attachments](#ai-services-and-attachments)
13. [Activity, Trash, and permissions](#activity-trash-and-permissions)
14. [Backup, recovery, and migration](#backup-recovery-and-migration)
15. [Run and build from source](#run-and-build-from-source)
16. [Troubleshooting](#troubleshooting)
17. [Privacy, license, and reporting](#privacy-license-and-reporting)

Enterprise knowledge, immutable versions, and the map are covered under [Persistent company knowledge and chat modes](#persistent-company-knowledge-and-chat-modes) and [Knowledge nebula](#knowledge-nebula).

The interface is primarily Chinese. Original labels identify controls; this guide does not claim a fully localized English interface.

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

1. Download `EcommerceProductManager-Setup-1.7.0.exe` from [Releases](https://github.com/myflww/ecommerce-product-management-system/releases).
2. Run Setup and confirm installation in its standard window. No PowerShell execution-policy change is needed.
3. Installation is per-user under `%LOCALAPPDATA%\Programs\EcommerceProductManager`, without administrator rights.
4. Open **电商产品管理系统** from the desktop or Start menu.

The package contains no publisher's business library, account, or API key. An independent first run creates your own empty library and does not scan private folders. Alternatively, download `EcommerceProductManager-1.7.0-Windows.zip`, extract all files, and run `Midoo.exe` as a portable application. Do not run it from an archive preview. The ZIP's `Install-Desktop.cmd` and `tools/Install-Desktop.ps1` provide a fallback installation method where scripts are allowed.

### Use an existing library

Do not re-import a library or substitute an empty folder. Back up first, then explicitly select its existing path, for example:

```powershell
.\EcommerceProductManager-Setup-1.7.0.exe --install --data-dir "D:\Midoo\data"
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

Create an account and a password of at least eight characters in an empty library. That account becomes the sole **老板 (owner)**; there are no preset credentials. An upgrade recognizes the earliest-created legacy account, breaking equal timestamps by internal ID. Ownership metadata preserves existing users and passwords. This version’s member interface cannot transfer, disable, demote, or create a second owner.

Add members under **设置与备份 → 团队账号** (Settings and backup → Team accounts); use separate accounts. Change your own password under **我的账号**. The owner can create administrators or employees. An administrator with account-creation permission can only add employees, not create administrators or adjust their own or anyone else’s capabilities.

The owner opens a member’s **角色与权限** (Role and permissions), chooses the base role and active / disabled state, then selects **继承角色** (Inherit role), **允许** (Allow), or **禁止** (Deny) per capability and saves. Inheritance follows role defaults; exceptions apply to that individual. The server checks permissions on subsequent requests. Disabling invalidates existing sessions. If another edit causes a version conflict, reopen and compare current settings before saving.

| Role | Defaults and fixed limits |
| --- | --- |
| Owner | All capabilities; member management and permanent product / knowledge cleanup cannot be delegated. A knowledge-cleanup capability name does not imply a separate cleanup interface exists |
| Administrator | Create / import / maintain the entire catalog, restore and merge products, create / edit knowledge, add employees, configure manual price collection, use AI / web search, view all activity, and edit / publish announcements. Model configuration, full backup export, knowledge deactivation / restoration, and announcement archiving / restoration require separate owner grants |
| Employee | Create / import products, edit / remove / restore products they created or own, and use AI / web search. The owner can adjust non-reserved capabilities. Employees can never maintain enterprise knowledge, even when they created a legacy entry |

“Own” products are those created by the member or currently assigned to their name. Whole-catalog editing, removal, and restoration are separate capabilities. Edit permission does not include removal or restoration automatically. Knowledge additionally requires the owner / administrator role and the relevant capability; employee knowledge overrides cannot enable maintenance. Non-reserved capabilities such as full backups and model configuration depend on the member’s actual grants.

Share the LAN address from settings or the compatible manager with teammates on a trusted network. The application does not change firewall rules, forward public ports, or configure startup automatically. Browser assistance and storefront import also require the host’s local address. LAN catalog privileges do not allow remote control of the host browser or clearance of its sign-in profiles.

## Image import and product records

**批量入库** (Batch import) accepts image selection, product folders, drag and drop, and pasted screenshots. A folder can hold multiple views of one product. Loose images start as separate groups. Before committing, rename, merge, or split groups and add specifications, notes, a category, and platform information.

Current limits are 300 images and 250 MB per batch, 20 MB per image, and 30 images per product. Split larger imports into batches. Keep clear original main images, detail views, and specification text; a similar background is not a product fact.

Each product has an automatically generated company code, name, owner, specifications, notes, and images. Exact-file duplicate notices do not automatically merge products: the same image may occur in records for different bundles and needs human review.

**AI 命名** (AI naming) is optional. It offers a suggestion that changes the name only after adoption. Manual naming and normal import work without AI configuration.

## Categories and operational queues

Custom categories, such as pillar candles, wax sheets, crystals, or molds, are product metadata independent of operational queues. Combine category or uncategorized filters with keywords, platform, store, and platform state.

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

## Store filters and persistent product merges

The catalog’s store dropdown uses existing platform records and follows the platform filter. Platform, store, and listing state must match the same record. If a product is preparing in store A and listed in store B, filtering “store A + listed” will not match it because of B. Whole-product operational queues still consider all recorded SKUs.

After confirming that existing records represent the same product:

1. Select 2–50 products in the catalog and choose **合并同款** (Merge).
2. Choose the main product and review images, platform / store groups, SKUs, and counts.
3. Confirm after checking. The main product retains its name, owner, category, specifications, and notes. Original images and platform / store / SKU records, links, manual states, price history, and provenance move to it. Similar SKUs are not automatically coalesced.
4. Source products enter Trash. A persistent journal keeps prior metadata and ownership. Open **最近合并** (Recent merges) to inspect and undo; restarting does not clear the journal.

Merging requires `products.merge`, edit rights for every selected product, and removal rights for source products. The result may contain at most 30 images and 100 platform records. Changes after preview require a fresh preview. Linked knowledge also requires edit permission for the affected documents; links move to the main product in the same transaction and append knowledge versions.

Undo requires merge and product-edit permission plus source-restore rights. Knowledge-edit rights are checked again. Later product, SKU, quote, provenance, or knowledge-version / link changes cause a conflict and stop direct undo. Review current records; repeating an old request does not force an overwrite. Restoring knowledge links appends new versions and preserves every older version.

Merged sources cannot be individually restored through ordinary Trash. An active merge’s main and source products cannot be permanently purged. A product linked to knowledge, including inactive documents, also requires explicit unlinking by a knowledge maintainer before the owner can purge it. See [STORE_IMPORT.md](../STORE_IMPORT.md), the bundled Chinese technical reference.

## Trial storefront import

**批量入库 → 从店铺前端导入 · 试用** (Batch import → Storefront import · Trial) currently supports **Temu only and is experimental**. Real dedicated-browser sign-in has encountered redirect loops. Successful authenticated main-image and complete-variant collection has not passed live acceptance. Full-store extraction, all-variant coverage, and success are not guaranteed. Browser / driver availability only means runtime components are ready. The capability endpoint separately reports `experimental=true`, `live_capture_verified=false`, and limitations.

1. A member with `products.import` enters a Temu storefront URL locally on the software host and selects **打开专用浏览器** (Open dedicated browser).
2. Sign in normally, choose the region, and complete required verification personally. Explicitly select **读取店铺** (Read store). The feature does not bypass verification or read everyday browser profiles.
3. Select discovered items for detail collection, up to 20 per batch. Only attributable store products from page JSON / visible content qualify. Recommendations, advertisements, and other stores’ products are not import evidence.
4. Review main images, names, specifications, individual currencies / quotes, and source links in a local preview. Choose create, add to an existing product, or skip. Previewing does not write the catalog. Creating also requires `products.create`; adding to a product requires its edit permission.
5. New listings are marked listed only when the user explicitly selects the manual listing-confirmation option. A collected quote or URL never implies listing confirmation.

Public product / variant IDs remain separate from merchant SKC / SKU. Missing stable variant identities produce records needing completion, not invented SKU prices. Missing prices, specifications, or store-attribution evidence are reported as gaps or ineligible items. Starting prices, crossed-out prices, and browser-plugin supply prices are not copied to every variant. More than 100 platform records per product cannot be committed directly.

Platform, store, site, public product, and variant provenance identify repeated sources. Recollection does not overwrite existing manual baselines, current quotes, metadata, or listing states. Main images are downloaded safely, checked, and indexed locally. Previews belong to the current login session, expire after about 30 minutes, and have count / memory limits. A successful commit releases preview image memory.

If sign-in repeatedly redirects, close collection and continue with ordinary image batch import; repeated registration is unnecessary. Multi-platform SKUs, manual current prices, and existing browser-assisted collection remain independently usable. Import-specific `browser-store-profiles` stays local, does not copy everyday cookies, and is excluded from ordinary backups, GitHub, and public packages. Real page behavior, sign-in, and variant completeness remain unverified boundaries. See [STORE_IMPORT.md](../STORE_IMPORT.md).

## Local image duplicate search

1. Open **以图查重** (Image duplicate search) and add main or detail images of the proposed product, up to five query images.
2. Check local index readiness. Until ready, only identical-file and previously human-confirmed matches are available.
3. Run normal search and inspect the small set of recommended candidates. Expand weaker candidates when needed.
4. Compare matched images, specifications, quantity, packaging, and owner before choosing same, uncertain, or different.

By default, at most three recommendations are shown, with additional candidates available explicitly. The interface does not present an uncalibrated retrieval score as an identical-SKU probability. Related colors or bundle sizes can be retrieved together; backgrounds, occlusion, and different views can also cause misses.

Human feedback applies to later review of the same query image; it does not automatically train the entire model. Rebuilding derived embeddings must not delete original images or business records. The application displays a limited state when the model is unavailable.

With AI-use capability and vision-sharing permission, **AI 查重** (AI review) compares images for up to eight locally retrieved candidates. It does not search every external marketplace or overwrite human decisions. Model suggestions, visual resemblance, and actual SKU identity are different kinds of evidence.

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

## Company announcements

The sidebar’s **公司公告** (Company announcements) shows active public notices and an unread count. Open a notice to read its text, view images, download authorized attachments, and select **标记已读** (Mark read) for its current public revision. Announcements are company-authenticated content: text and files both require sign-in and appropriate visibility. A read mark is not proof of understanding, agreement, or an electronic signature. Republishing a new revision requires a new mark.

With `announcements.edit`, select **撰写公告** (Write announcement). The Word-style toolbar offers headings / paragraphs, sizes, bold, italic, underline, strike-through, text / background colors, alignment, lists, tables, links, undo / redo, and format clearing. Upload or paste images and use the file-attachment control for documents. Microsoft Word is not required, and complete Word layout fidelity is not promised. Rich text is sanitized; external web images cannot become body images directly.

1. **保存草稿** (Save draft) keeps work for announcement managers; ordinary employees cannot see a new draft or its new attachments.
2. Optionally set pinning and expiry. Expiry input uses local time; blank means no expiry. Titles allow up to 160 characters and rich-text data up to 100,000 characters.
3. **保存并发布** (Save and publish) requires `announcements.publish`, a title, and text or attachments. It creates the current public snapshot.
4. Editing a published notice and saving its draft does not change the public text, pin, expiry, or attachments. Publish again to replace that snapshot. An attachment removed from the working draft remains available through the previous public snapshot until publication changes it.
5. Publication permission allows withdrawal to draft, removing the notice from the ordinary list. `announcements.archive` allows archiving and restoration; restoration produces a draft that needs publication again. Expired notices leave the ordinary list. Draft expiry edits must be published to affect public visibility.

Editing, publication / withdrawal, and archiving / restoration are separate capabilities. Administrators normally receive edit and publish capabilities; archiving / restoration needs another grant. Other members’ management rights depend on their actual permissions. Managers can inspect revision records. Saves and state changes check current revisions; on conflict, preserve your draft and review the other edit. Archiving permission alone does not expose an unpublished working draft.

| Attachment | Limit |
| --- | --- |
| Images | JPG, PNG, WebP, GIF; up to 5 MB each; safely re-encoded as JPEG and resized to at most 2000 pixels on the longest side; animation and original format are not retained |
| Files | PDF, DOCX, XLSX, PPTX, TXT, MD, CSV, JSON, ZIP; up to 10 MB each; download-only, not executed or extracted |
| Per notice | Up to six images / files, 20 MB combined |
| Total capacity | Up to 2000 notices and 500 MB attachment storage; historically retained attachment bytes also count |

Notice text, versions, read marks, and attachment bytes reside in company SQLite and are included in complete data backups. Images and files are private company data and are excluded from GitHub and public distributions.

## AI services and attachments

### Configure and authorize

Under **设置与备份 → AI 模型服务** (Settings and backup → AI providers), the owner or a member granted `settings.ai` selects a provider and enters a valid base URL, an available model ID, and API key, then enables it and chooses a default. Do not put keys in URLs, notes, source, screenshots, or public issues.

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

Enterprise knowledge consists of current company products and documents. Product, SKU, and image records remain in the catalog and need not be copied into documents. Signed-in members can read company-shared knowledge; this is not a system of private folders or confidential groups. Create / import requires an owner or administrator with `knowledge.create`. Editing needs the relevant own / all-document permission; deactivation and restoration separately require `knowledge.archive`. Employees cannot maintain knowledge even if they created an old entry. Administrator edit rights do not imply deactivation rights. Viewing and local retrieval need no cloud key.

Manual entries permit up to 100,000 characters. Capacity is 2000 entries including inactive documents; reaching it requests expansion, not automatic cleanup. Imports retain only bounded extracted text and provenance, not original files. Split long documents and keep originals separately. Inactive entries leave current retrieval and can be restored.

Use **关联商品** (Related products) when editing to search and select up to 30 products. Links help SKU / product questions retrieve associated procedures and experience. A product moved to Trash leaves current retrieval and the nebula, but existing links remain. Text edits do not automatically discard those links; new links to already removed products are forbidden. Permanent product cleanup requires explicit unlinking, including links from inactive knowledge.

Creation, edits, deactivation, restoration, and merge / undo link changes record versions and actors. Old versions are immutable. Users with permission to edit or deactivate that document can open **版本记录** (Revision history) and read past text, sources, and historical links; viewing does not roll back the current version. Old versions do not enter current AI retrieval. Edits submit the current revision; on conflict, reopen and compare the latest source before saving.

Company records and versions never decay, forget, or clear automatically with age or access frequency. Temporary chat attachments’ roughly 60-minute session retention is separate from enterprise knowledge. Clearing chat does not delete company knowledge. Complete backups retain documents, chunks, history, and links; never publish them to GitHub.

Chat has three modes:

- **智能助手 — Assistant:** combine permitted, relevant company excerpts with general discussion.
- **公司知识 — Company knowledge:** prioritize internally retrieved sources and explain missing evidence.
- **通用对话 — General:** do not read the company catalog or knowledge base; user-submitted attachments can still be analyzed.

Product and document-sharing permissions are independent. Retrieval supplies a bounded set of relevant records or excerpts, not a claim to have read the entire library. `[P1]`, `[K1]`, and source cards refer to actual items retrieved for that question. Product-image cards are rendered locally; displaying them does not mean the model has visually analyzed the original image.

### Knowledge nebula

Under **AI 管家 → 知识星云** (AI assistant → Knowledge nebula), explore products, knowledge documents, categories, and SKUs. Nodes are actual active sources. Edges represent recorded category membership, SKU ownership, and explicit document links, not inferred entities or complete GraphRAG. Brightness, position, size, and animation are visual choices, not authority, probability, or memory strength.

Search by keywords / SKU, filter products or knowledge, pan / zoom, drag nodes, pause motion, or choose a source from the keyboard-accessible list. Open original product or document records. Asking AI from a source fills a chat draft; sending remains explicit and follows AI capabilities and data-sharing permission. Browsing, searching the map, and inspecting versions do not themselves call a cloud model or external graph service.

Large-library views have explicit node limits and show total, matching, and displayed counts. Omitted sources remain fully stored and available to ordinary retrieval. Inactive knowledge and recycled products leave the current map and can reappear after restoration; inactivity on the map never triggers deletion. See [ENTERPRISE_KNOWLEDGE.md](../ENTERPRISE_KNOWLEDGE.md), the bundled Chinese technical reference.

### Explicit public web search

A member with model-configuration permission must configure and enable an available official DeepSeek search service and permit web search. The current user also needs `ai.use` and `ai.web`. The chat model may be selected separately. Before sending, the user checks **联网** (Web) and fills the separate **公开搜索关键词** (Public search query) field, up to 128 characters.

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

Recent activity combines actor and start / end date filters. Dates cover complete calendar days in Beijing time. Today, last seven days, and last 30 days shortcuts are available, with 40 records per page. Filtering applies to the entire log, not just the visible page. `audit.all` permits all-member activity; otherwise the view contains only the current user’s activity.

Cards support direct editing, one-item removal, and selection of maintainable products on the current page for batch movement to Trash. Batch removal checks every target before committing rather than intentionally partially succeeding. Trash images and details require maintenance permission. Restoration retains company codes, images, and platform / SKU records.

Product editing, removal, and restoration separately follow the owner-granted own / all-product capabilities. Being a creator or assigned owner does not override a denied capability. Do not replace separate member accounts with a shared administrator password. Browser assistance additionally requires requests from the host's local address; product-editing permission over the LAN does not relax this restriction.

## Backup, recovery, and migration

By default, `data` next to the application holds the database, originals, thumbnails, backups, and optional encrypted AI configuration; confirm the actual path in settings. The database also holds accounts / capabilities, knowledge documents / chunks / immutable versions / product links, announcements / images / attachments / read marks, and persistent merge journals. Dedicated price and storefront browser profiles, including `browser-store-profiles`, remain local and are excluded from existing backup exports.

While running, the service attempts one local automatic backup per day and retains the seven most recent automatic backups. Manual backups are not automatically pruned. Members with `data.backup` can download complete ZIP backups through settings; administrators do not receive it by default. Export regularly and keep another copy elsewhere. Backups on the same disk do not protect against disk failure.

To recover:

1. Normally close the relevant service and retain a complete copy of the current data directory.
2. Extract the backup into a new empty directory. Confirm `library.db`, `images`, and `thumbnails` are at the same level.
3. Point the application at that restored directory, or use it as `data` next to the executable.
4. Sign in with an account from that backup and check counts, originals, categories, SKUs, price history, ownership, member permissions, knowledge text / versions / links, announcements / attachments / read marks, and merge journals.
5. On a different computer or Windows account, reconfigure AI keys. Dedicated shop profiles do not migrate through these backups; sign in normally again.

Never upload any original imported images, announcement images / attachments, company knowledge, QA images, storefront samples, backups, databases, encrypted API configuration, or browser profiles to a public repository. Public source and distribution packages exclude these records. Encrypted configuration is still private internal data.

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

The build script checks the model checksum and uses `Midoo.spec` to create `dist/windows/Midoo.exe`. The planned 1.7.0 asset directory is `dist/release/1.7.0/`; the final release manifest controls. `ENTERPRISE_KNOWLEDGE.md` and `STORE_IMPORT.md` will be bundled. Frontend checks require Node.js; normal packaged use does not. Validate the final executable against a separate temporary library. A public archive contains application files, required model assets, and documentation—not working data. Do not rely on `.gitignore` as the only release-privacy check.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| A former administrator lacks model settings / backup controls | Ask the owner to review individual capabilities; these require grants, and shared owner passwords are unnecessary |
| Knowledge edit / history controls are missing | Employees remain read-only; administrators also need document-maintenance capabilities, regardless of legacy creator status |
| A saved notice still shows its old content | Draft saves preserve the public snapshot; a user with publication permission must publish the update |
| A merge cannot be undone or its source restored | Review later product / quote / knowledge changes and restoration permission; use Recent merges without bypassing conflicts |
| Temu sign-in keeps redirecting | Live sign-in / complete-variant acceptance remains unresolved; close collection and use image import without repeated registration |
| Desktop window cannot start | Check WebView2 and try the compatible service manager; obtain runtimes from official sources |
| A new installation shows an empty library | Check its displayed data path; do not re-import over the old library; select it with `--data-dir` |
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
