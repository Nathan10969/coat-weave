# Coating Patent Knowledge Graph — 完整 Pipeline 文档

涵盖 13 个 stage（含 V2 待加的 9.5 和 10）、当前云 API 调用方式、未来本地 AWQ 模型迁移方案、以及一个完整的端到端示例。

---

## 目录

1. [全部 stage 总览表](#1-全部-stage-总览表)
2. [每个 stage 的详细说明](#2-每个-stage-的详细说明)
3. [AWQ 在 A100 80G 单卡部署详解](#3-awq-在-a100-80g-单卡部署详解)
4. [端到端示例：WO2026052438A1 走完 stage 0→10](#4-端到端示例wo2026052438a1-走完-stage-010)

---

## 1. 全部 stage 总览表

13 个 stage 按 per-PDF 流程 + cross-PDF 汇总分两类。

### 1.1 Per-PDF 链路

| # | 名字 | 主代码 | 输入 | 输出 | LLM/VLM 调用（V1.2.5 现状） | V2 AWQ 迁移变化 |
|---|------|--------|------|------|----------------------------|----------------|
| **0** | 编排 | `src/coating_kg/cli.py` `ingest_cmd`（line 26-268）`scripts/run_pipeline.py` | PDF 列表 | — | 无 | 不动 |
| **1** | MinerU 版面解析 | `src/coating_kg/pipeline/pdf_layout.py:parse_pdf`（云）`scripts/a100_stages_0_3/01_batch_mineru.py`（本地） | PDF 文件 | `data/mineru_output/<doc_id>/auto/<uuid>_content_list.json` + `images/*.jpg` + `*.md` | MinerU 自己（不是 LLM） | 已迁本地 6 路 |
| **0.5** | 元数据 + IPC 闸门 ⭐ | `src/coating_kg/pipeline/patent_metadata_extractor.py``cli.py:64-108`（接线） | content_list 首页文本 | `data/patents/<doc_id>__patent_meta.json`（含 `is_coating_patent` / `is_coating_reason` / `section_split_mode` / `examples_page_range`） | **DashScope qwen-plus**，每 PDF 一次 | 切到本地 vLLM Qwen3.6-27B-AWQ |
| **2** | unit 切分 | `src/coating_kg/pipeline/unit_extractor.py:iter_figure_table_units` | content_list (in-mem layout) | units 列表 (in-mem)，含 `unit_id` / `bbox` / `caption_footnote_text` 等 | 无（纯规则） | 不动 |
| **3** | unit 物化 | `src/coating_kg/pipeline/unit_materializer.py:materialize_unit` | unit 对象 + content_list 引用 | `data/units/<unit_id>/`（`meta.json` + `image.png` 或 `table.html` + `caption.txt`） | 无（磁盘 IO） | 不动 |
| **4** | VLM 看图看表 | `src/coating_kg/pipeline/vlm_describe.py``QwenVLClient.describe_figure / describe_table` | image.png 或 table.html + caption + candidate_ids | `data/units/<unit_id>/vlm_description.json`（含 description + identified_entities + subtype） | **DashScope qwen-vl-plus**（figure）+ **qwen-plus**（table） | 切到本地 vLLM Qwen3.6-27B-AWQ（同一个实例处理两种） |
| **5** | 实体兜底打标 | `src/coating_kg/pipeline/entity_tagger.py:tag_entities` | VLM description + caption + table HTML + DB alias 表 | 写回 `unit.tagged_entities`（in-mem，aho-corasick 字符串匹配补漏） | 无（DB 查询 + 字符串匹配） | 不动；PoC 模式 (`--skip-db`) 跳过 |
| **6** | 段落匹配 (PoC-2) | `src/coating_kg/pipeline/paragraph_matcher.py:ParagraphMatcher.match``src/coating_kg/pipeline/paragraph_extractor.py` | VLM description + Examples 段落 list | `data/units/<unit_id>/matched_paragraphs.json`（top_k=10） | **DashScope qwen-plus**，每 unit 一次 | 切到本地 vLLM Qwen3.6-27B-AWQ |
| **7** | 事实抽取 (PoC-3) ⭐最贵 | `src/coating_kg/pipeline/fact_extractor.py:FactExtractor.extract``prompts/fact_extract.txt` | unit + matched paragraphs + VLM description + ontology | `data/units/<unit_id>/facts.json` + `coverage.json` + `proposed_canonicals.json` | **DashScope qwen-plus**，每 unit 一次（最贵的一段） | 切到本地 vLLM Qwen3.6-27B-AWQ + 加 JSON guided_decoding |
| **8** | 极性 + comparison_group + (DB) | `src/coating_kg/pipeline/polarity.py``src/coating_kg/pipeline/comparison_group.py``src/coating_kg/db/insert.py`（DB 模式） | facts list (in-mem) | 写回 `polarity_hint` + `comparison_group`；可选 PG insert | 无（纯规则） | 不动；DB 部分 V2 KG 上线时启用 |

### 1.2 Cross-PDF 汇总（所有 PDF 跑完后跑一次）

| # | 名字 | 主代码 | 输入 | 输出 | LLM/VLM 调用 | V2 AWQ 迁移变化 |
|---|------|--------|------|------|-------------|----------------|
| **9** | 宽表 CSV | `scripts/build_coatings_csv.py` | 所有 `data/units/*/facts.json` + 所有 `data/patents/*__patent_meta.json` | `output/coatings_wide.csv`（每 Example 一行，含 `extraction_confidence_min/mean`） | 无 | 不动 |
| **10** | KG 装载 🔜 V2 待写 | `scripts/load_facts_to_pg.py`（待建） | 所有 facts.json + 所有 passages.json + ontology seeded canonical_ids | PG 5 张表（node / fact_hyperedge / hyperedge_node / passage_hyperedge / passage_hyperedge_node）+ pgvector HNSW 索引 | **本地 BGE-M3** 算 embedding | 全本地实现 |

### 1.3 状态汇总

| 状态 | Stage 列表 |
|------|------------|
| ✅ V1.2.5 已完成 | 0, 1, 0.5, 2, 3, 4, 5, 6, 7, 8, 9（共 11 个） |
| 🔜 V2.0.5 待写 | **9.5**（passage_extractor，single per-doc LLM 设计 — 见 §2.9.5） |
| 🔜 V2.x 待写 | **10**（KG loader 灌 PG + pgvector） |
| 🔄 V2.0 改 base_url | 0.5 / 4 / 6 / 7（云 → 本地 vLLM AWQ） |

---

## 2. 每个 stage 的详细说明

### Stage 0 — 编排（Orchestration）

**职责**：把 stage 1→8 串起来，做错误隔离 + 进度日志。

**主代码**：
- `src/coating_kg/cli.py` 第 26-268 行 `@cli.command("ingest")`：单 PDF 入口
- `scripts/run_pipeline.py` 第 146-222 行 `main()`：多 PDF 批量驱动

**关键设计**：
- 三种模式：默认全跑、`--skip-db`（PoC 模式）、`--dry-run`（只跑 stage 1-3）
- 错误隔离三层粒度：单 stage warn / 单 unit rollback / 整篇 PDF return
- `nullcontext(None)` 让 PoC 和生产共用 `with conn_cm as conn` 代码

**LLM 调用**：无（编排层）

**V2 改动**：无

---

### Stage 1 — MinerU 版面解析

**职责**：把 PDF 解析成结构化 JSON（每页 blocks：text/title/image/table/equation 含 bbox），并把 figure / table 切成图片或 HTML。

**主代码（两条路径）**：
- 云 API：`src/coating_kg/pipeline/pdf_layout.py:parse_pdf`（line 313）— 走 MinerU 在线 API
- **本地 6 路**：`scripts/a100_stages_0_3/01_batch_mineru.py:run_mineru_for_pdf`（line 87）— 调本地 `mineru` CLI

**关键设计**：
- 内部 helper `_load_layout_json`（pdf_layout.py:356）支持读已有缓存的 content_list.json，本地跑完后云 fallback 不会重复触发
- 本地批跑 6 路 GPU 并发（A100 单卡满载约 72GB 显存）
- 输出 `<doc_id>/auto/<uuid>_content_list.json` 是后续所有 stage 的总输入

**LLM 调用**：无（MinerU 内部用了视觉编码器，但这是 MinerU 自己的事，不算我们的 LLM stage）

**V2 改动**：无（MinerU 已经是本地的）

---

### Stage 0.5 — 元数据抽取 + IPC 闸门 ⭐

**职责**：从 PDF 首页文本抽取 title / IPC / applicant / abstract / filing_date，并跑 V1.2.5 的 4 信号 `is_coating_patent` 分类器。**非涂料专利在此 return，省 stage 4-7 的 LLM 钱**。

**主代码**：
- `src/coating_kg/pipeline/patent_metadata_extractor.py`（约 360 行）
  - line 48 `_PROMPT`（inline prompt，让 LLM 输出严格 JSON）
  - line 184 `_ANTI_COATING_KEYWORDS` 黑名单（CO2 / amine / detergent 等 13 类）
  - line 196 `is_coating_patent()` 4 信号分类器（anti-keyword → primary IPC → title kw → secondary IPC + abstract → defer）
  - line 271 `extract_patent_metadata()` 主入口
- `src/coating_kg/cli.py:64-108` 早期闸门接线点

**关键设计**：
- 早期闸门"开门通过"是默认（`meta.get("is_coating_patent", True)`）— 容错优先
- 抽取失败时软退化（`except: warn 不 raise`）— 元数据失败不 kill 整批
- patent_meta.json 在闸门判断前就落盘 — 留证据可 grep

**LLM 调用**：DashScope qwen-plus，**每 PDF 一次**（首页 ~2K tokens 输入，~200 tokens 输出 JSON），约 ¥0.01/PDF

**V2 改动**：
- 改 `pipeline/patent_metadata_extractor.py:301` 的 `OpenAI(api_key=..., base_url=...)`
- base_url 从 `dashscope.aliyuncs.com/...` → `http://localhost:8000/v1`
- model 从 `qwen-plus` → `Qwen3.6-27B-AWQ-INT4`
- 关 thinking 模式：`extra_body={"chat_template_kwargs": {"enable_thinking": False}}`

---

### Stage 9.5 — passage_extractor（Layer 1）🔜 V2.0.5 待写

**职责**：从所有段落（不只 Examples 章节）NER 出实体共现，作为 Layer 1 PassageHyperedge，**为 V2 KG retrieval 提供"无定量事实时"的 fallback**。

**主代码**：
- `src/coating_kg/pipeline/passage_extractor.py`（**单次 per-doc LLM 调用 + 反扫各段落**）
- `scripts/run_passage_extractor.py` 批量驱动

**设计 — single-pass per-doc LLM**：

| 步骤 | 干什么 | 用什么 | 成本 |
|------|--------|--------|------|
| 1 | 整篇专利全文 markdown + 已有 canonical_ids 一起喂 LLM | DashScope qwen-plus（V2 切本地 vLLM Qwen3.6-27B-AWQ）| 1 次/PDF，~10s，~¥0.05 |
| 2 | LLM 输出 doc 级 entity-phrase mapping `[(phrase, canonical_id, confidence), ...]` | — | 0 |
| 3 | 对每个 passage 用 phrase 字符串扫描，命中即标 entity | 纯字符串匹配 | 0 |
| 4 | 段落级 BGE-M3 embedding（可选，retrieval 时算也行）| 本地 BGE-M3（~2GB 显存） | 算力 |

**为什么不再分 Tier A / Tier B / Tier C**：

V2.0.5 实测（10 篇 BASF 专利 demo）发现：
- 旧 Tier A（aho-corasick 静态字典 per-passage 扫）抽 **101 entities**
- 旧 Tier B（per-passage qwen-plus）抽 **405 entities** — 是 Tier A 的 4×，且实证**完全覆盖 Tier A 的所有命中**
- LLM 看全文上下文比看单段更准：能区分 "polyurethane" 在某段是 resin 还是 binder
- per-passage 调用 119 次/doc → ¥2.4/doc / 10 min/doc，per-doc 调用 1 次/doc → ¥0.05/doc / 10s/doc（**48× 便宜，60× 快**）
- 结论：static dict 没有独立价值，全部交给 LLM。

**输出**：`data/passages/<doc_id>/passages.json`，每条 PassageHyperedge 含：

```json
{
  "passage_id": "WO2026057741A1_p18_b237",
  "doc_id": "WO2026057741A1",          // ← 跟 Layer 2 的 fact 共享同一个 doc_id
  "page": 18,                            // 物理定位
  "section_type": "DESCRIPTION",         // DESCRIPTION / CLAIMS / EXAMPLES / ABSTRACT / ...
  "text_excerpt": "...",                 // 段落原文
  "entities": [                          // LLM 抽出 + phrase 反扫命中的 canonical_id list
    "MAT_silane_coupling_agent",
    "MAT_waterborne_polyurethane_dispersion",
    "APP_automotive_oem_clearcoat"
  ],
  "entity_phrases": {                    // ★ LLM 抽出的 surface phrase → canonical_id 映射
    "silane coupling agent": "MAT_silane_coupling_agent",
    "WPUD": "MAT_waterborne_polyurethane_dispersion",
    "OEM clearcoat": "APP_automotive_oem_clearcoat"
  },
  "confidence": 0.92,                    // ★ rerank 时用（per-passage 平均 LLM confidence）
  "embedding_id": "embed_..."            // 段落向量在 PG pgvector 表的 ID（可选，V2 计算）
}
```

**注意**：相对旧设计已删 `entities_provenance` / `source_tier` / `near_units` 三个字段（理由见下）。

**LLM 调用**：DashScope qwen-plus 一次调用 / PDF
- 输入：全文 markdown（~30K tokens for 大专利） + 现有 canonical_ids list（~3K tokens）+ prompt（~500 tokens）
- 输出：JSON list of `{"phrase": str, "canonical_id": str, "confidence": float}`
- qwen-plus 128K context 完全够用
- V2 切到本地 vLLM Qwen3.6-27B-AWQ（128K context 也够，模型本身原生支持长上下文）

**关键决策**：

1. **保留全部段落（不只 Examples）**：因为 Layer 1 是"无 fact 时 fallback"，Examples 之外的 description / claims 含大量未表格化的 hint，retrieval 端要能命中。

2. **跨层 cross-reference 是天然的**：Layer 1 PassageHyperedge 跟 Layer 2 FactHyperedge 共享 `doc_id` + 共享 ontology 节点（MAT_*/PROP_*/...）。retrieval 时按 `doc_id` GROUP BY 就能把同一篇专利的 fact + passage 聚合显示。

3. **为什么从 stage 1.5 改到 stage 9.5**：原本设计放在 per-PDF 链路（跟 stage 0.5 / 2 并列），但 passage_extractor 实际上不依赖 stage 2-8 的产出（只读 content_list 全文），跟 stage 9 / 10 一样属于"跨语料的 indexing 性步骤"。改 9.5 后 per-PDF 链路更线性，stage 10 (KG load) 自然依赖 9.5 + 9。

4. **取消 `near_units` 字段**：原设计想用物理页距（±10 页内的 unit）做"邻近 Layer 2 提示"，实际发现物理邻近 ≠ 语义相关（同章节里 30+ unit 全部命中，retrieval 时仍然要按 entity overlap 重新筛）。改为 retrieval 时按 `doc_id` JOIN 计算 entity overlap 排序更精准。

5. **取消 Tier A/B/C 分档 + `entities_provenance` + `source_tier` 字段**：V2.0.5 实测 Tier B（per-passage LLM）完全覆盖 Tier A（aho-corasick），static dict 失去独立价值。Tier C（embedding）作为 retrieval-time 计算更灵活，无需在 indexing 时落盘 provenance。简化为单一 per-doc LLM 流程：
   - 性能：60× 快（10s vs 10min）、48× 便宜（¥0.05 vs ¥2.4）
   - 准确度：LLM 看全文上下文 ≥ 看单段（实测案例：长链脂肪酸 alkyl acrylate 的碳数标注）
   - 可解释性靠 `entity_phrases` 字段（直接看 LLM 抽出的原文 surface phrase）

6. **触发：全篇都跑，不再有"长段才触发 Tier B"逻辑**。每篇 PDF 固定一次 LLM 调用，触发逻辑被消除。

**预算**（348 篇 BASF 全跑）：
- per-doc LLM ¥0.05 × 348 = **¥17**
- 时间 ~10s × 348 / 并发 8 = **~7 分钟**（云 API 8 并发）/ 本地 vLLM 32 并发可压到 **~2 分钟**

---

---

### Stage 2 — unit 切分

**职责**：从 content_list 里挑出 type=image / table 且 page_idx 落在 Examples 章节内的 block，构造 `FigureTableUnit` 对象列表。

**主代码**：
- `src/coating_kg/pipeline/unit_extractor.py:iter_figure_table_units`（line 46）
- V1.2.5 加的 `_extract_bbox` 辅助函数
- `src/coating_kg/pipeline/section_split.py:split_examples_section` — Examples 章节定位
- `src/coating_kg/pipeline/paragraph_extractor.py:examples_page_range`

**关键参数**：`examples_only=True`（V1.2.5 设计：Layer 2 fact 只来自 Examples 表格）

**LLM 调用**：无（纯规则）

**V2 改动**：无

---

### Stage 3 — unit 物化

**职责**：把 in-memory 的 FigureTableUnit 对象写到磁盘 — `data/units/<unit_id>/` 下创建 `meta.json` + `caption.txt` + `image.png` 或 `table.html`。

**主代码**：`src/coating_kg/pipeline/unit_materializer.py:materialize_unit`（line 43）

**关键设计**：失败 propagate up（无 try/except），物化是基础设施失败应 fail-fast。

**LLM 调用**：无（磁盘 IO）

**V2 改动**：无

---

### Stage 4 — VLM 看图看表

**职责**：每个 unit 调 VLM/LLM 生成自然语言描述 + 识别已知实体 + 分类 subtype。

**主代码**：`src/coating_kg/pipeline/vlm_describe.py:QwenVLClient`
- `describe_figure(image_path, candidate_ids)` — figure 走 VLM
- `describe_table(html, caption, candidate_ids)` — table 走文本 LLM（已 OCR 成 HTML 了）

**输出**：`data/units/<unit_id>/vlm_description.json` 含 `{description, identified_entities, subtype}`

**LLM 调用**：
- figure: DashScope **qwen-vl-plus / qwen-vl-max**（每 figure ¥0.05）
- table: DashScope **qwen-plus**（每 table ¥0.005）

**V2 改动**：
- 切到本地 vLLM Qwen3.6-27B-AWQ（同一个实例同时处理 figure VLM 请求和 table 文本请求 — 因为 27B 是 VLM）
- 改 `pipeline/vlm_describe.py` 的 client base_url + model name
- 关 thinking 模式

---

### Stage 5 — 实体兜底打标

**职责**：对 VLM 没识别出的已知 canonical，用 aho-corasick 字符串/别名匹配补一道。

**主代码**：`src/coating_kg/pipeline/entity_tagger.py:tag_entities`

**关键设计**：
- 只在 `conn is not None`（生产模式）才跑，因为需要查 DB 的 alias 表
- PoC 模式（`--skip-db`）整段 skip
- 跟 stage 9.5 的 Tier A 是同一类机制（aho-corasick 多模式匹配），只是作用对象是 unit 级而不是段落级

**LLM 调用**：无

**V2 改动**：无

---

### Stage 6 — 段落匹配 (PoC-2)

**职责**：为每个 unit 选 top-10 个最相关的 Examples 段落（提供 stage 7 fact_extractor 的上下文）。

**主代码**：
- `src/coating_kg/pipeline/paragraph_matcher.py:ParagraphMatcher.match`
- `src/coating_kg/pipeline/paragraph_extractor.py:gather_examples_paragraphs`

**关键参数**：`top_k=10` 硬编码（PoC-2 测出的甜蜜点）+ `region_label=unit.region_id` 提示 LLM 找哪张表/图

**输出**：`data/units/<unit_id>/matched_paragraphs.json`（含 top_k 段落 + 评分 + 选择理由）

**LLM 调用**：DashScope qwen-plus，每 unit 一次

**V2 改动**：
- 改 `pipeline/paragraph_matcher.py` 的 client base_url + model name
- 关 thinking 模式（top_k 选段不需要 reasoning chain）

---

### Stage 7 — 事实抽取 (PoC-3) ⭐最贵

**职责**：从 unit（表 HTML + caption + matched paragraphs）抽出结构化 FactHyperedge JSON list（19+1 字段：fact_id / application / property / evidence_pointer / result_value / process / test_method / etc.）。

**主代码**：
- `src/coating_kg/pipeline/fact_extractor.py:FactExtractor.extract`（约 600 行，最复杂）
- `prompts/fact_extract.txt`（V1.2.5 改了 rule 5b/5c empty cell + rule 8 example_id literal + rule 9 sub_type closed enum + EXAMPLE D rowspan）

**V1.2.5 改动**：
- evidence_pointer.unit_id 回填（task #1）
- evidence_pointer.bbox 透传（task #2）
- empty-cell guard（task #3）
- example_id Level 0 trust LLM literal（V1.2.4）

**输出**：
- `data/units/<unit_id>/facts.json`（主产出，stage 9 build CSV 用）
- `proposed_canonicals.json`（V1.2.3 propose-and-curate，待人审）
- `coverage.json`（V1.2.5 Tier-0 Fix 3，让 stage 9 区分"真没有"vs"抽取截断"）

**LLM 调用**：DashScope qwen-plus，**每 unit 一次**（输入 ~3K tokens table HTML + matched paragraphs，输出 ~500-1500 tokens JSON facts），约 ¥0.3-0.5/unit

**V2 改动**：
- 改 `pipeline/fact_extractor.py` 的 client base_url + model name
- 加 `response_format={"type": "json_object"}` 强制 JSON
- 用 vLLM `--guided-decoding-backend outlines` 在采样阶段禁止生成非法 JSON
- 加 retry on JSON parse error
- 关 thinking 模式（fact 抽取是结构化任务，不需要 reasoning）

---

### Stage 8 — 极性 + comparison_group + (可选 DB)

**职责**：
- 8a 极性后处理：LLM 不确定（"unknown"）时用规则推 inventive/comparative
- 8b comparison_group 绑定：把同一张表里相关 fact 归到同一个 group_id
- 8c（可选）DB 写入：bulk_insert_facts 进 PG

**主代码**：
- `src/coating_kg/pipeline/polarity.py:classify_polarity`（纯规则，看 row label）
- `src/coating_kg/pipeline/comparison_group.py:resolve_comparison_group`
- `src/coating_kg/db/insert.py:bulk_insert_facts`（DB 模式）
- 三个调用点都在 `cli.py:232-263` 的同一个 try 块内（per-unit 事务）

**关键设计**：
- 规则只在 LLM 不确定时补位（LLM 上下文更全，优先）
- facts.json 总是落盘（不论 DB 有无），让 PoC 模式 build CSV 能跑

**LLM 调用**：无（全规则）

**V2 改动**：无；DB 部分等 V2 KG 上线启用

---

### Stage 9 — 宽表 CSV

**职责**：把所有 PDF 的 facts.json 聚合成一行一个 Example 的 wide CSV，每个 Material/Property sub_type 一列。

**主代码**：`scripts/build_coatings_csv.py`（约 790 行）
- line 81 `is_coating_patent()` — 局部函数，用 IPC 前缀做老式过滤（V2 会被 stage 0.5 的 4 信号闸门替代）
- line 397 `build_row()` — 单个 (doc_id, example_id) 行构建
- line 689 `main()` — 主流程

**V1.2.5 改动**：
- `extraction_confidence_min/mean` 列（line 553）
- qualitative result_value_text fallback（line 557）

**LLM 调用**：无

**V2 改动**：无

---

### Stage 10 — KG 装载 🔜 V2.x 待写

**职责**：把所有 facts.json + passages.json + ontology canonical_ids 灌进 PostgreSQL + pgvector，建好 5 张表 + HNSW 索引。

**主代码（待写）**：`scripts/load_facts_to_pg.py` 或 `pipeline/kg_loader.py`

**5 张 PG 表**：
1. `node` — 实体节点（id / canonical_id / type / label / aliases / **embedding vector(1024)**）
2. `fact_hyperedge` — Layer 2 边（fact_id / doc_id / example_id / result_value / evidence_pointer JSONB）
3. `hyperedge_node` — Layer 2 边-节点多对多链接（hyperedge_id / node_id / role）
4. `passage_hyperedge` — Layer 1 边（passage_id / doc_id / page / text_excerpt / **embedding**）
5. `passage_hyperedge_node` — Layer 1 边-节点链接

**索引**：
- `node`: BTree(type, canonical_id) + **HNSW(embedding)**
- `passage_hyperedge`: HNSW(embedding) + GIN(entities)
- `fact_hyperedge`: BTree(doc_id, example_id) + GIN(evidence_pointer)

**外部依赖**：
- PostgreSQL 16 + pgvector extension
- 本地 BGE-M3 嵌入模型（~2 GB 显存，跟 vLLM 共驻 A100）

**LLM 调用**：无；本地 BGE-M3 算 embedding（不是 LLM 调用，是 embedding 模型）

**V2 改动**：本身就是 V2 新增

---

## 3. AWQ 在 A100 80G 单卡部署详解

### 3.1 AWQ 是什么

**AWQ = Activation-aware Weight Quantization**（激活感知权重量化，MIT Han Lab 2023 提出）。

**位宽**：W4A16 — 权重 4 bit，激活 16 bit（推理时）。
**核心创新**：根据 activation magnitude 智能保护 1% 重要权重，其余 99% 4-bit 量化。质量比同 4-bit 的 GPTQ 高 1-2 个 MMLU 点。

### 3.2 为什么 AWQ 能塞进 A100 80G

以 Qwen3.6-27B-AWQ-INT4 为例：

```
模型权重 (AWQ INT4):
  27B params × 0.5 bytes/param = 13.5 GB
  + 量化元数据（scales / zeros）≈ 5.5 GB
  ──────────────────────────────────────
  实际权重落盘                  ~19 GB （模型卡确认）

A100 80G 余量 = 80 - 19 = 61 GB → 极宽
```

对比 BF16 版本：27B × 2 bytes = 54 GB，加 KV cache + 框架 overhead 后 A100 80G 紧。**AWQ 把 weights 压缩 4x，给 KV cache 和并发留出大量空间**。

### 3.3 显存占用如何计算

总占用公式：
```
VRAM_total = VRAM_weights + VRAM_kv_cache + VRAM_activation + VRAM_framework
```

#### 3.3.1 KV cache 计算（Qwen3.6-27B 特殊架构）

Qwen3.6-27B 是 **hybrid attention** 模型（48 层 DeltaNet 线性注意力 + 16 层门控 softmax 注意力）。**只有 16 层 softmax 注意力占 KV cache**：

```
KV per token = 2 (K+V) × n_softmax_layers × n_kv_heads × head_dim × dtype_bytes
             = 2 × 16 × 4 × 256 × 2
             = 65,536 bytes
             = 64 KB / token
```

DeltaNet 部分用 O(d²) 状态向量，不随 sequence 长度增长，可以忽略不计。

**对比常规 dense 模型**：
- Qwen2.5-32B: 64 layers × 8 KV heads × 128 head_dim × 2 × 2 = 256 KB/token（**4 倍于 27B**）
- Qwen2.5-72B: 80 × 8 × 128 × 2 × 2 = 320 KB/token（**5 倍于 27B**）

Qwen3.6-27B 的 KV cache 优势是架构红利。

#### 3.3.2 实战配置矩阵（A100 80G）

```
固定占用:
  权重         19 GB
  视觉编码器   3 GB  （27B 是 VLM，含 ViT + projector）
  vLLM overhead 4 GB
  ─────────────────
  小计         26 GB
  剩余 KV 池   54 GB
```

| max_model_len | max_num_seqs | KV 池需求 | 总占用 | 状态 |
|---------------|--------------|-----------|--------|------|
| 8 KB | 16 | 8 GB | 34 GB | ✅ 极宽 |
| 8 KB | 32 | 16 GB | 42 GB | ✅ 推荐 |
| 16 KB | 32 | 32 GB | 58 GB | ✅ 余量大 |
| 32 KB | 32 | 64 GB | 90 GB | ❌ OOM |
| 16 KB | 64 | 64 GB | 90 GB | ❌ OOM |

我们 fact_extractor 输入 ~3K + 输出 ~1K = 4K/请求，**推荐配置 max_model_len=8K, max_num_seqs=32**，总占用 ~42 GB，A100 80G 余 38 GB 安全 margin。

#### 3.3.3 vLLM 启动命令

```bash
vllm serve ~/models/Qwen3.6-27B-AWQ-INT4 \
  --port 8000 \
  --max-model-len 8192 \
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.7 \
  --enable-prefix-caching \
  --guided-decoding-backend outlines
```

`--gpu-memory-utilization 0.7` 留 30% buffer 应对 KV cache 突发。`--enable-prefix-caching` 让相同 prompt 前缀复用缓存（fact_extractor 的 system prompt 完全相同，加速 5x）。`--guided-decoding-backend outlines` 在采样阶段强制 JSON 合法。

### 3.4 精度损失分析

#### 3.4.1 量化精度损失的来源

1. **权重压缩**：FP16 (16 bit) → INT4 (4 bit)，每个权重值的有效精度从 65,536 个等级降到 16 个等级
2. **舍入误差**：原本连续的 FP16 值映射到离散的 INT4 等级
3. **激活溢出**：某些 outlier 权重对应的激活值会被夹断

#### 3.4.2 AWQ 如何减少损失

朴素 INT4 量化：所有权重一视同仁。
**AWQ 算法**：

```
1. 用 calibration dataset 跑 forward，记录每个 channel 的 activation magnitude |a|
2. 找出 |a| 最大的 1% channel（这些是"显著权重"）
3. 对显著 channel 用 per-channel scaling，激活值缩小 s 倍后量化（s 由优化得到）
4. 推理时把对应权重×s，激活×(1/s)，数学等价但量化精度高
```

效果：1% 显著权重保留接近 FP16 精度，99% 普通权重 INT4。最终模型平均 4-bit 但对最关键的 1% 没损失。

#### 3.4.3 精度损失定量

学术 benchmark（Qwen2.5 / Llama-3 系列实测）：

| 量化方案 | 模型 | 任务 | 损失（vs FP16） |
|----------|------|------|----------------|
| GPTQ INT4 | Llama-3-70B | MMLU | -2.1 pt |
| **AWQ INT4** | **Llama-3-70B** | **MMLU** | **-1.0 pt** |
| AWQ INT4 | Qwen2.5-72B | MMLU | -0.8 pt |
| AWQ INT4 | Qwen2.5-32B | MMLU | -1.2 pt |
| AWQ INT3 | Llama-3-70B | MMLU | -5.5 pt |
| BF16 (baseline) | — | — | 0 |

**对结构化抽取任务（我们 fact_extractor）影响通常更小**：
- 任务模式固定，模型只需"遵循 prompt 格式"，不需要复杂推理
- AWQ 4-bit 的 1-2% 损失主要影响"长链推理"，对"按 prompt 输出 JSON"几乎无影响
- 实际影响估计 < 1% precision drop

#### 3.4.4 我们实际可能损失多少

**端到端任务（348 篇涂料专利 fact 抽取）**：

```
潜在损失来源:
  AWQ 量化              ~1-2%  fact 数量 / result_value 一致性
  关 thinking 模式      ~3-5%  对边缘 case（多义性 row）影响略大
  社区量化 calibration  ~0-2%  cyankiwi 的 calibration set 是"STEM + Agentic"，对涂料 domain 适配度未知
  ─────────────────────────────
  合计估计              5-9%   总质量下降
```

**这个数字是估计上限，实际可能更小**。必须用 11 篇 ground truth 跑回归实测：
- 通过率 ≥ 90%（即损失 < 10%）→ 可用
- 通过率 80-90% → 调 prompt 再回归
- 通过率 < 80% → 切回云 qwen-plus 或换更大模型

#### 3.4.5 速度对比（实测估计）

| 配置 | 单流 tok/s | 32 并发 aggregate tok/s |
|------|-----------|------------------------|
| Qwen3.6-27B BF16 | ~15 | ~200 |
| **Qwen3.6-27B AWQ + outlines + prefix-caching** | **~50** | **~1000** |
| Qwen3.6-27B AWQ + MTP | ~80 | ~1500 |
| Qwen2.5-32B BF16 | ~15 | OOM (装不下 32 并发) |
| Qwen2.5-72B AWQ | ~25 | ~280 |

**AWQ 不只省显存，还快 2-3 倍**（因为 weights load 减少，memory bandwidth 是 LLM 推理瓶颈）。

---

## 4. 端到端示例：WO2026052438A1 走完 stage 0→10（含 9.5）

挑一篇真实 BASF 2026 PCT 专利 `WO2026052438A1`（"TWO-COMPONENT COATING COMPOSITION"）走完整流程，每 stage 列出**输入 / 处理 / 产出**。

### 输入

```
./data/pdf/example.pdf
```

42 页 PDF，含：
- 摘要、说明、claims（page 1-22）
- Examples 章节（page 23-41）含 6 张表
- 1 张工艺流程图

### Stage 0 — 编排

**输入**：上面的 PDF 路径
**处理**：`python -m coating_kg ingest WO2026052438A1.pdf --skip-db` 启动单 PDF 流程
**产出**：日志 `ingest start: WO2026052438A1.pdf (dry_run=False, skip_db=True)`

### Stage 1 — MinerU 版面解析

**输入**：PDF
**处理**：本地 mineru CLI 6 路并发其中 1 路跑这篇
**产出**：
```
data/mineru_output/WO2026052438A1/
└── auto/
    ├── 60fd22cc-..._content_list.json    （42 页 × 平均 30 block ≈ 1260 block）
    ├── 60fd22cc-...md                     （markdown 全文）
    ├── 60fd22cc-..._layout.json
    └── images/
        ├── a3f8b2.jpg                      （工艺流程图切片）
        └── ... 5 张表的截图也在
```

### Stage 0.5 — 元数据 + IPC 闸门

**输入**：content_list 第 1 页 text blocks（前 ~2K tokens）
**处理**：DashScope qwen-plus 调用一次（V2 后切本地 vLLM Qwen3.6-27B-AWQ）
**产出**：
```json
data/patents/WO2026052438A1__patent_meta.json
{
  "patent_id": "WO2026052438A1",
  "title": "TWO-COMPONENT COATING COMPOSITION",
  "applicant": "BASF Coatings GmbH",
  "filing_date": "2025-09-15",
  "publication_number": "WO2026052438A1",
  "ipc_codes": ["C09D 175/14", "C08G 18/62", "C08G 18/73", "C08K 5/10", "C08L 75/14"],
  "abstract": "A two-component coating composition comprising ...",
  "is_coating_patent": true,                 ← ★ 早期闸门通过（C09D primary IPC）
  "is_coating_reason": "primary IPC: C09D 175/14",
  "section_split_mode": "found",             ← Examples 章节定位成功
  "examples_page_range": [23, 41]
}
```

cli.py 看到 `is_coating_patent=true`，**继续走 stage 2-8**。

### Stage 9.5 — passage_extractor（V2 后才有）

**输入**：content_list 全文 markdown（42 页 ~95K 字符 / ~30K tokens）+ ontology 已有 canonical_ids list
**处理**：
- 一次 qwen-plus 调用（输入 ~33K tokens / 输出 ~2K tokens JSON）
- LLM 返回 doc 级 entity-phrase mapping，约 80 个 (phrase, canonical_id, confidence)：
  - 已有 canonical 命中：`polyurethane`, `clearcoat`, `isocyanate`, `Tin catalyst`, ...
  - 新 propose：`MAT_BASF_X-Tend_2400`（trade name，落 propose-and-curate sidecar）
- 对 200 段 type=text block 各自做 phrase 字符串扫描，命中即标 entity
- 失败回退：JSON parse 失败（罕见）则 retry 一次；仍失败则 skip 该 PDF 落 warn 日志，不阻塞 pipeline

**LLM 调用成本**：1 次 qwen-plus / ~10s / ¥0.05

**产出**：
```json
data/passages/WO2026052438A1/passages.json
[
  {
    "passage_id": "WO2026052438A1_p23_b417",
    "doc_id": "WO2026052438A1",
    "page": 23,
    "section_type": "EXAMPLES",
    "text_excerpt": "Example A1B1 was prepared by mixing component A1 (polyurethane resin)...",
    "entities": ["MAT_polyurethane_resin", "MAT_BASF_X-Tend_2400", "PROC_spray_apply"],
    "entity_phrases": {
      "polyurethane resin": "MAT_polyurethane_resin",
      "X-Tend 2400": "MAT_BASF_X-Tend_2400",
      "applied by spraying": "PROC_spray_apply"
    },
    "confidence": 0.92,
    "embedding_id": null     // V2 retrieval 时再算 BGE-M3
  },
  ... 共约 200 条
]

data/passages/WO2026052438A1/proposed_canonicals.json
[
  {
    "proposed_id": "MAT_BASF_X-Tend_2400",
    "phrase": "X-Tend 2400",
    "first_passage_id": "WO2026052438A1_p23_b417",
    "evidence_excerpt": "...component A2 (X-Tend 2400, BASF) was added at 5 wt%..."
  }
]
```

### Stage 2 — unit 切分

**输入**：layout in-memory（含 1260 个 block）
**处理**：`iter_figure_table_units(layout, examples_only=True)` 挑出 page 23-41 内的 table/image block
**产出**：6 个 `FigureTableUnit` in-memory 对象：
- `U_WO2026052438A1_p25_b330` (Table 1，组成表)
- `U_WO2026052438A1_p25_b332` (Table 2，物性表)
- `U_WO2026052438A1_p26_b343` (Table 3)
- `U_WO2026052438A1_p26_b346` (Table 4)
- `U_WO2026052438A1_p27_b357` (Table 5)
- `U_WO2026052438A1_p27_b359` (Table 6)

### Stage 3 — unit 物化

**输入**：6 个 unit 对象 + content_list 中的 image / table HTML 引用
**处理**：每 unit 创建目录 + 写 4 个文件
**产出**：
```
data/units/U_WO2026052438A1_p25_b330/
├── meta.json                  （unit 元数据：bbox, page, region_id, etc.）
├── caption.txt                （"Table 1: Composition of clearcoat A1B1..."）
├── table.html                 （MinerU OCR 出来的表 HTML）
└── （没 image.png，因为是 table）
... 6 个 unit 目录
```

### Stage 4 — VLM 看图看表

**输入**：6 个 unit 的 `table.html` + caption + candidate_ids（PoC 模式空 list）
**处理**：每 unit 调一次 DashScope qwen-plus（table 走文本 LLM，因为 HTML 已结构化）
**产出**（每 unit 一份）：
```json
data/units/U_WO2026052438A1_p25_b330/vlm_description.json
{
  "description": "Table shows composition (in wt%) of inventive clearcoat A1B1 and three comparative formulations (CC-A1B1 inv, CC-A2 comp, CC-B1 comp, CC-A1+B1 comp). Components include polyurethane resin (component A), polyisocyanate (component B), additives Hydropalat WE3650, Tego Wet 270, EFKA SL3035.",
  "identified_entities": ["MAT_COMPONENT_A1", "MAT_Hydropalat_WE3650", "MAT_EFKA_SL3035", "MAT_Tego_Wet_270"],
  "subtype": "Composition"
}
```

### Stage 5 — 实体兜底打标

**输入**：VLM description + caption + table HTML + DB alias 表
**处理**：PoC 模式 (`--skip-db`) **整段 skip**
**产出**：无（生产模式才有）

### Stage 6 — 段落匹配 (PoC-2)

**输入**：unit 的 VLM description + Examples 段落 list（约 200 段，stage 9.5 输出）
**处理**：DashScope qwen-plus 一次调用，从 200 段里选 top-10 最相关
**产出**（每 unit 一份）：
```json
data/units/U_WO2026052438A1_p25_b330/matched_paragraphs.json
{
  "top_k": 10,
  "matches": [
    {
      "para_id": "p23_b417",
      "page": 23,
      "text": "Example A1B1 was prepared by mixing component A1 (polyurethane resin) and component B1 (HDI trimer) in a ratio corresponding to NCO/OH index 1.35, after 30 min induction time...",
      "score": 0.92,
      "reason": "段落明确给出 A1B1 的配方组成 + 工艺条件，跟 Table 1 强相关"
    },
    ... 9 个更多
  ]
}
```

### Stage 7 — 事实抽取 (PoC-3) ⭐最贵

**输入**：unit + matched paragraphs + VLM description + ontology
**处理**：DashScope qwen-plus 一次调用，输入 ~3K tokens，输出 ~1500 tokens 严格 JSON
**产出**（每 unit 一份）：
```json
data/units/U_WO2026052438A1_p25_b330/facts.json
{
  "facts": [
    {
      "fact_id": "F_U_WO2026052438A1_p25_b330_001",
      "application": "APP_automotive_oem_clearcoat",
      "property": "PROP_composition_weight_percent",
      "source_section_type": "TABLE",
      "polarity_hint": "positive",
      "evidence_pointer": {
        "doc_id": "WO2026052438A1",
        "page": 25,
        "region_type": "TABLE",
        "region_id": "Table 1",
        "row": "CC-A1B1 (inventive)",
        "column": "Clearcoat code",
        "cell": "CC-A1B1 (inventive)",
        "unit_id": "U_WO2026052438A1_p25_b330",   ← V1.2.5 task #1
        "bbox": [120, 340, 480, 360]               ← V1.2.5 task #2
      },
      "doc_id": "WO2026052438A1",
      "resin_system": "MAT_COMPONENT_A1",
      "additives": ["MAT_Hydropalat_WE3650", "MAT_EFKA_SL3035"],
      "process": [
        {"step": "PROC_spray_apply", "condition": "after 30 min induction time"},
        {"step": "PROC_ambient_cure", "condition": "23°C, 50% RH, 7 days"},
        {"step": "PROC_thermal_cure", "condition": "80°C, 30 min"}
      ],
      "test_condition": {
        "film_thickness": "45–55 μm",
        "isocyanate_index": 135
      },
      "result_value": 100.0,
      "result_value_text": "CC-A1B1 (inventive)",
      "comparison_group": "F_001",
      "extraction_confidence": 0.95,
      "example_id": "A1B1"
    },
    ... 共约 30 facts
  ]
}

data/units/U_WO2026052438A1_p25_b330/coverage.json
{
  "total_rows": 8,
  "rows_extracted": 8,
  "rows_skipped": [],
  "truncated": false,
  "completeness_score": 1.0
}

data/units/U_WO2026052438A1_p25_b330/proposed_canonicals.json
{
  "proposed_canonicals": [
    {
      "proposed_id": "MAT_BASF_X-Tend_2400",
      "sub_type": "Resin / binder",
      "evidence": "Mentioned in Table 1 as primary resin component",
      "first_fact_id": "F_..._001"
    }
  ]
}
```

6 个 unit 共抽出约 **42 个 facts**（实际数据测过）。

### Stage 8 — 极性 + comparison_group + (DB)

**输入**：42 个 facts in-memory
**处理**：
- 8a 极性补位：纯规则推 row label，把 LLM 输出 "unknown" 的 8 条改成 "negative"（comparative）
- 8b comparison_group 绑定：6 张表内 fact 按 (region_id, property, application) 分到 8 个 group（F_001 ~ F_008）
- 8c DB 写入：PoC 模式跳过
**产出**：facts in-memory 更新（写回 facts.json）

### Stage 9 — 宽表 CSV

**输入**：所有 PDF 的 facts.json + patent_meta.json（含 WO2026052438A1 的 6 个 facts.json）
**处理**：聚合到一行一个 (doc_id, example_id) 的宽表
**产出**（节选 WO2026052438A1 部分）：
```csv
output/coatings_wide.csv
patent_id,example_id,polarity,Material:Resin / binder,Material:Crosslinker,Property:Mechanical,...,extraction_confidence_min,extraction_confidence_mean,applicant,filing_date
WO2026052438A1,A1B1,positive,"MAT_COMPONENT_A1@7:100 wt%","MAT_HDI_trimer@4:NCO/OH=1.35","adhesion_cross_cut@3:5B; pendulum_hardness@5:165 s",...,0.85,0.93,BASF Coatings GmbH,2025-09-15
WO2026052438A1,A1B2,positive,"MAT_COMPONENT_A1@7:100 wt%; MAT_COMPONENT_A2@6:50 wt%","MAT_HDI_trimer@4:NCO/OH=1.35","adhesion_cross_cut@3:5B; pendulum_hardness@5:172 s",...,0.88,0.94,BASF Coatings GmbH,2025-09-15
WO2026052438A1,CC-A1+B1,negative,"MAT_COMPONENT_A1@7:100 wt%","MAT_HDI_trimer@4:NCO/OH=1.35","adhesion_cross_cut@3:3B; pendulum_hardness@5:140 s",...,0.83,0.91,BASF Coatings GmbH,2025-09-15
... 共 8 行（4 inventive + 4 comparative，对应 Examples 章节 8 个 example_id）
```

stage 9 还顺手算了 `extraction_confidence_min=0.83 / mean=0.92` 两列，给 retrieval 端做"信任分级"。

### Stage 10 — KG 装载（V2 待写）

**输入**：
- 全部 PDF 的 `data/units/*/facts.json`（348 篇 × ~30 unit ≈ 10000 个 facts.json）
- 全部 PDF 的 `data/passages/<doc_id>/passages.json`（348 个，每个含 200 段）
- ontology seed canonical_ids（~150 个 MAT_* / ~80 个 PROP_* / ~30 个 APP_* / ...）

**处理**（5 张 PG 表 + pgvector 索引）：
1. `node` 表：把 ontology canonical 灌进去（含 BGE-M3 embedding 1024 维）
2. `fact_hyperedge` 表：每条 fact 一行（fact_id PK，doc_id / example_id / result_value / evidence_pointer JSONB）
3. `hyperedge_node` 表：每条 fact 跟它涉及的 node 的多对多链接（fact_id, node_id, role），role ∈ {application, property, resin_system, additive, process_step, test_method, ...}
4. `passage_hyperedge` 表：每段 passage 一行（passage_id PK，doc_id / page / text_excerpt / embedding 1024 维）
5. `passage_hyperedge_node` 表：passage 跟 node 的多对多链接（passage_id, node_id）

**索引**：
```sql
-- node 表
CREATE INDEX idx_node_type_canonical ON node (type, canonical_id);
CREATE INDEX idx_node_embedding_hnsw ON node USING hnsw (embedding vector_cosine_ops);

-- passage_hyperedge 表
CREATE INDEX idx_passage_embedding_hnsw ON passage_hyperedge USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_passage_entities_gin ON passage_hyperedge USING gin (entities);

-- fact_hyperedge 表
CREATE INDEX idx_fact_doc_example ON fact_hyperedge (doc_id, example_id);
CREATE INDEX idx_fact_evidence_gin ON fact_hyperedge USING gin (evidence_pointer);
```

**产出**：PG 数据库 ready，retrieval API（V2 写）能跑：
- 关键字检索 → GIN(entities) 命中 passage / fact
- 语义检索 → HNSW(embedding) 取 top-k 段 / top-k 节点
- 跨层 → SQL JOIN by doc_id 把 fact + passage 对齐展示
- 结构化过滤 → BTree 索引按 doc_id / example_id / property 切片

**LLM 调用**：无；本地 BGE-M3 算 embedding（passage 200 × 348 ≈ 7 万段，BGE-M3 在 A100 上 ~500 段/秒，约 2.5 分钟跑完所有段落 embedding）

**外部依赖**：
- PostgreSQL 16
- `pgvector` extension（编译装 `CREATE EXTENSION vector;`）
- 本地 BGE-M3 模型（HuggingFace `BAAI/bge-m3`，3.6 GB safetensors）

---

## 5. V1.2.5 → V2.0.5 迁移清单

按优先级排列（先跑通的优先）：

| 优先级 | 改动 | 涉及代码 | 预计工作量 |
|--------|------|---------|-----------|
| P0 | stage 0.5 / 4 / 6 / 7 切本地 vLLM | 4 个 client 的 base_url / model 名 + 关 thinking | 1 天 |
| P0 | vLLM 启动参数固化（`max-model-len 8192`, `max-num-seqs 32`, `--guided-decoding-backend outlines`） | `scripts/start_vllm.sh` | 0.5 天 |
| P1 | Stage 9.5 实装 single per-doc LLM 流程 | `pipeline/passage_extractor.py` 重写、`scripts/run_passage_extractor.py` 跑批 | 1.5 天 |
| P1 | 11 篇 golden set 回归测试 | 跑前后 fact 数 / canonical 命中数对比 | 0.5 天 |
| P2 | Stage 10 KG loader 实装 | `scripts/load_facts_to_pg.py` + 5 张表 schema migration | 2 天 |
| P2 | BGE-M3 跟 vLLM 共驻 A100 + retrieval API skeleton | `scripts/start_bge.sh` + FastAPI | 2 天 |
| P3 | propose-and-curate 人审界面（Streamlit） | `scripts/curate_ui.py` | 2 天 |

**总工作量**：~10 工作日（约 2 周可演示 V2.0.5）。

---

## 6. 端到端时间 / 成本预算

### 6.1 V1.2.5（云 qwen-plus）

| 量级 | 时间 | 成本 |
|------|------|------|
| 单篇 PDF | ~5 min + N×3 min（N=unit 数）| ~¥0.5 |
| 348 篇 BASF 全跑 | ~5 小时（8 并发） | **~¥150-200** |

### 6.2 V2.0.5（本地 vLLM Qwen3.6-27B-AWQ + BGE-M3）

| 量级 | 时间 | 成本 |
|------|------|------|
| 单篇 PDF | ~3 min（本地 32 并发吞吐 ~1000 tok/s）| **¥0**（电费忽略不计） |
| 348 篇 BASF 全跑 | **~2 小时**（A100 单卡跑满）| **¥0** |
| 增量 Stage 9.5 | ~2 分钟（348 PDF × 1 LLM 调用，本地 32 并发）| **¥0** |
| 增量 Stage 10 KG load | ~3 分钟（pg copy + HNSW 建索引）| **¥0** |

**ROI 拐点**：跑过 ~600 篇 PDF 后，云 API 成本就追平 A100 一年的折旧。BASF 一家 348 篇 + 后续多家公司语料 → 本地推理在 6 个月内回本。

---

## 7. 文档版本

| 版本 | 日期 | 主要改动 |
|------|------|---------|
| v1 | 2026-04-22 | 初稿（10 stage） |
| v2 | 2026-05-02 | 加 stage 0.5 IPC 闸门 + V1.2.3 propose-and-curate |
| v3 | 2026-05-06 | stage 1.5 → 9.5 移位 + 删 `near_units` 字段 |
| **v4** | **2026-05-09** | **Stage 9.5 改 single per-doc LLM（删 Tier A/B/C 分档）+ 补全 Stage 10 + 加迁移清单** |
