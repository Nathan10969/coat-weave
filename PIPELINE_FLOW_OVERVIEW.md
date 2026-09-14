# Pipeline 流程梳理

这套 pipeline 的目标不是单纯 OCR，也不是直接让 LLM 总结 PDF，而是把涂料/专利 PDF 转成两类可追溯知识产物：

1. **Layer 2 facts**：从表格和上下文中抽出的结构化事实，例如某配方、材料或应用在某个测试条件下的性能结果。
2. **Layer 1 evidence**：正文段落和图表证据，例如某段话提到了哪些材料/性能/应用，或某张结构图、反应图、SEM 图可以作为检索和证据引用。

整体可以先理解成：

```text
PDF
 -> MinerU 版面解析
 -> 专利 metadata 抽取 / 涂料专利 gate
 -> Examples 章节定位
 -> figure/table unit 抽取
 -> unit 落盘
 -> VLM 图表描述
 -> unit_router 三路分流
 -> 段落匹配
 -> fact 抽取
 -> canonical 解析与质量门控
 -> Layer 1 passages / figures
```

## 1. PDF 解析：`pdf_layout.py`

入口文件：`pipeline/pdf_layout.py`

这一阶段调用 MinerU，把 PDF 解析成结构化版面结果，主要产物是：

```text
content_list.json
layout.json
middle.json
images/
```

这些结果还不是知识，只是把 PDF 拆成 text、table、image 等 block。

当前已有检查：

- MinerU API 是否提交成功。
- 任务是否进入 done/success/completed。
- 是否能找到本地 JSON 输出。

后续建议补的 gate：

- 页数是否异常。
- 首页文字是否过少。
- table/image block 数量是否异常。
- MinerU 返回 stub 时不应继续进入正式抽取。

## 2. 专利 metadata 与涂料过滤：`patent_metadata_extractor.py`

入口文件：`pipeline/patent_metadata_extractor.py`

这一阶段读取 MinerU 首页文字，抽取专利基础信息：

```text
publication_number
publication_date
filing_date
priority_date
application_number
applicant
inventor
title
ipc_codes
abstract
```

然后用 `is_coating_patent()` 判断该文档是否属于涂料相关专利。

当前已有 gate：

```text
is_coating_patent: true/false
is_coating_reason: 判定原因
```

判定信号包括：

- primary coating IPC，例如 `C09D`、`C09K`、`B05D`。
- title/abstract 中的 coating、paint、primer、clearcoat 等关键词。
- 反向关键词，例如 CO2 capture、detergent、surfactant、fertilizer 等。

这一阶段的作用是尽早过滤无关文档，避免浪费下游 VLM/LLM 成本。

## 3. Examples 章节定位：`section_split.py`

入口文件：`pipeline/section_split.py`

这一阶段在全文中定位 Examples / Embodiments / Beispiele / 实施例 等章节。

核心函数：

```text
split_examples_section(text)
extract_examples_text(text)
```

设计意图：

- 专利中真正包含实验、配方和性能数据的地方通常在 Examples。
- 只抽 Examples 内的图表，可以减少噪声。
- 避免 description、claims 中的大量泛化文本污染 fact extraction。

当前风险：

- 如果 Examples 标题识别失败，会 fallback 到 whole-doc。
- whole-doc fallback 虽然保 recall，但会增加噪声和成本。

建议 gate：

```text
Examples 找到 -> pass
Examples 未找到但 whole-doc fallback -> review
全文无有效 text -> fail
```

## 4. Figure/Table unit 抽取：`unit_extractor.py`

入口文件：`pipeline/unit_extractor.py`

这一阶段从 MinerU blocks 中抽出 figure/table unit。

会处理三类 block：

```text
image block -> figure unit
equation block with img_path -> figure unit
table block -> table unit
```

每个 unit 的核心字段：

```text
unit_id
unit_type: figure/table
doc_id
page
region_id: Table 1 / Figure 2 / Scheme 1
caption_footnote_text
image_path
extracted_table_html
bbox
```

当前已有 gate：

