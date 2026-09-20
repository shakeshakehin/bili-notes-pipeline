# harness 自主运行部署说明

只需**一个 LLM 接入点 + 一个字幕目录**，即可全自动：清洗 → 树生成 → 同模型校验 → 修订 → 画图 → 归档 Obsidian。
不依赖 Hermes profile / se-esee(Kimi) / B站凭证。

## 部署三步

1. **配置**（三选一，优先级从高到低）
   - 环境变量：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`
   - `harness.env`（项目根，模板见下）
   - 回退：本机 manager config.yaml（开发机便利）

   ```bash
   # harness.env 模板
   LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
   LLM_API_KEY=你的key
   LLM_MODEL=deepseek-v4-flash
   ```

2. **放入字幕**：`inbox/` 目录（可改 `SUBTITLE_DIR`），扫描 `*.txt`

3. **运行**
   ```bash
   python eval/harness_run.py            # 处理 inbox/ 全部字幕
   python eval/harness_run.py <文件>     # 指定单份
   python eval/harness_run.py --no-png   # 无 Chrome 环境：只出 SVG
   ```

## 输出

- 中间产物：`harness_out/<字幕名>/`（树 json/md、repaired.txt、verdict.json、修订 diff）
- 归档：`OBSIDIAN_ROOT/<YYYY-MM-DD>/`（json + md + verdict + 复核 + 导图 PNG）

每份字幕归档：`<名>.json`（修订后树）· `<名>.md` · `<名>_verdict.json`（校验裁决）· `<名>_复核.md`（人类可读报告）· `<名>_导图.png`（仅逻辑型）

## 单模型模式的已知权衡

- **校验与树生成同模型** → 有自证倾向（产出的错误可能校验时也放过）。CS336 的字节值错误在双模型（Kimi 独立仲裁）下被抓住；单模型下可靠性依赖模型自身。**可配置校验用独立模型**：改 `harness.env` 后手动调 `step5_seesee.py --direct --model <别的模型>`，或直接用默认分支（se-esee/Kimi 外部仲裁）获得独立第三方校验。
- 确定性清洗（subtitle_repair）零 LLM，不受影响；回流修订只 patch 指出的节点 + validate 兜底 + diff 可见，不黑盒。
- 画图需要本机 Chrome（headless）；无 Chrome 用 `--no-png` 只出 SVG。

## 依赖

- 系统 python 3.11（requests）
- Chrome（画 PNG，可选）
- 无 bilibili-cli / 无 B站凭证需求（字幕已就位）
