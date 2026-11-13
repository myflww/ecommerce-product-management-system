# AI 管家思考选项依据

核对日期：2026-11-13。只使用已实际打开的服务商原始文档。能力表是本版本的已核实名单，不自动猜测未知模型、未来版本、自定义代理或方舟接入点背后的能力。运行时不自动联网拉取文档，也不读取或发送密钥。

## DeepSeek

- [Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)：`deepseek-flash`、`deepseek-v4-pro` 的有效 `reasoning_effort` 是 `none/low/high/max`。不填写时启用思考且强度为 high。
- [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)：`thinking.type` 是 enabled/disabled；兼容的 medium/xhigh 会折算为 high，不做重复等级。
- [Vision](https://api-docs.deepseek.com/guides/vision/) 与 [Anthropic 兼容](https://api-docs.deepseek.com/guides/anthropic_api/)：flash 支持图片，v4-pro 不处理图片。图片能力与管理员发送授权分别检查。

## OpenAI official documentation

- [Reasoning](https://developers.openai.com/api/docs/guides/reasoning?api-mode=chat)、[Chat 请求结构](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)：Chat 采用 `reasoning_effort`，推理模型使用 `max_completion_tokens` 替换旧字段。
- [部署指南](https://developers.openai.com/api/docs/guides/deployment-checklist)：GPT-6 Astra / 6.1 Sol 是 low/medium/high/xhigh/max；6 Sol / 6 Luna 另支持 none。
- [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra)：none/low/medium/high/xhigh/max。
- [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5)、[5.4](https://developers.openai.com/api/docs/models/gpt-5.4)、[5.2](https://developers.openai.com/api/docs/models/gpt-5.2)：none/low/medium/high/xhigh；[5.1](https://developers.openai.com/api/docs/models/gpt-5.1) 没有 xhigh。

## Claude

- [Effort](https://platform.claude.com/docs/en/build-with-claude/effort) 与 [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)：强度放在 `output_config.effort`，自适应思考用 `thinking.type=adaptive`。4.6 可用 low/medium/high/max；4.7/4.8 及代码中列出的 5.x 型号有 xhigh。不同型号关闭规则不同，始终思考型号不提供关闭，部分 5.x 只开放共同的五档强度。
- 未向只支持 extended thinking 的旧型号发送 adaptive。当前模块未实现需要 budget_tokens 的旧型号手动预算。

## Qwen / 百炼

- [深度思考](https://help.aliyun.com/zh/model-studio/deep-thinking)、[视觉理解](https://help.aliyun.com/zh/model-studio/vision)：已列出的商业混合型号用 `enable_thinking=true/false`。这属于开关，不能假称官方有 low/medium/high 等级。
- Qwen Plus 的日期快照只识别 2025-04-28 起、Flash 只识别 2025-07-28 起。不把所有 Qwen3 开源型号归为可关闭，部分开源型号只接受流式。
- [API 兼容升级](https://help.aliyun.com/zh/model-studio/api-compatibility-upgrade-notice)：Qwen4 起输出预算含义调整；本版不推断 Qwen4 参数。已列出的旧商业 Qwen 保持原 max_tokens，以免提高到超出某型号的最终回复上限。

## Gemini

- [OpenAI 兼容接口](https://ai.google.dev/gemini-api/docs/openai)：`reasoning_effort` 映射为官方思考等级或预算。2.5 Flash/Flash-Lite 可关闭，2.5 Pro 不可关闭；3 Flash / 3.1 Flash-Lite 可用 minimal/low/medium/high；4 Hydrogen 另有 xhigh。没有将一套等级套给所有 Gemini。

## Kimi

- [官方 K2.5 模型卡](https://huggingface.co/moonshotai/Kimi-K2.5) 与 [参数比较](https://platform.kimi.ai/docs/api/models-overview)：K2.5/K2.6 用 thinking 开关。
- [当前思考模型](https://platform.kimi.ai/docs/guide/use-thinking-models)、[Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)：K3/K3.1 Pro 的有效强度是 low/high/max，但多轮要求回传完整 assistant、reasoning_content。本地当前聊天适配仅保留文字与附件历史，所以本版不开放 K3 强度，明确显示适配限制。

## GLM

- [深度思考](https://docs.bigmodel.cn/cn/guide/capabilities/thinking)：5.3 系列是 low/high/max，不能通用地关闭；5.2 提供关闭/high/max，别名等级会合并；所列旧版本是 thinking.type 开关。

## 本地实现约束

- `default` 不发送任何思考控制字段，保留各服务商的默认模式。
- 已确认思考会和正文共用输出配额的型号将输出上限至少提高到 32768（包含 default 开启思考的 DeepSeek），避免旧 2048 上限在思考完成前耗尽。此数值是本软件的输出上限，不是伪造的官方思考档位，也不保证复杂任务一定完成。
- 调用方应按预算使用最长 300 秒的响应等待；不会自动重试付费请求。应检查 finish_reason/stop_reason 的长度截断，不把空正文包装成成功。
- 每个请求先校验所选等级，再计入本地日限额。任何未经核实的等级返回 400，不静默降档。
- 有效等级、账号访问权限和实际费用仍取决于服务商；本次验证使用 mock，没有真实云端付费调用。