- table 没有 `table_body/html/text` 时跳过。
- image path 尽量解析，但解析失败主要是 warning。

建议 gate：

- table HTML 为空 -> fail。
- figure 没有 image 文件 -> review 或 fail。
- bbox 缺失 -> review。
- region label 无法从 caption 解析、只能 fallback -> review。

## 5. Unit 落盘：`unit_materializer.py`

入口文件：`pipeline/unit_materializer.py`

这一阶段把每个 unit 写成独立目录，方便后续调试和重跑。

典型目录结构：

```text
data/units/<unit_id>/
  meta.json
  caption.txt
  table.html
  image.png
```

这一阶段的意义：

- 每个 unit 的输入和中间产物都有留痕。
- 出错时可以单独追踪某张图、某个表。
- 后续 VLM description、paragraph match、fact extraction 的输出可以追加到同一目录。

## 6. VLM 图表描述：`vlm_describe.py`

入口文件：`pipeline/vlm_describe.py`

这一阶段调用 Qwen VL 或文本模型，为 figure/table 生成结构化描述。

figure 路径：

```text
image -> Qwen-VL -> JSON description
```

table 路径：

```text
table_html + caption -> text model -> JSON description
```

典型输出包括：

```json
{
  "description": "...",
  "identified_entities": ["MAT_xxx", "PROP_xxx"],
  "subtype": "scheme",
  "table_subject": "性能对比"
}
```

当前已有 gate：

- OpenAI-compatible client 是否可用。
- API 调用 retry。
- JSON parse 是否成功。

建议 gate：

- 输出 JSON 必须符合 schema。
- `subtype` / `table_subject` 必须属于封闭枚举。
- `identified_entities` 必须可解析或标记为 proposed。
- VLM 失败时可以 fallback，但应该降置信度。

## 7. Unit 三路分流：`unit_router.py`

入口文件：`pipeline/unit_router.py`

这是当前 pipeline 中最关键的加速和降噪阶段之一。

router 根据 unit 类型和 VLM 描述，把 unit 分成三路：

```text
extract_facts
  进入后续 fact extraction。
  主要是性能对比、配方对比、测试条件等表格。

register_layer1
  不抽结构化数值 fact，只登记为 Layer 1 图表证据。
  主要是结构图、反应图、SEM、装置图、普通图表等。

delete
  低信息噪声，软删除到 data/units/_routed_out/。
  例如 logo、装饰图、版本 banner。
```

当前已有 gate：

- VLM 失败时保守进入 `extract_facts`。
- 低信息图进入 `delete`，但使用软删除，可恢复。
- 图类通常进入 `register_layer1`。
- 性能/配方/测试表进入 `extract_facts`。

已补充：

- `RouteDecision.confidence`
- audit CSV 中的 `route_confidence`
- pending figure 可继承 route confidence

建议 gate：

```text
route_confidence >= 0.7 -> pass
route_confidence < 0.7 -> review
delete 永远只 quarantine，不物理删除
```

## 8. 段落候选与匹配：`paragraph_extractor.py` / `paragraph_matcher.py`

入口文件：

```text
pipeline/paragraph_extractor.py
pipeline/paragraph_matcher.py
```

表格里经常只有数值和短标签，而测试条件、材料体系、基材、工艺可能写在正文段落中。

所以这一阶段会：

1. 从 Examples 页面范围中收集正文段落。
2. 去掉 running header、页码、小标题等噪声。
3. 用 LLM 将 unit description 与候选段落匹配。

典型输出：

```json
[
  {
    "para_id": 123,
    "score": 0.82,
    "reason": "...",
    "page": 15,
    "text": "..."
  }
]
```

当前已有信号：

- 每条 matched paragraph 有 `score`。

当前缺口：

- score 还没有真正作为 gate 使用。
- 低分段落仍可能进入 fact extraction prompt，污染上下文。

建议 gate：

```text
max_score >= 0.6 -> pass
0.4 <= max_score < 0.6 -> review
max_score < 0.4 -> 不把段落上下文塞进 fact prompt
```

