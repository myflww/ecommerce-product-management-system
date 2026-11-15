# Public release checklist / 公开发布检查

This checklist describes the files that may be published. It is not a dump of a working installation.

本清单用于准备干净的公开源码与发行包，不能把日常工作目录整体上传。

## Include / 应包含

- Original top-level Python source modules used by the application, desktop entry point and image index.
- `static/` application HTML, JavaScript, CSS, and original icons.
- Synthetic unit/UI tests and test-only servers that use temporary databases and mock APIs.
- Requirements, build/install scripts, source model metadata, pinned model download/checksum instructions, and original third-party license files.
- Chinese/English README and user guides; `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, and `third_party_licenses/`.
- Only the eight reviewed files under `browser-extension/`: `manifest.json`, `popup.html`, `popup.css`, `popup.js`, `capture-core.js`, `collector.js`, `README.md`, and the original decorative `icon.svg`. Embedded resources and the extension download must use that exact public list.

## Exclude / 必须排除

- `data/`, `qa-data/`, `backups/`, all SQLite databases, original merchant/product images and thumbnails.
- `ai-config.json`, `.ai-vault-key`, `.env*`, API keys, credentials, authentication cookies and browser profiles.
- `.work/`, `.build/`, build caches, virtual environments, `__pycache__/`, personal absolute paths, coordination/chat records and private screenshots.
- Internal delivery folders or old archives, which may contain live business data or backups. Assemble each release from an explicit clean file list.
- Real merchant product identifiers and tracking URLs in test fixtures; substitute consistent synthetic identifiers before publishing. Do not change the assertions' behavior while anonymizing.
- `tests/test_real_product_regression.py`: a private-corpus regression tied to a local company's images and internal identifiers; omit it from the public test set.
- Manually launched UI fixture servers unless they require an explicitly isolated test data directory. A test server must not silently reuse an existing application's default data directory.
- Captured storefront JSON exports, extension queues, real page DOM or samples, and collected product images. These are local business data, not extension source or test fixtures.
- `.midoo-runtime/`, generated `Launch-Desktop.vbs`, and `.ecommerce-product-manager-install.json`. Installers generate machine-local launch paths after installation; publish their generator source, never a launcher copied from a working machine.

## Source and binary distribution / 源码与程序分发

Keep large model weights out of the source Git history when distributing them as a separate release asset. Provide the pinned version, SHA-256 and download instructions. A Windows executable that embeds those weights must still carry the model's Apache-2.0 text and provenance alongside the application's noncommercial license.

发布二进制程序时，应一并提供应用许可、版权通知、第三方许可目录、模型来源和中英文说明。只写依赖名称或许可证缩写，不能替代应保留的完整通知。

## Final verification / 发布前核验

1. Inspect the exact staged file list, archive entries and Git status; use an explicit allowlist for the initial publication.
2. Scan the staged source, tests, screenshots, Git history and release archives for secrets, private data, business URLs and absolute user paths.
3. Verify model and executable checksums. Start the release with an empty temporary data directory; never test first-run setup against a company database.
4. Confirm that the release contains full license texts and the documented installation/shortcut scripts; it must contain no company database or browser login profile.
5. Review the repository description and README: describe the project as **source-available, noncommercial**, not as OSI-approved open source.
6. Re-check dependencies and their notices whenever requirements, embedded native libraries, Python, or the packaging toolchain change.

The checklist is a release procedure, not a guarantee that every future artifact has already been audited. The final published commit and release archive must be checked separately.