## 9. Fact 抽取：`fact_extractor.py`

入口文件：`pipeline/fact_extractor.py`

这是 Layer 2 结构化事实的核心阶段。

输入包括：

```text
unit metadata
table_html
caption
vlm_description
matched_paragraphs
canonical ontology blocks
```

输出是 `FactHyperedge` 列表，以及 proposed canonicals 和 coverage 信息。

一个 fact 大致描述：

```text
某 application / material / formulation
在某 substrate / process / test condition 下
对某 property
得到某 result_value
证据来自某 table cell / bbox / unit
```

当前已有 gate：

- JSON parse 失败时尝试 permissive cleanup。
- 空 cell + 空 result value 的 fact 会跳过。
- Pydantic schema validation 失败的 fact 会跳过。
- coverage 会统计 expected data cells 和 extracted facts。

已补充：

```text
ExtractionResult.quality
ExtractionResult.rejected_facts
```

现在会记录：

- `low_coverage`
- `unresolved_required_canonicals`
- `schema_validation_failed`
- `no_accepted_facts`

默认规则：

```text
coverage < 60% -> review
application/property canonical 未解析 -> reject fact
有抽取但 accepted facts = 0 -> fail
```

这是目前最接近“抽出来的结果是否可信”的门控。

## 10. Canonical 解析：`canonical_resolver.py`

入口文件：`pipeline/canonical_resolver.py`

这一阶段是标准词表守门员。

LLM 可能输出自由文本或 ID，例如：

```text
automotive clearcoat
scratch resistance
polyurethane dispersion
```

pipeline 需要把它们解析到 canonical ID：

```text
APP_automotive_oem_clearcoat
PROP_scratch_resistance
MAT_polyurethane_dispersion
```

当前规则偏严格：

1. 精确匹配 canonical ID。
2. 匹配 must_merge alias。
3. 匹配不到就返回 `None`。

其中最重要的 gate：

```text
application 必须 resolve 到 APP_*
property 必须 resolve 到 PROP_*
否则该 fact 不应进入最终可信 KG
```

这个 gate 很关键，因为它防止 LLM 随意发明 property/application 名称。

## 11. Polarity 与 comparison group

入口文件：

```text
pipeline/polarity.py
pipeline/comparison_group.py
```

`polarity.py` 判断表格行是：

```text
positive: inventive example
negative: comparative/reference example
unknown
```

判断依据包括：

- VLM 的 `row_polarity` hint。
- row label 中的 comparative、reference、example、embodiment 等关键词。

`comparison_group.py` 会把 positive fact 绑定到同一表格中对应的 negative baseline。

规则：

```text
positive fact
 -> 找同表、同 property、唯一 negative fact
 -> 找到唯一一个则绑定
 -> 找不到或多个候选则返回 None，进入 review
```

建议 gate：

- `polarity=unknown` -> review。
- positive 找不到唯一 baseline -> review。
- 同一 property 出现多个 baseline -> review。

## 12. Layer 1 段落和图证据：`passage_extractor.py`

入口文件：`pipeline/passage_extractor.py`

这一阶段产出 Layer 1：

```text
PassageHyperedge
FigureHyperedge
```

`PassageHyperedge` 来自正文段落：

```text
passage_id
doc_id
page
section_type
text_excerpt
entities
entities_provenance
source_tier
confidence
```

`FigureHyperedge` 来自 router 登记的 pending figures：

```text
figure_id
unit_id
doc_id
page
subtype
image_path
vlm_description
caption
tagged_entities
reference_numerals
confidence
```

Layer 1 的定位：

- 支持检索。
- 支持证据回溯。
- 给前端展示“这个 fact 或实体在原文哪里出现过”。

当前已有 confidence：

- passage confidence 是启发式：实体越多，置信度越高。
- figure confidence 来自默认值或 router。

当前缺口：

- passage confidence 不是模型校准分。
- alias matcher 目前仍是 substring scan，只做了轻量预过滤。
- ontology 变大后建议换 Aho-Corasick。

## 三条主线

可以把整个 pipeline 记成三条价值线。

### A. 文档过滤线

```text
PDF
 -> MinerU
 -> first-page metadata
 -> is_coating_patent gate
```

作用：

- 尽早判断是不是涂料专利。
- 避免无关文档进入昂贵下游。

### B. 数值事实线

```text
PDF
 -> MinerU
 -> Examples figure/table units
 -> VLM description
 -> router
 -> paragraph match
 -> fact extraction
 -> canonical gate
 -> FactHyperedge
```

作用：

- 从表格和上下文中抽结构化性能、配方、测试事实。
- 这是最核心、也最需要质量门控的一条线。

### C. 证据检索线

```text
PDF
 -> text passages / visual units
 -> entity tagging
 -> Layer 1 passages / figures
```

作用：

- 给 fact 和实体提供原文证据。
- 支持检索、跳转、前端展示。

## 当前置信度和 gate 总结

| Stage | 当前信号 | 当前问题 | 建议 |
| --- | --- | --- | --- |
| MinerU parse | API state | 缺 parse quality | 增加 page/block/table sanity |
| metadata | `is_coating_patent`, reason | 缺 metadata confidence | 增加 source/fallback/confidence |
| Examples split | span 或 fallback | fallback 风险未结构化 | fallback 标记 review |
| unit extract | skip empty table | image/bbox/label 缺少 gate | 增加 unit completeness score |
| VLM describe | retry + JSON parse | 缺 schema/enum gate | 增加 strict schema validation |
| router | route + reason | 置信度刚补，还未全链路使用 | `<0.7` 进入 review |
| paragraph match | score | score 未阻断低质上下文 | 设置上下文注入阈值 |
| fact extract | coverage + schema skip | 已补 quality/rejected_facts | 后续补 cell-level alignment |
| canonical | required field resolve | 当前只管 application/property | 扩展 optional slot quality |
| Layer 1 | heuristic confidence | 分数未校准 | 后续引入 rerank/embedding score |

## 建议统一产物 envelope

后续每个 stage 都可以统一输出一个质量 envelope：

```json
{
  "stage": "fact_extraction",
  "status": "pass",
  "confidence": 0.86,
  "issues": [],
  "metrics": {
    "coverage_pct": 91.2,
    "accepted_facts": 24,
    "rejected_facts": 1
  },
  "evidence": {
    "unit_id": "U_xxx",
    "source_file": "data/units/U_xxx/table.html"
  }
}
```

推荐状态语义：

```text
pass
  可以进入下游或最终 KG。

review
  产物可用但不够稳，需要人工或二次模型复核。

fail
  不应进入下游可信产物，只保留日志和原始证据。
```

## 加速优先级

1. **保留 router 前置**
   - 已经能避免很多非数值图表进入 fact extraction。

2. **VLM/fact 调用加缓存**
   - cache key 可由 `input_hash + prompt_version + model` 组成。
   - 重跑时跳过未变化 unit。

3. **按 doc 或 unit 并发**
   - VLM 和 fact extraction 大多是 I/O 等待型，适合 bounded concurrency。

4. **paragraph matcher 降频**
   - 只有 `extract_facts` 的 unit 才需要 paragraph matcher。
   - router 低置信度时可先 review，不急着跑昂贵下游。

5. **alias matcher 替换为 Aho-Corasick**
   - 当前 substring scan 在 ontology 小时够用。
   - 当 canonical/alias 增长到几千以上，应换自动机匹配。

6. **manifest 增量重跑**
   - 每个 stage 记录输入 hash、输出 schema version、model/prompt version。
   - 未变化的 stage 直接复用产物。

## 最重要的理解

这套 pipeline 的本质是：

```text
把专利 Examples 里的图表和正文
拆成可信结构化事实
再保留可回溯证据
```

当前已经有一些局部置信度和门控，但还没有完全统一。

下一阶段的工程目标应该是：

```text
从“能产出结果”
升级为
“每个结果都有质量状态、置信度、拒收原因和证据路径”
```

