# Coating Patent Knowledge Graph 鈥?瀹屾暣 Pipeline 鏂囨。

娑电洊 13 涓?stage锛堝惈 V2 寰呭姞鐨?9.5 鍜?10锛夈€佸綋鍓嶄簯 API 璋冪敤鏂瑰紡銆佹湭鏉ユ湰鍦?AWQ 妯″瀷杩佺Щ鏂规銆佷互鍙婁竴涓畬鏁寸殑绔埌绔ず渚嬨€?
---

## 鐩綍

1. [鍏ㄩ儴 stage 鎬昏琛╙(#1-鍏ㄩ儴-stage-鎬昏琛?
2. [姣忎釜 stage 鐨勮缁嗚鏄嶿(#2-姣忎釜-stage-鐨勮缁嗚鏄?
3. [璺ㄥ眰鏄犲皠浣撶郴锛圴isual Evidence Pointer锛塢(#3-璺ㄥ眰鏄犲皠浣撶郴visual-evidence-pointer)
4. [AWQ 鍦?A100 80G 鍗曞崱閮ㄧ讲璇﹁В](#4-awq-鍦?a100-80g-鍗曞崱閮ㄧ讲璇﹁В)
5. [绔埌绔ず渚嬶細WO2026052438A1 璧板畬 stage 0鈫?0](#5-绔埌绔ず渚媤o2026052438a1-璧板畬-stage-010)
6. [V1.2.5 鈫?V2.0.5 杩佺Щ娓呭崟](#6-v125--v205-杩佺Щ娓呭崟)
7. [绔埌绔椂闂?/ 鎴愭湰棰勭畻](#7-绔埌绔椂闂?-鎴愭湰棰勭畻)
8. [鏂囨。鐗堟湰](#8-鏂囨。鐗堟湰)

---

## 1. 鍏ㄩ儴 stage 鎬昏琛?
13 涓?stage 鎸?per-PDF 娴佺▼ + cross-PDF 姹囨€诲垎涓ょ被銆?
### 1.1 Per-PDF 閾捐矾

| # | 鍚嶅瓧 | 涓讳唬鐮?| 杈撳叆 | 杈撳嚭 | LLM/VLM 璋冪敤锛圴1.2.5 鐜扮姸锛?| V2 AWQ 杩佺Щ鍙樺寲 |
|---|------|--------|------|------|----------------------------|----------------|
| **0** | 缂栨帓 | `src/coating_kg/cli.py` `ingest_cmd`锛坙ine 26-268锛塦scripts/run_pipeline.py` | PDF 鍒楄〃 | 鈥?| 鏃?| 涓嶅姩 |
| **1** | MinerU 鐗堥潰瑙ｆ瀽 | `src/coating_kg/pipeline/pdf_layout.py:parse_pdf`锛堜簯锛塦scripts/a100_stages_0_3/01_batch_mineru.py`锛堟湰鍦帮級 | PDF 鏂囦欢 | `data/mineru_output/<doc_id>/auto/<uuid>_content_list.json` + `images/*.jpg` + `*.md` | MinerU 鑷繁锛堜笉鏄?LLM锛?| 宸茶縼鏈湴 6 璺?|
| **0.5** | 鍏冩暟鎹?+ IPC 闂搁棬 猸?| `src/coating_kg/pipeline/patent_metadata_extractor.py``cli.py:64-108`锛堟帴绾匡級 | content_list 棣栭〉鏂囨湰 | `data/patents/<doc_id>__patent_meta.json`锛堝惈 `is_coating_patent` / `is_coating_reason` / `section_split_mode` / `examples_page_range`锛?| **DashScope qwen-plus**锛屾瘡 PDF 涓€娆?| 鍒囧埌鏈湴 vLLM Qwen3.6-27B-AWQ |
| **2** | unit 鍒囧垎 | `src/coating_kg/pipeline/unit_extractor.py:iter_figure_table_units` | content_list (in-mem layout) | units 鍒楄〃 (in-mem)锛屽惈 `unit_id` / `bbox` / `caption_footnote_text` 绛?| 鏃狅紙绾鍒欙級 | 涓嶅姩 |
| **3** | unit 鐗╁寲 | `src/coating_kg/pipeline/unit_materializer.py:materialize_unit` | unit 瀵硅薄 + content_list 寮曠敤 | `data/units/<unit_id>/`锛坄meta.json` + `image.png` 鎴?`table.html` + `caption.txt`锛?| 鏃狅紙纾佺洏 IO锛?| 涓嶅姩 |
| **4** | VLM 鐪嬪浘鐪嬭〃 | `src/coating_kg/pipeline/vlm_describe.py``QwenVLClient.describe_figure / describe_table` | image.png 鎴?table.html + caption + candidate_ids | `data/units/<unit_id>/vlm_description.json`锛堝惈 description + identified_entities + subtype锛?| **DashScope qwen-vl-plus**锛坒igure锛? **qwen-plus**锛坱able锛?| 鍒囧埌鏈湴 vLLM Qwen3.6-27B-AWQ锛堝悓涓€涓疄渚嬪鐞嗕袱绉嶏級 |
| **4.5** | **unit router锛堜笁璺垎娴侊級猸?V2.0.5 寰呭啓** | `src/coating_kg/pipeline/unit_router.py`锛堝緟寤猴級 | stage 4 鐨?vlm_description.json + unit.meta | route enum: `extract_facts` / `register_layer1` / `delete`锛沘udit log `output/unit_routing_audit.csv` | 鏃狅紙绾鍒欒矾鐢憋級| 涓嶅姩 鈥?澶嶇敤 stage 4 杈撳嚭鍋氬厤璐瑰垎娴?|
| **5** | 瀹炰綋鍏滃簳鎵撴爣 | `src/coating_kg/pipeline/entity_tagger.py:tag_entities` | VLM description + caption + table HTML + DB alias 琛?| 鍐欏洖 `unit.tagged_entities`锛坕n-mem锛宎ho-corasick 瀛楃涓插尮閰嶈ˉ婕忥級 | 鏃狅紙DB 鏌ヨ + 瀛楃涓插尮閰嶏級 | 涓嶅姩锛汸oC 妯″紡 (`--skip-db`) 璺宠繃 |
| **6** | 娈佃惤鍖归厤 (PoC-2) | `src/coating_kg/pipeline/paragraph_matcher.py:ParagraphMatcher.match``src/coating_kg/pipeline/paragraph_extractor.py` | VLM description + Examples 娈佃惤 list | `data/units/<unit_id>/matched_paragraphs.json`锛坱op_k=10锛?| **DashScope qwen-plus**锛屾瘡 unit 涓€娆?| 鍒囧埌鏈湴 vLLM Qwen3.6-27B-AWQ |
| **7** | 浜嬪疄鎶藉彇 (PoC-3) 猸愭渶璐?| `src/coating_kg/pipeline/fact_extractor.py:FactExtractor.extract``prompts/fact_extract.txt` | unit + matched paragraphs + VLM description + ontology | `data/units/<unit_id>/facts.json` + `coverage.json` + `proposed_canonicals.json` | **DashScope qwen-plus**锛屾瘡 unit 涓€娆★紙鏈€璐电殑涓€娈碉級 | 鍒囧埌鏈湴 vLLM Qwen3.6-27B-AWQ + 鍔?JSON guided_decoding |
| **8** | 鏋佹€?+ comparison_group + (DB) | `src/coating_kg/pipeline/polarity.py``src/coating_kg/pipeline/comparison_group.py``src/coating_kg/db/insert.py`锛圖B 妯″紡锛?| facts list (in-mem) | 鍐欏洖 `polarity_hint` + `comparison_group`锛涘彲閫?PG insert | 鏃狅紙绾鍒欙級 | 涓嶅姩锛汥B 閮ㄥ垎 V2 KG 涓婄嚎鏃跺惎鐢?|

### 1.2 Cross-PDF 姹囨€伙紙鎵€鏈?PDF 璺戝畬鍚庤窇涓€娆★級

| # | 鍚嶅瓧 | 涓讳唬鐮?| 杈撳叆 | 杈撳嚭 | LLM/VLM 璋冪敤 | V2 AWQ 杩佺Щ鍙樺寲 |
|---|------|--------|------|------|-------------|----------------|
| **9** | 瀹借〃 CSV | `scripts/build_coatings_csv.py` | 鎵€鏈?`data/units/*/facts.json` + 鎵€鏈?`data/patents/*__patent_meta.json` | `output/coatings_wide.csv`锛堟瘡 Example 涓€琛岋紝鍚?`extraction_confidence_min/mean`锛?| 鏃?| 涓嶅姩 |
| **10** | KG 瑁呰浇 馃敎 V2 寰呭啓 | `scripts/load_facts_to_pg.py`锛堝緟寤猴級 | 鎵€鏈?facts.json + 鎵€鏈?passages.json + ontology seeded canonical_ids | PG 5 寮犺〃锛坣ode / fact_hyperedge / hyperedge_node / passage_hyperedge / passage_hyperedge_node锛? pgvector HNSW 绱㈠紩 | **鏈湴 BGE-M3** 绠?embedding | 鍏ㄦ湰鍦板疄鐜?|

### 1.3 鐘舵€佹眹鎬?
| 鐘舵€?| Stage 鍒楄〃 |
|------|------------|
| 鉁?V1.2.5 宸插畬鎴?| 0, 1, 0.5, 2, 3, 4, 5, 6, 7, 8, 9锛堝叡 11 涓級 |
| 馃敎 V2.0.5 寰呭啓 | **4.5**锛坲nit router 涓夎矾鍒嗘祦锛? **9.5**锛坧assage_extractor锛宻ingle per-doc LLM锛? **9.5 鎵╁睍 FigureHyperedge** |
| 馃敎 V2.x 寰呭啓 | **10**锛圞G loader 鐏?PG + pgvector锛?|
| 馃攧 V2.0 鏀?base_url | 0.5 / 4 / 6 / 7锛堜簯 鈫?鏈湴 vLLM AWQ锛?|

---

## 2. 姣忎釜 stage 鐨勮缁嗚鏄?
### Stage 0 鈥?缂栨帓锛圤rchestration锛?
**鑱岃矗**锛氭妸 stage 1鈫? 涓茶捣鏉ワ紝鍋氶敊璇殧绂?+ 杩涘害鏃ュ織銆?
**涓讳唬鐮?*锛?- `src/coating_kg/cli.py` 绗?26-268 琛?`@cli.command("ingest")`锛氬崟 PDF 鍏ュ彛
- `scripts/run_pipeline.py` 绗?146-222 琛?`main()`锛氬 PDF 鎵归噺椹卞姩

**鍏抽敭璁捐**锛?- 涓夌妯″紡锛氶粯璁ゅ叏璺戙€乣--skip-db`锛圥oC 妯″紡锛夈€乣--dry-run`锛堝彧璺?stage 1-3锛?- 閿欒闅旂涓夊眰绮掑害锛氬崟 stage warn / 鍗?unit rollback / 鏁寸瘒 PDF return
- `nullcontext(None)` 璁?PoC 鍜岀敓浜у叡鐢?`with conn_cm as conn` 浠ｇ爜

**LLM 璋冪敤**锛氭棤锛堢紪鎺掑眰锛?
**V2 鏀瑰姩**锛氭棤

---

### Stage 1 鈥?MinerU 鐗堥潰瑙ｆ瀽

**鑱岃矗**锛氭妸 PDF 瑙ｆ瀽鎴愮粨鏋勫寲 JSON锛堟瘡椤?blocks锛歵ext/title/image/table/equation 鍚?bbox锛夛紝骞舵妸 figure / table 鍒囨垚鍥剧墖鎴?HTML銆?
**涓讳唬鐮侊紙涓ゆ潯璺緞锛?*锛?- 浜?API锛歚src/coating_kg/pipeline/pdf_layout.py:parse_pdf`锛坙ine 313锛夆€?璧?MinerU 鍦ㄧ嚎 API
- **鏈湴 6 璺?*锛歚scripts/a100_stages_0_3/01_batch_mineru.py:run_mineru_for_pdf`锛坙ine 87锛夆€?璋冩湰鍦?`mineru` CLI

**鍏抽敭璁捐**锛?- 鍐呴儴 helper `_load_layout_json`锛坧df_layout.py:356锛夋敮鎸佽宸叉湁缂撳瓨鐨?content_list.json锛屾湰鍦拌窇瀹屽悗浜?fallback 涓嶄細閲嶅瑙﹀彂
- 鏈湴鎵硅窇 6 璺?GPU 骞跺彂锛圓100 鍗曞崱婊¤浇绾?72GB 鏄惧瓨锛?- 杈撳嚭 `<doc_id>/auto/<uuid>_content_list.json` 鏄悗缁墍鏈?stage 鐨勬€昏緭鍏?
**LLM 璋冪敤**锛氭棤锛圡inerU 鍐呴儴鐢ㄤ簡瑙嗚缂栫爜鍣紝浣嗚繖鏄?MinerU 鑷繁鐨勪簨锛屼笉绠楁垜浠殑 LLM stage锛?
**V2 鏀瑰姩**锛氭棤锛圡inerU 宸茬粡鏄湰鍦扮殑锛?
---

### Stage 0.5 鈥?鍏冩暟鎹娊鍙?+ IPC 闂搁棬 猸?
**鑱岃矗**锛氫粠 PDF 棣栭〉鏂囨湰鎶藉彇 title / IPC / applicant / abstract / filing_date锛屽苟璺?V1.2.5 鐨?4 淇″彿 `is_coating_patent` 鍒嗙被鍣ㄣ€?*闈炴秱鏂欎笓鍒╁湪姝?return锛岀渷 stage 4-7 鐨?LLM 閽?*銆?
**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/patent_metadata_extractor.py`锛堢害 360 琛岋級
  - line 48 `_PROMPT`锛坕nline prompt锛岃 LLM 杈撳嚭涓ユ牸 JSON锛?  - line 184 `_ANTI_COATING_KEYWORDS` 榛戝悕鍗曪紙CO2 / amine / detergent 绛?13 绫伙級
  - line 196 `is_coating_patent()` 4 淇″彿鍒嗙被鍣紙anti-keyword 鈫?primary IPC 鈫?title kw 鈫?secondary IPC + abstract 鈫?defer锛?  - line 271 `extract_patent_metadata()` 涓诲叆鍙?- `src/coating_kg/cli.py:64-108` 鏃╂湡闂搁棬鎺ョ嚎鐐?
**鍏抽敭璁捐**锛?- 鏃╂湡闂搁棬"寮€闂ㄩ€氳繃"鏄粯璁わ紙`meta.get("is_coating_patent", True)`锛夆€?瀹归敊浼樺厛
- 鎶藉彇澶辫触鏃惰蒋閫€鍖栵紙`except: warn 涓?raise`锛夆€?鍏冩暟鎹け璐ヤ笉 kill 鏁存壒
- patent_meta.json 鍦ㄩ椄闂ㄥ垽鏂墠灏辫惤鐩?鈥?鐣欒瘉鎹彲 grep

**LLM 璋冪敤**锛欴ashScope qwen-plus锛?*姣?PDF 涓€娆?*锛堥椤?~2K tokens 杈撳叆锛寏200 tokens 杈撳嚭 JSON锛夛紝绾?楼0.01/PDF

**V2 鏀瑰姩**锛?- 鏀?`pipeline/patent_metadata_extractor.py:301` 鐨?`OpenAI(api_key=..., base_url=...)`
- base_url 浠?`dashscope.aliyuncs.com/...` 鈫?`http://localhost:8000/v1`
- model 浠?`qwen-plus` 鈫?`Qwen3.6-27B-AWQ-INT4`
- 鍏?thinking 妯″紡锛歚extra_body={"chat_template_kwargs": {"enable_thinking": False}}`

---

### Stage 9.5 鈥?passage_extractor锛圠ayer 1锛夝煍?V2.0.5 寰呭啓

**鑱岃矗**锛氫粠鎵€鏈夋钀斤紙涓嶅彧 Examples 绔犺妭锛塏ER 鍑哄疄浣撳叡鐜帮紝浣滀负 Layer 1 PassageHyperedge锛?*涓?V2 KG retrieval 鎻愪緵"鏃犲畾閲忎簨瀹炴椂"鐨?fallback**銆?
**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/passage_extractor.py`锛?*鍗曟 per-doc LLM 璋冪敤 + 鍙嶆壂鍚勬钀?*锛?- `scripts/run_passage_extractor.py` 鎵归噺椹卞姩

**璁捐 鈥?single-pass per-doc LLM**锛?
| 姝ラ | 骞蹭粈涔?| 鐢ㄤ粈涔?| 鎴愭湰 |
|------|--------|--------|------|
| 1 | 鏁寸瘒涓撳埄鍏ㄦ枃 markdown + 宸叉湁 canonical_ids 涓€璧峰杺 LLM | DashScope qwen-plus锛圴2 鍒囨湰鍦?vLLM Qwen3.6-27B-AWQ锛墊 1 娆?PDF锛寏10s锛寏楼0.05 |
| 2 | LLM 杈撳嚭 doc 绾?entity-phrase mapping `[(phrase, canonical_id, confidence), ...]` | 鈥?| 0 |
| 3 | 瀵规瘡涓?passage 鐢?phrase 瀛楃涓叉壂鎻忥紝鍛戒腑鍗虫爣 entity | 绾瓧绗︿覆鍖归厤 | 0 |
| 4 | 娈佃惤绾?BGE-M3 embedding锛堝彲閫夛紝retrieval 鏃剁畻涔熻锛墊 鏈湴 BGE-M3锛垀2GB 鏄惧瓨锛?| 绠楀姏 |

**涓轰粈涔堜笉鍐嶅垎 Tier A / Tier B / Tier C**锛?
V2.0.5 瀹炴祴锛?0 绡?BASF 涓撳埄 demo锛夊彂鐜帮細
- 鏃?Tier A锛坅ho-corasick 闈欐€佸瓧鍏?per-passage 鎵級鎶?**101 entities**
- 鏃?Tier B锛坧er-passage qwen-plus锛夋娊 **405 entities** 鈥?鏄?Tier A 鐨?4脳锛屼笖瀹炶瘉**瀹屽叏瑕嗙洊 Tier A 鐨勬墍鏈夊懡涓?*
- LLM 鐪嬪叏鏂囦笂涓嬫枃姣旂湅鍗曟鏇村噯锛氳兘鍖哄垎 "polyurethane" 鍦ㄦ煇娈垫槸 resin 杩樻槸 binder
- per-passage 璋冪敤 119 娆?doc 鈫?楼2.4/doc / 10 min/doc锛宲er-doc 璋冪敤 1 娆?doc 鈫?楼0.05/doc / 10s/doc锛?*48脳 渚垮疁锛?0脳 蹇?*锛?- 缁撹锛歴tatic dict 娌℃湁鐙珛浠峰€硷紝鍏ㄩ儴浜ょ粰 LLM銆?
**杈撳嚭**锛歚data/layer1/<doc_id>/layer1.json`锛屽惈 **passages + figures 涓や釜 list**锛圴2.0.5 璧?Layer 1 鍚屾椂鎵胯浇娈佃惤鍜屽浘琛級锛?
#### Layer 1 绫诲瀷 A锛歅assageHyperedge锛堟钀借秴杈癸級

```json
{
  "passage_id": "WO2026057741A1_p18_b237",
  "doc_id": "WO2026057741A1",          // 鈫?璺?Layer 2 fact 鍏变韩 doc_id
  "page": 18,                            // 鐗╃悊瀹氫綅
  "section_type": "DESCRIPTION",         // DESCRIPTION / CLAIMS / EXAMPLES / ABSTRACT / ...
  "text_excerpt": "...",                 // 娈佃惤鍘熸枃
  "entities": [                          // LLM 鎶藉嚭 + phrase 鍙嶆壂鍛戒腑鐨?canonical_id list
    "MAT_silane_coupling_agent",
    "MAT_waterborne_polyurethane_dispersion",
    "APP_automotive_oem_clearcoat"
  ],
  "entity_phrases": {                    // 鈽?LLM 鎶藉嚭鐨?surface phrase 鈫?canonical_id 鏄犲皠
    "silane coupling agent": "MAT_silane_coupling_agent",
    "WPUD": "MAT_waterborne_polyurethane_dispersion",
    "OEM clearcoat": "APP_automotive_oem_clearcoat"
  },
  "confidence": 0.92,                    // 鈽?rerank 鏃剁敤锛坧er-passage 骞冲潎 LLM confidence锛?  "embedding_id": "embed_..."            // 娈佃惤鍚戦噺鍦?PG pgvector 琛ㄧ殑 ID锛圴2 璁＄畻锛?}
```

**瀛楁鍋忕 spec 鐨勮鏄?*锛氱浉瀵?`v2_spec_checklist.md:140` 鐨?5 瀛楁绠€鐗堬紙text + page + para_index + tagged_entities + text_embedding锛夛紝澶氫簡 `section_type` / `entity_phrases` / `confidence` 涓夊瓧娈点€?*杩欐槸 V2.0.5 conscious deviation**锛堣瑙?搂8 鍋忕璇存槑锛夛紝鐢ㄦ埛瀹炴祴鍐冲畾淇濈暀銆傚凡鍒?`entities_provenance` / `source_tier` / `near_units` 涓変釜鏃у瓧娈点€?
#### Layer 1 绫诲瀷 B锛欶igureHyperedge锛堝浘琛ㄨ秴杈癸級猸?V2.0.5 鏂板

鏉ヨ嚜 stage 4.5 璺敱涓?`register_layer1` 鐨?unit锛堝弽搴斿紡 / 娴佺▼鍥?/ 绀烘剰鍥?/ SEM / 璁惧鍥?绛夋棤瀹氶噺鏁版嵁浣嗘湁瑙嗚浠峰€肩殑鍥撅級銆?
```json
{
  "figure_id": "FIG_WO2026052438A1_p15_b203",
  "unit_id": "U_WO2026052438A1_p15_b203",       // 鈫?per-unit traceability key锛?*涓嶆槸璺ㄥ眰 JOIN**锛岃涓嬫枃璇存槑锛?  "doc_id": "WO2026052438A1",
  "page": 15,                                    // 鈫?鐗╃悊瀹氫綅锛圥DF 璺宠浆鐢級
  "subtype": "scheme",                           // 鈫?stage 4 闂泦锛歴tructure/scheme/plot/sem/apparatus/other
  "image_path": "data/units/U_.../image.png",    // 鈫?retrieval 鏃剁洿鎺ユ覆鏌撳師鍥?  "vlm_description": "Reaction scheme illustrating urethane formation between hydroxyl-functional polyacrylate (Component A) and HDI trimer isocyanate (Component B), catalyzed by DBTDL...",
  "caption": "Scheme 1. Synthesis of polyurethane network via hydroxyl-isocyanate addition.",
  "tagged_entities": [
    "MAT_polyacrylate", "MAT_HDI_trimer", "MAT_DBTDL",
    "PROC_urethane_formation"
  ],
  "reference_numerals": [                        // 鈫?stage 4 vlm_figure prompt 宸叉娊锛屽墠绔彲鏍囧彿
    {"numeral": "101", "label": "polyacrylate"},
    {"numeral": "102", "label": "HDI trimer"}
  ],
  "confidence": 0.88
}
```

**鍏抽敭涓夊瓧娈碉紙V2.0.5 鏂板锛?*锛?- `unit_id` 鈥?**per-unit traceability key**锛?*涓嶆槸璺ㄥ眰 JOIN 涓婚敭**锛?- `page` 鈥?鐗╃悊瀹氫綅锛宺etrieval 杩斿洖鏃剁粰 PDF 璺宠浆閾炬帴
- `image_path` 鈥?鐩存帴缁欏墠绔覆鏌撳師鍥撅紙涓嶄緷璧?retrieval 鏃跺啀鍘绘煡鏂囦欢绯荤粺锛?
**鍏充簬 unit_id 鐨勮鑹叉緞娓咃紙V1.2.7 淇锛?*锛?
stage 4.5 router 鏄?*浜掓枼涓夎矾鍒嗘祦** 鈥斺€?涓€涓?`unit_id` 鍙細鍑虹幇鍦ㄤ互涓?*涓€澶?*锛?```
unit U_xxx 缁?router 鍚庡彧鑳借繘涓€澶勶細
  鈫?extract_facts:    unit_id 钀藉湪 facts.evidence_pointer.unit_id
  鈫?register_layer1:  unit_id 钀藉湪 figures.unit_id
  鈫?delete:           unit_id soft-绉诲埌 _routed_out/锛屾案涓嶅嚭鐜板湪 layer1/2
```

鈫?`SELECT ... FROM facts JOIN figures ON facts.unit_id = figures.unit_id` **姘歌繙绌洪泦**銆?
**鐪熸鐨勮法灞傚叧鑱旀槸閫氳繃 canonical_id 鍏变韩**锛堣妭鐐圭骇 JOIN锛?
- Layer 2 fact 鐨?`resin_system / additives / property` 閮芥槸 canonical_id锛圡AT_/PROP_/...锛?- Layer 1 figure / passage 鐨?`tagged_entities` 涔熸槸 canonical_id 鍒楄〃
- 鐢ㄦ埛鏌ヨ鏌愪釜 entity 鏃讹紝Layer 1 + Layer 2 閮界敤 entity overlap 鍙洖锛宺etrieval 灞?GROUP BY `doc_id` 鑱氬悎鏄剧ず

`unit_id` 鐨勭湡瀹炰綔鐢ㄦ槸**灞傚唴 traceability** 鈥斺€?retrieval 鍛戒腑 fact / figure 鍚庯紝鐢?`unit_id` JOIN `figure_table_units` 琛ㄥ彇 image_path / table_html 娓叉煋瑙嗚璇佹嵁銆?
鈥斺€斺€斺€斺€?
### V1 宸茬煡鏁版嵁缂哄彛 鈥?Plot 绫?figure

V1 鎶?`plot` 绫?figure 閮借矾鐢卞埌 `register_layer1`锛堜笉杩?stage 7 fact 鎶藉彇锛夈€備唬浠凤細

- 鉁?retrieval 鏃?VLM 鎻忚堪閲岀殑"gloss 92% at 4000h"瀵硅€佹澘鑳藉睍绀?- 鉂?**wide CSV 缂?plot 琛嶇敓鐨勫畾閲忔暟鎹垪**锛圕SV 鍙?cover table 鏁版嵁锛?- 鉂?V2 KG SQL 閲?`WHERE result_value > 80 AND test_method = 'TEST_QUV'` 杩欑 plot 鏁版嵁鏌ヤ笉鍒?
V2 璁″垝鍗囩骇 stage 7 鍔?plot 鏁板€艰瘑鍒兘鍔涳紙鍥剧墖 鈫?CSV 璺緞锛夛紝鎶?plot 鍒囧埌 `extract_facts`銆傚綋鍓?V1 demo 涓嶅繀鍋氥€?
**缁?stakeholder 鐨勮瘽鏈?*锛歱lot 绫?figure 鐨勫畾閲忔暟鎹繚鐣欏湪 VLM 鑷劧璇█鎻忚堪閲岋紝鏈粨鏋勫寲鎶藉彇锛沄2 鍗囩骇 stage 7 鍚庡垏鍒扮粨鏋勫寲鎶藉彇銆?
**涓轰粈涔堜笉鍋氭垚 PassageHyperedge 鐨勫瓙绫?*锛氫袱鑰呭舰鎬佸樊澶銆侾assage 鏄?text锛孎igure 鏄?image_path + VLM 鎻忚堪 + reference_numerals 杩欑瑙嗚鐗规湁瀛楁銆傚己琛屽悎骞?schema 浼氳 PG 绱㈠紩璁捐鍙樺鏉傘€?*V2 瀹炴柦鏃?figures 鍗曠嫭寤鸿〃**锛?
```sql
CREATE TABLE figures (
    figure_id TEXT PRIMARY KEY,
    unit_id TEXT REFERENCES figure_table_units,
    doc_id TEXT REFERENCES patents,
    page INT,
    subtype TEXT,                       -- closed enum
    image_path TEXT,
    vlm_description TEXT,
    caption TEXT,
    tagged_entities JSONB,
    reference_numerals JSONB,
    description_embedding VECTOR(1024),
    confidence FLOAT
);
CREATE INDEX ON figures USING hnsw (description_embedding vector_cosine_ops);
CREATE INDEX ON figures USING gin (tagged_entities);
CREATE INDEX ON figures (doc_id, page);
```

**LLM 璋冪敤**锛欴ashScope qwen-plus 涓€娆¤皟鐢?/ PDF
- 杈撳叆锛氬叏鏂?markdown锛垀30K tokens for 澶т笓鍒╋級 + 鐜版湁 canonical_ids list锛垀3K tokens锛? prompt锛垀500 tokens锛?- 杈撳嚭锛欽SON list of `{"phrase": str, "canonical_id": str, "confidence": float}`
- qwen-plus 128K context 瀹屽叏澶熺敤
- V2 鍒囧埌鏈湴 vLLM Qwen3.6-27B-AWQ锛?28K context 涔熷锛屾ā鍨嬫湰韬師鐢熸敮鎸侀暱涓婁笅鏂囷級

**鍏抽敭鍐崇瓥**锛?
1. **淇濈暀鍏ㄩ儴娈佃惤锛堜笉鍙?Examples锛?*锛氬洜涓?Layer 1 鏄?鏃?fact 鏃?fallback"锛孍xamples 涔嬪鐨?description / claims 鍚ぇ閲忔湭琛ㄦ牸鍖栫殑 hint锛宺etrieval 绔鑳藉懡涓€?
2. **璺ㄥ眰 cross-reference 鏄ぉ鐒剁殑**锛歀ayer 1 PassageHyperedge 璺?Layer 2 FactHyperedge 鍏变韩 `doc_id` + 鍏变韩 ontology 鑺傜偣锛圡AT_*/PROP_*/...锛夈€俽etrieval 鏃舵寜 `doc_id` GROUP BY 灏辫兘鎶婂悓涓€绡囦笓鍒╃殑 fact + passage 鑱氬悎鏄剧ず銆?
3. **涓轰粈涔堜粠 stage 1.5 鏀瑰埌 stage 9.5**锛氬師鏈璁℃斁鍦?per-PDF 閾捐矾锛堣窡 stage 0.5 / 2 骞跺垪锛夛紝浣?passage_extractor 瀹為檯涓婁笉渚濊禆 stage 2-8 鐨勪骇鍑猴紙鍙 content_list 鍏ㄦ枃锛夛紝璺?stage 9 / 10 涓€鏍峰睘浜?璺ㄨ鏂欑殑 indexing 鎬ф楠?銆傛敼 9.5 鍚?per-PDF 閾捐矾鏇寸嚎鎬э紝stage 10 (KG load) 鑷劧渚濊禆 9.5 + 9銆?
4. **鍙栨秷 `near_units` 瀛楁**锛氬師璁捐鎯崇敤鐗╃悊椤佃窛锛埪?0 椤靛唴鐨?unit锛夊仛"閭昏繎 Layer 2 鎻愮ず"锛屽疄闄呭彂鐜扮墿鐞嗛偦杩?鈮?璇箟鐩稿叧锛堝悓绔犺妭閲?30+ unit 鍏ㄩ儴鍛戒腑锛宺etrieval 鏃朵粛鐒惰鎸?entity overlap 閲嶆柊绛涳級銆傛敼涓?retrieval 鏃舵寜 `doc_id` JOIN 璁＄畻 entity overlap 鎺掑簭鏇寸簿鍑嗐€?
5. **鍙栨秷 Tier A/B/C 鍒嗘。 + `entities_provenance` + `source_tier` 瀛楁**锛歏2.0.5 瀹炴祴 Tier B锛坧er-passage LLM锛夊畬鍏ㄨ鐩?Tier A锛坅ho-corasick锛夛紝static dict 澶卞幓鐙珛浠峰€笺€俆ier C锛坋mbedding锛変綔涓?retrieval-time 璁＄畻鏇寸伒娲伙紝鏃犻渶鍦?indexing 鏃惰惤鐩?provenance銆傜畝鍖栦负鍗曚竴 per-doc LLM 娴佺▼锛?   - 鎬ц兘锛?0脳 蹇紙10s vs 10min锛夈€?8脳 渚垮疁锛埪?.05 vs 楼2.4锛?   - 鍑嗙‘搴︼細LLM 鐪嬪叏鏂囦笂涓嬫枃 鈮?鐪嬪崟娈碉紙瀹炴祴妗堜緥锛氶暱閾捐剛鑲吀 alkyl acrylate 鐨勭⒊鏁版爣娉級
   - 鍙В閲婃€ч潬 `entity_phrases` 瀛楁锛堢洿鎺ョ湅 LLM 鎶藉嚭鐨勫師鏂?surface phrase锛?
6. **瑙﹀彂锛氬叏绡囬兘璺戯紝涓嶅啀鏈?闀挎鎵嶈Е鍙?Tier B"閫昏緫**銆傛瘡绡?PDF 鍥哄畾涓€娆?LLM 璋冪敤锛岃Е鍙戦€昏緫琚秷闄ゃ€?
**棰勭畻**锛?48 绡?BASF 鍏ㄨ窇锛夛細
- per-doc LLM 楼0.05 脳 348 = **楼17**
- 鏃堕棿 ~10s 脳 348 / 骞跺彂 8 = **~7 鍒嗛挓**锛堜簯 API 8 骞跺彂锛? 鏈湴 vLLM 32 骞跺彂鍙帇鍒?**~2 鍒嗛挓**

---

---

### Stage 2 鈥?unit 鍒囧垎

**鑱岃矗**锛氫粠 content_list 閲屾寫鍑?type=image / table 涓?page_idx 钀藉湪 Examples 绔犺妭鍐呯殑 block锛屾瀯閫?`FigureTableUnit` 瀵硅薄鍒楄〃銆?
**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/unit_extractor.py:iter_figure_table_units`锛坙ine 46锛?- V1.2.5 鍔犵殑 `_extract_bbox` 杈呭姪鍑芥暟
- `src/coating_kg/pipeline/section_split.py:split_examples_section` 鈥?Examples 绔犺妭瀹氫綅
- `src/coating_kg/pipeline/paragraph_extractor.py:examples_page_range`

**鍏抽敭鍙傛暟**锛歚examples_only=True`锛圴1.2.5 璁捐锛歀ayer 2 fact 鍙潵鑷?Examples 琛ㄦ牸锛?
**LLM 璋冪敤**锛氭棤锛堢函瑙勫垯锛?
**V2 鏀瑰姩**锛氭棤

---

### Stage 3 鈥?unit 鐗╁寲

**鑱岃矗**锛氭妸 in-memory 鐨?FigureTableUnit 瀵硅薄鍐欏埌纾佺洏 鈥?`data/units/<unit_id>/` 涓嬪垱寤?`meta.json` + `caption.txt` + `image.png` 鎴?`table.html`銆?
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/unit_materializer.py:materialize_unit`锛坙ine 43锛?
**鍏抽敭璁捐**锛氬け璐?propagate up锛堟棤 try/except锛夛紝鐗╁寲鏄熀纭€璁炬柦澶辫触搴?fail-fast銆?
**LLM 璋冪敤**锛氭棤锛堢鐩?IO锛?
**V2 鏀瑰姩**锛氭棤

---

### Stage 4 鈥?VLM 鐪嬪浘鐪嬭〃

**鑱岃矗**锛氭瘡涓?unit 璋?VLM/LLM 鐢熸垚鑷劧璇█鎻忚堪 + 璇嗗埆宸茬煡瀹炰綋 + 鍒嗙被 subtype銆?
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/vlm_describe.py:QwenVLClient`
- `describe_figure(image_path, candidate_ids)` 鈥?figure 璧?VLM
- `describe_table(html, caption, candidate_ids)` 鈥?table 璧版枃鏈?LLM锛堝凡 OCR 鎴?HTML 浜嗭級

**杈撳嚭**锛歚data/units/<unit_id>/vlm_description.json` 鍚?`{description, identified_entities, subtype}`

**LLM 璋冪敤**锛?- figure: DashScope **qwen-vl-plus / qwen-vl-max**锛堟瘡 figure 楼0.05锛?- table: DashScope **qwen-plus**锛堟瘡 table 楼0.005锛?
**V2 鏀瑰姩**锛?- 鍒囧埌鏈湴 vLLM Qwen3.6-27B-AWQ锛堝悓涓€涓疄渚嬪悓鏃跺鐞?figure VLM 璇锋眰鍜?table 鏂囨湰璇锋眰 鈥?鍥犱负 27B 鏄?VLM锛?- 鏀?`pipeline/vlm_describe.py` 鐨?client base_url + model name
- 鍏?thinking 妯″紡

---

### Stage 4.5 鈥?unit router锛堜笁璺垎娴侊級猸?V2.0.5 寰呭啓

**鑱岃矗**锛氬熀浜?stage 4 宸茬粡浠樿繃閽辩殑 VLM 杈撳嚭锛坄vlm_description.json`锛夛紝鎶婃瘡涓?unit 璺敱鍒颁笁鏉′笅娓歌矾寰勪箣涓€锛?*鎴帀涓嶈璺戠殑 LLM 璋冪敤**锛屽苟鎶?闈炲畾閲忎絾鏈夎瑙変环鍊?鐨?unit 娉ㄥ唽鍒?Layer 1 FigureHyperedge銆?
**鍏抽敭鎬ц川**锛?*绾鍒欒矾鐢憋紝涓嶈皟 LLM**銆傚鐢?stage 4 宸茬粡浠樿繃鐨勯挶鍋氬厤璐瑰垎娴侊紝鏈韩鑰楁椂鍑犳绉掋€?
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/unit_router.py`锛堝緟寤猴紝绾?80 琛岋級

**涓夎矾鍒嗘祦瑙勫垯**锛?
| 璺敱 | 瑙﹀彂鏉′欢 | 涓嬫父澶勭悊 | 鍗犳瘮浼拌 |
|------|---------|---------|---------|
| `extract_facts` | table 绫伙紙subject 鈭?鎬ц兘瀵规瘮/閰嶆柟瀵规瘮/娴嬭瘯鏉′欢锛墊 璧?stage 6/7/8 鎶?fact锛岃繘 Layer 2 | ~50% |
| `register_layer1` | figure 绫伙紙subtype 鈭?structure/scheme/plot/sem/apparatus/other锛夛紱鎴?table_subject="鍏跺畠" | 璺?stage 6/7锛屾敞鍐?FigureHyperedge 杩?Layer 1 | ~45% |
| `delete` | `region_id_dedup_skip=True`锛堣法椤佃〃鐨勫悗鍗婇儴鍒嗭級锛涙垨 entities=[] AND len(description)<30锛堣楗板厓绱?/ Logo锛墊 `shutil.rmtree(unit_dir)` 鐗╃悊鍒犻櫎 | ~5% |

**Plot 绫荤殑鐗规畩璇存槑**锛歷1 鏆傚綊 `register_layer1`锛堜繚鐣欏師鍥?+ VLM 鎻忚堪锛夛紱v2 鍗囩骇 stage 7 鍔?plot 鏁板€艰瘑鍒兘鍔涘悗鍒囧埌 `extract_facts`锛堜粠鑰愬€欐洸绾挎娊 (x, y) 鏁版嵁鐐癸級銆?
**鎺ョ嚎鐐?*锛歚cli.py:185` 涔嬪墠銆俿tage 4 璺戝畬鍚庣珛鍒昏矾鐢憋細

```python
# stage 4 宸茶惤 vlm_description.json
route, reason = unit_router.classify_unit_route(unit, vlm_desc)
audit_log.write(unit.unit_id, route, reason)

if route == "delete":
    shutil.rmtree(unit_dir)
    continue                 # 璺?stage 5/6/7/8/9.5

if route == "register_layer1":
    write_pending_figure(unit, vlm_desc)   # 鈫?绛?stage 9.5 鏀?    continue                 # 璺?stage 6/7/8

# route == "extract_facts": 姝ｅ父璧?stage 6/7/8
```

**鏀剁泭浼扮畻**锛?48 绡?脳 ~15 unit = 5220 unit锛夛細

| 鍦烘櫙 | unit 璋冪敤 LLM 娆℃暟 |
|------|-------------------|
| 褰撳墠锛堟棤 4.5锛墊 5220 脳 3 (stage 4+6+7) = **15660 娆?* |
| 鍔?4.5 鍚?| 50% 脳 2 (4+6+7 鍚堝苟) + 45% 脳 1 (4 only) + 5% 脳 1 (4 + rmtree) = **2610脳2 + 2349脳1 + 261脳1 = 7830 娆?* |

**鏈湴 A100 vLLM 32 骞跺彂鎺ㄧ悊**锛?- 褰撳墠锛?5660 脳 2s / 32 = ~16 鍒嗛挓
- 鍔?4.5锛?830 脳 2s / 32 = **~8 鍒嗛挓锛堢渷 50%锛?*

**Audit 涓?false-negative 闃插尽**锛?- 钀?`output/unit_routing_audit.csv`锛坲nit_id, route, reason, vlm_desc 鍓?200 瀛楃锛?- 11 绡?golden set 鍏ㄨ窇鍚庢娊鏍?100 涓?`register_layer1` + 30 涓?`delete` unit 浜哄伐瀹?- false-negative 鐜?< 5% 鎵嶅悎 main锛堝嵆"璇?extract_facts 浣嗚璺敱鎴?register_layer1"鐨勬瘮渚嬶級

**LLM 璋冪敤**锛氭棤

**V2 鏀瑰姩**锛氭棤 鈥?杩欎釜 stage 涓€寮€濮嬪氨鏄?V2.0.5 璁捐

---

### Stage 5 鈥?瀹炰綋鍏滃簳鎵撴爣

**鑱岃矗**锛氬 VLM 娌¤瘑鍒嚭鐨勫凡鐭?canonical锛岀敤 aho-corasick 瀛楃涓?鍒悕鍖归厤琛ヤ竴閬撱€?
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/entity_tagger.py:tag_entities`

**鍏抽敭璁捐**锛?- 鍙湪 `conn is not None`锛堢敓浜фā寮忥級鎵嶈窇锛屽洜涓洪渶瑕佹煡 DB 鐨?alias 琛?- PoC 妯″紡锛坄--skip-db`锛夋暣娈?skip
- 璺?stage 9.5 鐨?Tier A 鏄悓涓€绫绘満鍒讹紙aho-corasick 澶氭ā寮忓尮閰嶏級锛屽彧鏄綔鐢ㄥ璞℃槸 unit 绾ц€屼笉鏄钀界骇

**LLM 璋冪敤**锛氭棤

**V2 鏀瑰姩**锛氭棤

---

### Stage 6 鈥?娈佃惤鍖归厤 (PoC-2)

**鑱岃矗**锛氫负姣忎釜 unit 閫?top-10 涓渶鐩稿叧鐨?Examples 娈佃惤锛堟彁渚?stage 7 fact_extractor 鐨勪笂涓嬫枃锛夈€?
**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/paragraph_matcher.py:ParagraphMatcher.match`
- `src/coating_kg/pipeline/paragraph_extractor.py:gather_examples_paragraphs`

**鍏抽敭鍙傛暟**锛歚top_k=10` 纭紪鐮侊紙PoC-2 娴嬪嚭鐨勭敎铚滅偣锛? `region_label=unit.region_id` 鎻愮ず LLM 鎵惧摢寮犺〃/鍥?
**杈撳嚭**锛歚data/units/<unit_id>/matched_paragraphs.json`锛堝惈 top_k 娈佃惤 + 璇勫垎 + 閫夋嫨鐞嗙敱锛?
**LLM 璋冪敤**锛欴ashScope qwen-plus锛屾瘡 unit 涓€娆?
**V2 鏀瑰姩**锛?- 鏀?`pipeline/paragraph_matcher.py` 鐨?client base_url + model name
- 鍏?thinking 妯″紡锛坱op_k 閫夋涓嶉渶瑕?reasoning chain锛?
---

### Stage 7 鈥?浜嬪疄鎶藉彇 (PoC-3) 猸愭渶璐?
**鑱岃矗**锛氫粠 unit锛堣〃 HTML + caption + matched paragraphs锛夋娊鍑虹粨鏋勫寲 FactHyperedge JSON list锛?9+1 瀛楁锛歠act_id / application / property / evidence_pointer / result_value / process / test_method / etc.锛夈€?
**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/fact_extractor.py:FactExtractor.extract`锛堢害 600 琛岋紝鏈€澶嶆潅锛?- `prompts/fact_extract.txt`锛圴1.2.5 鏀逛簡 rule 5b/5c empty cell + rule 8 example_id literal + rule 9 sub_type closed enum + EXAMPLE D rowspan锛?
**V1.2.5 鏀瑰姩**锛?- evidence_pointer.unit_id 鍥炲～锛坱ask #1锛?- evidence_pointer.bbox 閫忎紶锛坱ask #2锛?- empty-cell guard锛坱ask #3锛?- example_id Level 0 trust LLM literal锛圴1.2.4锛?
**杈撳嚭**锛?- `data/units/<unit_id>/facts.json`锛堜富浜у嚭锛宻tage 9 build CSV 鐢級
- `proposed_canonicals.json`锛圴1.2.3 propose-and-curate锛屽緟浜哄锛?- `coverage.json`锛圴1.2.5 Tier-0 Fix 3锛岃 stage 9 鍖哄垎"鐪熸病鏈?vs"鎶藉彇鎴柇"锛?
**LLM 璋冪敤**锛欴ashScope qwen-plus锛?*姣?unit 涓€娆?*锛堣緭鍏?~3K tokens table HTML + matched paragraphs锛岃緭鍑?~500-1500 tokens JSON facts锛夛紝绾?楼0.3-0.5/unit

**V2 鏀瑰姩**锛?- 鏀?`pipeline/fact_extractor.py` 鐨?client base_url + model name
- 鍔?`response_format={"type": "json_object"}` 寮哄埗 JSON
- 鐢?vLLM `--guided-decoding-backend outlines` 鍦ㄩ噰鏍烽樁娈电姝㈢敓鎴愰潪娉?JSON
- 鍔?retry on JSON parse error
- 鍏?thinking 妯″紡锛坒act 鎶藉彇鏄粨鏋勫寲浠诲姟锛屼笉闇€瑕?reasoning锛?
---

### Stage 8 鈥?鏋佹€?+ comparison_group + (鍙€?DB)

**鑱岃矗**锛?- 8a 鏋佹€у悗澶勭悊锛歀LM 涓嶇‘瀹氾紙"unknown"锛夋椂鐢ㄨ鍒欐帹 inventive/comparative
- 8b comparison_group 缁戝畾锛氭妸鍚屼竴寮犺〃閲岀浉鍏?fact 褰掑埌鍚屼竴涓?group_id
- 8c锛堝彲閫夛級DB 鍐欏叆锛歜ulk_insert_facts 杩?PG

**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/polarity.py:classify_polarity`锛堢函瑙勫垯锛岀湅 row label锛?- `src/coating_kg/pipeline/comparison_group.py:resolve_comparison_group`
- `src/coating_kg/db/insert.py:bulk_insert_facts`锛圖B 妯″紡锛?- 涓変釜璋冪敤鐐归兘鍦?`cli.py:232-263` 鐨勫悓涓€涓?try 鍧楀唴锛坧er-unit 浜嬪姟锛?
**鍏抽敭璁捐**锛?- 瑙勫垯鍙湪 LLM 涓嶇‘瀹氭椂琛ヤ綅锛圠LM 涓婁笅鏂囨洿鍏紝浼樺厛锛?- facts.json 鎬绘槸钀界洏锛堜笉璁?DB 鏈夋棤锛夛紝璁?PoC 妯″紡 build CSV 鑳借窇

**LLM 璋冪敤**锛氭棤锛堝叏瑙勫垯锛?
**V2 鏀瑰姩**锛氭棤锛汥B 閮ㄥ垎绛?V2 KG 涓婄嚎鍚敤

---

### Stage 9 鈥?瀹借〃 CSV

**鑱岃矗**锛氭妸鎵€鏈?PDF 鐨?facts.json 鑱氬悎鎴愪竴琛屼竴涓?Example 鐨?wide CSV锛屾瘡涓?Material/Property sub_type 涓€鍒椼€?
**涓讳唬鐮?*锛歚scripts/build_coatings_csv.py`锛堢害 790 琛岋級
- line 81 `is_coating_patent()` 鈥?灞€閮ㄥ嚱鏁帮紝鐢?IPC 鍓嶇紑鍋氳€佸紡杩囨护锛圴2 浼氳 stage 0.5 鐨?4 淇″彿闂搁棬鏇夸唬锛?- line 397 `build_row()` 鈥?鍗曚釜 (doc_id, example_id) 琛屾瀯寤?- line 689 `main()` 鈥?涓绘祦绋?
**V1.2.5 鏀瑰姩**锛?- `extraction_confidence_min/mean` 鍒楋紙line 553锛?- qualitative result_value_text fallback锛坙ine 557锛?
**LLM 璋冪敤**锛氭棤

**V2 鏀瑰姩**锛氭棤

---

### Stage 10 鈥?KG 瑁呰浇 馃敎 V2.x 寰呭啓

**鑱岃矗**锛氭妸鎵€鏈?facts.json + passages.json + ontology canonical_ids 鐏岃繘 PostgreSQL + pgvector锛屽缓濂?5 寮犺〃 + HNSW 绱㈠紩銆?
**涓讳唬鐮侊紙寰呭啓锛?*锛歚scripts/load_facts_to_pg.py` 鎴?`pipeline/kg_loader.py`

**5 寮?PG 琛?*锛?1. `node` 鈥?瀹炰綋鑺傜偣锛坕d / canonical_id / type / label / aliases / **embedding vector(1024)**锛?2. `fact_hyperedge` 鈥?Layer 2 杈癸紙fact_id / doc_id / example_id / result_value / evidence_pointer JSONB锛?3. `hyperedge_node` 鈥?Layer 2 杈?鑺傜偣澶氬澶氶摼鎺ワ紙hyperedge_id / node_id / role锛?4. `passage_hyperedge` 鈥?Layer 1 杈癸紙passage_id / doc_id / page / text_excerpt / **embedding**锛?5. `passage_hyperedge_node` 鈥?Layer 1 杈?鑺傜偣閾炬帴

**绱㈠紩**锛?- `node`: BTree(type, canonical_id) + **HNSW(embedding)**
- `passage_hyperedge`: HNSW(embedding) + GIN(entities)
- `fact_hyperedge`: BTree(doc_id, example_id) + GIN(evidence_pointer)

**澶栭儴渚濊禆**锛?- PostgreSQL 16 + pgvector extension
- 鏈湴 BGE-M3 宓屽叆妯″瀷锛垀2 GB 鏄惧瓨锛岃窡 vLLM 鍏遍┗ A100锛?
**LLM 璋冪敤**锛氭棤锛涙湰鍦?BGE-M3 绠?embedding锛堜笉鏄?LLM 璋冪敤锛屾槸 embedding 妯″瀷锛?
**V2 鏀瑰姩**锛氭湰韬氨鏄?V2 鏂板

---

## 3. 璺ㄥ眰鏄犲皠浣撶郴锛圴isual Evidence Pointer锛?
### 3.1 涓夊眰鏄犲皠绮掑害

| 灞?| 鍛戒腑绫诲瀷 | 鏄犲皠鐩爣 | 绮掑害 | 涓婚敭瀛楁 |
|----|---------|---------|------|----------|
| Layer 1 | PassageHyperedge | 娈佃惤鍘熸枃 + page | **娈佃惤绾?* | `passage_id` |
| Layer 1 | FigureHyperedge | image.png + VLM 鎻忚堪 + page | **鍥炬暣寮?* | `figure_id` 鈫?`unit_id` |
| Layer 2 | FactHyperedge | image.png/table.html + cell + bbox + page | **cell 绾?* 猸?鏈€绮剧‘ | `fact_id` 鈫?`evidence_pointer.unit_id` |

**鏍稿績鎬濇兂**锛氭瘡鏉¤秴杈归兘閫氳繃 `unit_id` 鎴?`passage_id` 涓婚敭 JOIN 鍥炲埌鍘熺墿鏂欙紙image.png / table.html / 娈佃惤鍘熸枃锛夛紝**璁?retrieval 绛旀姘歌繙鑳借烦杞埌 PDF 鍘熷瑙嗚璇佹嵁**銆?
### 3.2 retrieval-time 鐢ㄦ埛浣撻獙瀵规瘮

鐢ㄦ埛闂€宨nventive A1B1 閰嶆柟涓?cross-cut adhesion 澶氬皯銆嶏細

#### Layer 2 鍛戒腑 fact锛堟渶瀹屾暣锛?
```
馃煝 楂樼疆淇″害鍥炵瓟
   adhesion (cross-cut) = 5B

馃搳 鏉ユ簮锛堢粨鏋勫寲锛?   inventive: A1B1
   閰嶆柟: COMPONENT_A1 + HDI_trimer (NCO/OH=1.35)
   宸ヨ壓: spray apply, ambient cure 23掳C/50% RH/7d, then 80掳C/30 min

馃摲 瑙嗚璇佹嵁
   [Table 1 绗?3 琛岀 5 鍒梋 鈫?bbox 妗嗙孩
   [璺宠浆 PDF page 25]      鈫?閾炬帴鍒板師涓撳埄
```

瀹炴柦闈狅細`fact.evidence_pointer.unit_id` JOIN `figure_table_units` 琛ㄦ嬁 `image_path` / `table_html`锛屽墠绔敤 `bbox` 鍦?image 涓婄敾妗嗐€?
#### 鍙?Layer 1 鍛戒腑娈佃惤锛堣瘹瀹為檷绾э級

```
馃煛 鍩轰簬娈佃惤绾ц瘉鎹紝鏈粨鏋勫寲鎻愬彇
   "Example A1B1 was prepared by mixing component A1..."

馃摲 [璺宠浆 PDF page 23]
```

瀹炴柦闈狅細`passage.text` 鐩存帴灞曠ず锛宍passage.page` 缁欒烦杞€?
#### 鍙?Layer 1 鍛戒腑鍥撅紙瑙嗚闄嶇骇锛?
```
馃煛 鍩轰簬鍥捐〃璇佹嵁
   [reaction scheme image]                鈫?娓叉煋鍘熷浘
   VLM 鎻忚堪: "Reaction scheme illustrating urethane formation between..."
   reference numerals: 鈶?polyacrylate, 鈶?HDI trimer
   [璺宠浆 PDF page 15]
```

瀹炴柦闈狅細`figure.image_path` 娓叉煋锛宍figure.reference_numerals` 鏍囧彿銆?
### 3.3 璺ㄥ眰 JOIN锛圴2 retrieval API锛?
V2 retrieval 绔彲浠ュ仛杩欑璺ㄥ眰鏌ヨ锛?
```sql
-- 缁欎竴涓懡涓殑 fact锛屾壘瀹冩墍鍦?unit 鐨勬梺杈规钀斤紙瑙嗚涓婁笅鏂囷級
SELECT p.text, p.page
FROM facts f
JOIN passages p
  ON f.evidence_pointer->>'doc_id' = p.doc_id
WHERE f.fact_id = $1
  AND ABS((f.evidence_pointer->>'page')::int - p.page) <= 2
ORDER BY p.text_embedding <=> f.evidence_embedding
LIMIT 3;
```

娉ㄦ剰锛?*杩欎釜璺ㄥ眰 JOIN 鍙敤浜?灞曠ず灞傝ˉ鍏?锛屼笉杩?Evidence Pack**锛坰pec 搂淇 6 瑕佹眰 Layer 1 涓嶇洿鎺?ground claim锛夈€倂erifier 浠嶇劧鍙 Layer 2 fact 鍋?claim-level 鏍￠獙銆?
### 3.4 V1.2.5 宸插氨浣?/ V2.0.5 寰呭姞

| 鏄犲皠閾?| 鐜扮姸 |
|--------|------|
| Layer 2 fact 鈫?unit_id | 鉁?V1.2.5 task #1 |
| Layer 2 fact 鈫?bbox | 鉁?V1.2.5 task #2 |
| Layer 2 fact 鈫?page / region_id / row / column / cell | 鉁?V1.2.5 宸叉湁 |
| Layer 1 passage 鈫?text + page | 鉁?schema 鍚?|
| **Layer 1 figure 鈫?unit_id** | 馃敎 V2.0.5 stage 4.5 钀藉湴鏃跺姞 |
| **Layer 1 figure 鈫?page** | 馃敎 V2.0.5 stage 4.5 钀藉湴鏃跺姞 |
| **Layer 1 figure 鈫?image_path** | 馃敎 V2.0.5 stage 4.5 钀藉湴鏃跺姞 |

---

## 4. AWQ 鍦?A100 80G 鍗曞崱閮ㄧ讲璇﹁В

### 4.1 AWQ 鏄粈涔?
**AWQ = Activation-aware Weight Quantization**锛堟縺娲绘劅鐭ユ潈閲嶉噺鍖栵紝MIT Han Lab 2023 鎻愬嚭锛夈€?
**浣嶅**锛歐4A16 鈥?鏉冮噸 4 bit锛屾縺娲?16 bit锛堟帹鐞嗘椂锛夈€?**鏍稿績鍒涙柊**锛氭牴鎹?activation magnitude 鏅鸿兘淇濇姢 1% 閲嶈鏉冮噸锛屽叾浣?99% 4-bit 閲忓寲銆傝川閲忔瘮鍚?4-bit 鐨?GPTQ 楂?1-2 涓?MMLU 鐐广€?
### 4.2 涓轰粈涔?AWQ 鑳藉杩?A100 80G

浠?Qwen3.6-27B-AWQ-INT4 涓轰緥锛?
```
妯″瀷鏉冮噸 (AWQ INT4):
  27B params 脳 0.5 bytes/param = 13.5 GB
  + 閲忓寲鍏冩暟鎹紙scales / zeros锛夆増 5.5 GB
  鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
  瀹為檯鏉冮噸钀界洏                  ~19 GB 锛堟ā鍨嬪崱纭锛?
A100 80G 浣欓噺 = 80 - 19 = 61 GB 鈫?鏋佸
```

瀵规瘮 BF16 鐗堟湰锛?7B 脳 2 bytes = 54 GB锛屽姞 KV cache + 妗嗘灦 overhead 鍚?A100 80G 绱с€?*AWQ 鎶?weights 鍘嬬缉 4x锛岀粰 KV cache 鍜屽苟鍙戠暀鍑哄ぇ閲忕┖闂?*銆?
### 4.3 鏄惧瓨鍗犵敤濡備綍璁＄畻

鎬诲崰鐢ㄥ叕寮忥細
```
VRAM_total = VRAM_weights + VRAM_kv_cache + VRAM_activation + VRAM_framework
```

#### 4.3.1 KV cache 璁＄畻锛圦wen3.6-27B 鐗规畩鏋舵瀯锛?
Qwen3.6-27B 鏄?**hybrid attention** 妯″瀷锛?8 灞?DeltaNet 绾挎€ф敞鎰忓姏 + 16 灞傞棬鎺?softmax 娉ㄦ剰鍔涳級銆?*鍙湁 16 灞?softmax 娉ㄦ剰鍔涘崰 KV cache**锛?
```
KV per token = 2 (K+V) 脳 n_softmax_layers 脳 n_kv_heads 脳 head_dim 脳 dtype_bytes
             = 2 脳 16 脳 4 脳 256 脳 2
             = 65,536 bytes
             = 64 KB / token
```

DeltaNet 閮ㄥ垎鐢?O(d虏) 鐘舵€佸悜閲忥紝涓嶉殢 sequence 闀垮害澧為暱锛屽彲浠ュ拷鐣ヤ笉璁°€?
**瀵规瘮甯歌 dense 妯″瀷**锛?- Qwen2.5-32B: 64 layers 脳 8 KV heads 脳 128 head_dim 脳 2 脳 2 = 256 KB/token锛?*4 鍊嶄簬 27B**锛?- Qwen2.5-72B: 80 脳 8 脳 128 脳 2 脳 2 = 320 KB/token锛?*5 鍊嶄簬 27B**锛?
Qwen3.6-27B 鐨?KV cache 浼樺娍鏄灦鏋勭孩鍒┿€?
#### 4.3.2 瀹炴垬閰嶇疆鐭╅樀锛圓100 80G锛?
```
鍥哄畾鍗犵敤:
  鏉冮噸         19 GB
  瑙嗚缂栫爜鍣?  3 GB  锛?7B 鏄?VLM锛屽惈 ViT + projector锛?  vLLM overhead 4 GB
  鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
  灏忚         26 GB
  鍓╀綑 KV 姹?  54 GB
```

| max_model_len | max_num_seqs | KV 姹犻渶姹?| 鎬诲崰鐢?| 鐘舵€?|
|---------------|--------------|-----------|--------|------|
| 8 KB | 16 | 8 GB | 34 GB | 鉁?鏋佸 |
| 8 KB | 32 | 16 GB | 42 GB | 鉁?鎺ㄨ崘 |
| 16 KB | 32 | 32 GB | 58 GB | 鉁?浣欓噺澶?|
| 32 KB | 32 | 64 GB | 90 GB | 鉂?OOM |
| 16 KB | 64 | 64 GB | 90 GB | 鉂?OOM |

鎴戜滑 fact_extractor 杈撳叆 ~3K + 杈撳嚭 ~1K = 4K/璇锋眰锛?*鎺ㄨ崘閰嶇疆 max_model_len=8K, max_num_seqs=32**锛屾€诲崰鐢?~42 GB锛孉100 80G 浣?38 GB 瀹夊叏 margin銆?
#### 4.3.3 vLLM 鍚姩鍛戒护

```bash
vllm serve ~/models/Qwen3.6-27B-AWQ-INT4 \
  --port 8000 \
  --max-model-len 8192 \
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.7 \
  --enable-prefix-caching \
  --guided-decoding-backend outlines
```

`--gpu-memory-utilization 0.7` 鐣?30% buffer 搴斿 KV cache 绐佸彂銆俙--enable-prefix-caching` 璁╃浉鍚?prompt 鍓嶇紑澶嶇敤缂撳瓨锛坒act_extractor 鐨?system prompt 瀹屽叏鐩稿悓锛屽姞閫?5x锛夈€俙--guided-decoding-backend outlines` 鍦ㄩ噰鏍烽樁娈靛己鍒?JSON 鍚堟硶銆?
### 4.4 绮惧害鎹熷け鍒嗘瀽

#### 4.4.1 閲忓寲绮惧害鎹熷け鐨勬潵婧?
1. **鏉冮噸鍘嬬缉**锛欶P16 (16 bit) 鈫?INT4 (4 bit)锛屾瘡涓潈閲嶅€肩殑鏈夋晥绮惧害浠?65,536 涓瓑绾ч檷鍒?16 涓瓑绾?2. **鑸嶅叆璇樊**锛氬師鏈繛缁殑 FP16 鍊兼槧灏勫埌绂绘暎鐨?INT4 绛夌骇
3. **婵€娲绘孩鍑?*锛氭煇浜?outlier 鏉冮噸瀵瑰簲鐨勬縺娲诲€间細琚す鏂?
#### 4.4.2 AWQ 濡備綍鍑忓皯鎹熷け

鏈寸礌 INT4 閲忓寲锛氭墍鏈夋潈閲嶄竴瑙嗗悓浠併€?**AWQ 绠楁硶**锛?
```
1. 鐢?calibration dataset 璺?forward锛岃褰曟瘡涓?channel 鐨?activation magnitude |a|
2. 鎵惧嚭 |a| 鏈€澶х殑 1% channel锛堣繖浜涙槸"鏄捐憲鏉冮噸"锛?3. 瀵规樉钁?channel 鐢?per-channel scaling锛屾縺娲诲€肩缉灏?s 鍊嶅悗閲忓寲锛坰 鐢变紭鍖栧緱鍒帮級
4. 鎺ㄧ悊鏃舵妸瀵瑰簲鏉冮噸脳s锛屾縺娲幻?1/s)锛屾暟瀛︾瓑浠蜂絾閲忓寲绮惧害楂?```

鏁堟灉锛?% 鏄捐憲鏉冮噸淇濈暀鎺ヨ繎 FP16 绮惧害锛?9% 鏅€氭潈閲?INT4銆傛渶缁堟ā鍨嬪钩鍧?4-bit 浣嗗鏈€鍏抽敭鐨?1% 娌℃崯澶便€?
#### 4.4.3 绮惧害鎹熷け瀹氶噺

瀛︽湳 benchmark锛圦wen2.5 / Llama-3 绯诲垪瀹炴祴锛夛細

| 閲忓寲鏂规 | 妯″瀷 | 浠诲姟 | 鎹熷け锛坴s FP16锛?|
|----------|------|------|----------------|
| GPTQ INT4 | Llama-3-70B | MMLU | -2.1 pt |
| **AWQ INT4** | **Llama-3-70B** | **MMLU** | **-1.0 pt** |
| AWQ INT4 | Qwen2.5-72B | MMLU | -0.8 pt |
| AWQ INT4 | Qwen2.5-32B | MMLU | -1.2 pt |
| AWQ INT3 | Llama-3-70B | MMLU | -5.5 pt |
| BF16 (baseline) | 鈥?| 鈥?| 0 |

**瀵圭粨鏋勫寲鎶藉彇浠诲姟锛堟垜浠?fact_extractor锛夊奖鍝嶉€氬父鏇村皬**锛?- 浠诲姟妯″紡鍥哄畾锛屾ā鍨嬪彧闇€"閬靛惊 prompt 鏍煎紡"锛屼笉闇€瑕佸鏉傛帹鐞?- AWQ 4-bit 鐨?1-2% 鎹熷け涓昏褰卞搷"闀块摼鎺ㄧ悊"锛屽"鎸?prompt 杈撳嚭 JSON"鍑犱箮鏃犲奖鍝?- 瀹為檯褰卞搷浼拌 < 1% precision drop

#### 4.4.4 鎴戜滑瀹為檯鍙兘鎹熷け澶氬皯

**绔埌绔换鍔★紙348 绡囨秱鏂欎笓鍒?fact 鎶藉彇锛?*锛?
```
娼滃湪鎹熷け鏉ユ簮:
  AWQ 閲忓寲              ~1-2%  fact 鏁伴噺 / result_value 涓€鑷存€?  鍏?thinking 妯″紡      ~3-5%  瀵硅竟缂?case锛堝涔夋€?row锛夊奖鍝嶇暐澶?  绀惧尯閲忓寲 calibration  ~0-2%  cyankiwi 鐨?calibration set 鏄?STEM + Agentic"锛屽娑傛枡 domain 閫傞厤搴︽湭鐭?  鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
  鍚堣浼拌              5-9%   鎬昏川閲忎笅闄?```

**杩欎釜鏁板瓧鏄及璁′笂闄愶紝瀹為檯鍙兘鏇村皬**銆傚繀椤荤敤 11 绡?ground truth 璺戝洖褰掑疄娴嬶細
- 閫氳繃鐜?鈮?90%锛堝嵆鎹熷け < 10%锛夆啋 鍙敤
- 閫氳繃鐜?80-90% 鈫?璋?prompt 鍐嶅洖褰?- 閫氳繃鐜?< 80% 鈫?鍒囧洖浜?qwen-plus 鎴栨崲鏇村ぇ妯″瀷

#### 4.4.5 閫熷害瀵规瘮锛堝疄娴嬩及璁★級

| 閰嶇疆 | 鍗曟祦 tok/s | 32 骞跺彂 aggregate tok/s |
|------|-----------|------------------------|
| Qwen3.6-27B BF16 | ~15 | ~200 |
| **Qwen3.6-27B AWQ + outlines + prefix-caching** | **~50** | **~1000** |
| Qwen3.6-27B AWQ + MTP | ~80 | ~1500 |
| Qwen2.5-32B BF16 | ~15 | OOM (瑁呬笉涓?32 骞跺彂) |
| Qwen2.5-72B AWQ | ~25 | ~280 |

**AWQ 涓嶅彧鐪佹樉瀛橈紝杩樺揩 2-3 鍊?*锛堝洜涓?weights load 鍑忓皯锛宮emory bandwidth 鏄?LLM 鎺ㄧ悊鐡堕锛夈€?
---

## 5. 绔埌绔ず渚嬶細WO2026052438A1 璧板畬 stage 0鈫?0锛堝惈 9.5锛?
鎸戜竴绡囩湡瀹?BASF 2026 PCT 涓撳埄 `WO2026052438A1`锛?TWO-COMPONENT COATING COMPOSITION"锛夎蛋瀹屾暣娴佺▼锛屾瘡 stage 鍒楀嚭**杈撳叆 / 澶勭悊 / 浜у嚭**銆?
### 杈撳叆

```
data/pdf/WO2026052438A1.pdf
```

42 椤?PDF锛屽惈锛?- 鎽樿銆佽鏄庛€乧laims锛坧age 1-22锛?- Examples 绔犺妭锛坧age 23-41锛夊惈 6 寮犺〃
- 1 寮犲伐鑹烘祦绋嬪浘

### Stage 0 鈥?缂栨帓

**杈撳叆**锛氫笂闈㈢殑 PDF 璺緞
**澶勭悊**锛歚python -m coating_kg ingest WO2026052438A1.pdf --skip-db` 鍚姩鍗?PDF 娴佺▼
**浜у嚭**锛氭棩蹇?`ingest start: WO2026052438A1.pdf (dry_run=False, skip_db=True)`

### Stage 1 鈥?MinerU 鐗堥潰瑙ｆ瀽

**杈撳叆**锛歅DF
**澶勭悊**锛氭湰鍦?mineru CLI 6 璺苟鍙戝叾涓?1 璺窇杩欑瘒
**浜у嚭**锛?```
data/mineru_output/WO2026052438A1/
鈹斺攢鈹€ auto/
    鈹溾攢鈹€ 60fd22cc-..._content_list.json    锛?2 椤?脳 骞冲潎 30 block 鈮?1260 block锛?    鈹溾攢鈹€ 60fd22cc-...md                     锛坢arkdown 鍏ㄦ枃锛?    鈹溾攢鈹€ 60fd22cc-..._layout.json
    鈹斺攢鈹€ images/
        鈹溾攢鈹€ a3f8b2.jpg                      锛堝伐鑹烘祦绋嬪浘鍒囩墖锛?        鈹斺攢鈹€ ... 5 寮犺〃鐨勬埅鍥句篃鍦?```

### Stage 0.5 鈥?鍏冩暟鎹?+ IPC 闂搁棬

**杈撳叆**锛歝ontent_list 绗?1 椤?text blocks锛堝墠 ~2K tokens锛?**澶勭悊**锛欴ashScope qwen-plus 璋冪敤涓€娆★紙V2 鍚庡垏鏈湴 vLLM Qwen3.6-27B-AWQ锛?**浜у嚭**锛?```json
data/patents/WO2026052438A1__patent_meta.json
{
  "patent_id": "WO2026052438A1",
  "title": "TWO-COMPONENT COATING COMPOSITION",
  "applicant": "BASF Coatings GmbH",
  "filing_date": "2025-09-15",
  "publication_number": "WO2026052438A1",
  "ipc_codes": ["C09D 175/14", "C08G 18/62", "C08G 18/73", "C08K 5/10", "C08L 75/14"],
  "abstract": "A two-component coating composition comprising ...",
  "is_coating_patent": true,                 鈫?鈽?鏃╂湡闂搁棬閫氳繃锛圕09D primary IPC锛?  "is_coating_reason": "primary IPC: C09D 175/14",
  "section_split_mode": "found",             鈫?Examples 绔犺妭瀹氫綅鎴愬姛
  "examples_page_range": [23, 41]
}
```

cli.py 鐪嬪埌 `is_coating_patent=true`锛?*缁х画璧?stage 2-8**銆?
### Stage 9.5 鈥?passage_extractor锛圴2 鍚庢墠鏈夛級

**杈撳叆**锛歝ontent_list 鍏ㄦ枃 markdown锛?2 椤?~95K 瀛楃 / ~30K tokens锛? ontology 宸叉湁 canonical_ids list
**澶勭悊**锛?- 涓€娆?qwen-plus 璋冪敤锛堣緭鍏?~33K tokens / 杈撳嚭 ~2K tokens JSON锛?- LLM 杩斿洖 doc 绾?entity-phrase mapping锛岀害 80 涓?(phrase, canonical_id, confidence)锛?  - 宸叉湁 canonical 鍛戒腑锛歚polyurethane`, `clearcoat`, `isocyanate`, `Tin catalyst`, ...
  - 鏂?propose锛歚MAT_BASF_X-Tend_2400`锛坱rade name锛岃惤 propose-and-curate sidecar锛?- 瀵?200 娈?type=text block 鍚勮嚜鍋?phrase 瀛楃涓叉壂鎻忥紝鍛戒腑鍗虫爣 entity
- 澶辫触鍥為€€锛欽SON parse 澶辫触锛堢綍瑙侊級鍒?retry 涓€娆★紱浠嶅け璐ュ垯 skip 璇?PDF 钀?warn 鏃ュ織锛屼笉闃诲 pipeline

**LLM 璋冪敤鎴愭湰**锛? 娆?qwen-plus / ~10s / 楼0.05

**浜у嚭**锛?```json
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
    "embedding_id": null     // V2 retrieval 鏃跺啀绠?BGE-M3
  },
  ... 鍏辩害 200 鏉?]

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

### Stage 2 鈥?unit 鍒囧垎

**杈撳叆**锛歭ayout in-memory锛堝惈 1260 涓?block锛?**澶勭悊**锛歚iter_figure_table_units(layout, examples_only=True)` 鎸戝嚭 page 23-41 鍐呯殑 table/image block
**浜у嚭**锛? 涓?`FigureTableUnit` in-memory 瀵硅薄锛?- `U_WO2026052438A1_p25_b330` (Table 1锛岀粍鎴愯〃)
- `U_WO2026052438A1_p25_b332` (Table 2锛岀墿鎬ц〃)
- `U_WO2026052438A1_p26_b343` (Table 3)
- `U_WO2026052438A1_p26_b346` (Table 4)
- `U_WO2026052438A1_p27_b357` (Table 5)
- `U_WO2026052438A1_p27_b359` (Table 6)

### Stage 3 鈥?unit 鐗╁寲

**杈撳叆**锛? 涓?unit 瀵硅薄 + content_list 涓殑 image / table HTML 寮曠敤
**澶勭悊**锛氭瘡 unit 鍒涘缓鐩綍 + 鍐?4 涓枃浠?**浜у嚭**锛?```
data/units/U_WO2026052438A1_p25_b330/
鈹溾攢鈹€ meta.json                  锛坲nit 鍏冩暟鎹細bbox, page, region_id, etc.锛?鈹溾攢鈹€ caption.txt                锛?Table 1: Composition of clearcoat A1B1..."锛?鈹溾攢鈹€ table.html                 锛圡inerU OCR 鍑烘潵鐨勮〃 HTML锛?鈹斺攢鈹€ 锛堟病 image.png锛屽洜涓烘槸 table锛?... 6 涓?unit 鐩綍
```

### Stage 4 鈥?VLM 鐪嬪浘鐪嬭〃

**杈撳叆**锛? 涓?unit 鐨?`table.html` + caption + candidate_ids锛圥oC 妯″紡绌?list锛?**澶勭悊**锛氭瘡 unit 璋冧竴娆?DashScope qwen-plus锛坱able 璧版枃鏈?LLM锛屽洜涓?HTML 宸茬粨鏋勫寲锛?**浜у嚭**锛堟瘡 unit 涓€浠斤級锛?```json
data/units/U_WO2026052438A1_p25_b330/vlm_description.json
{
  "description": "Table shows composition (in wt%) of inventive clearcoat A1B1 and three comparative formulations (CC-A1B1 inv, CC-A2 comp, CC-B1 comp, CC-A1+B1 comp). Components include polyurethane resin (component A), polyisocyanate (component B), additives Hydropalat WE3650, Tego Wet 270, EFKA SL3035.",
  "identified_entities": ["MAT_COMPONENT_A1", "MAT_Hydropalat_WE3650", "MAT_EFKA_SL3035", "MAT_Tego_Wet_270"],
  "subtype": "Composition"
}
```

### Stage 4.5 鈥?unit router锛堜笁璺垎娴侊級

**杈撳叆**锛? 涓?unit 鐨?stage 4 杈撳嚭 `vlm_description.json`
**澶勭悊**锛?- 6 寮犺〃 鍏ㄩ儴 subtype="Composition" 鎴?"Property" 鈫?璺敱 `extract_facts` 脳 6
- 鍋囪杩欑瘒涓撳埄杩樻湁 1 寮犲弽搴斿紡 figure锛坰ubtype="scheme"锛夆啋 璺敱 `register_layer1` 脳 1
- 鍋囪鏈?1 寮?BASF logo锛坋ntities=[], desc<30 瀛楃锛夆啋 璺敱 `delete` 脳 1
**浜у嚭**锛?```
output/unit_routing_audit.csv
unit_id, route, reason
U_WO2026052438A1_p25_b330, extract_facts, table_閰嶆柟瀵规瘮
U_WO2026052438A1_p25_b332, extract_facts, table_鎬ц兘瀵规瘮
... 6 琛?extract_facts ...
U_WO2026052438A1_p10_b88,  register_layer1, figure_scheme
U_WO2026052438A1_p1_b1,    delete, low_info_decoration
```
- 6 涓?extract_facts unit 缁х画璧?stage 5/6/7/8
- 1 涓?register_layer1 unit 鍐欏埌 `data/layer1/WO2026052438A1/pending_figures.jsonl` 绛?stage 9.5 鏀?- 1 涓?delete unit 鐩存帴 `rmtree(unit_dir)`

### Stage 5 鈥?瀹炰綋鍏滃簳鎵撴爣

**杈撳叆**锛歏LM description + caption + table HTML + DB alias 琛?**澶勭悊**锛歅oC 妯″紡 (`--skip-db`) **鏁存 skip**
**浜у嚭**锛氭棤锛堢敓浜фā寮忔墠鏈夛級

### Stage 6 鈥?娈佃惤鍖归厤 (PoC-2)

**杈撳叆**锛歶nit 鐨?VLM description + Examples 娈佃惤 list锛堢害 200 娈碉紝stage 9.5 杈撳嚭锛?**澶勭悊**锛欴ashScope qwen-plus 涓€娆¤皟鐢紝浠?200 娈甸噷閫?top-10 鏈€鐩稿叧
**浜у嚭**锛堟瘡 unit 涓€浠斤級锛?```json
data/units/U_WO2026052438A1_p25_b330/matched_paragraphs.json
{
  "top_k": 10,
  "matches": [
    {
      "para_id": "p23_b417",
      "page": 23,
      "text": "Example A1B1 was prepared by mixing component A1 (polyurethane resin) and component B1 (HDI trimer) in a ratio corresponding to NCO/OH index 1.35, after 30 min induction time...",
      "score": 0.92,
      "reason": "娈佃惤鏄庣‘缁欏嚭 A1B1 鐨勯厤鏂圭粍鎴?+ 宸ヨ壓鏉′欢锛岃窡 Table 1 寮虹浉鍏?
    },
    ... 9 涓洿澶?  ]
}
```

### Stage 7 鈥?浜嬪疄鎶藉彇 (PoC-3) 猸愭渶璐?
**杈撳叆**锛歶nit + matched paragraphs + VLM description + ontology
**澶勭悊**锛欴ashScope qwen-plus 涓€娆¤皟鐢紝杈撳叆 ~3K tokens锛岃緭鍑?~1500 tokens 涓ユ牸 JSON
**浜у嚭**锛堟瘡 unit 涓€浠斤級锛?```json
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
        "unit_id": "U_WO2026052438A1_p25_b330",   鈫?V1.2.5 task #1
        "bbox": [120, 340, 480, 360]               鈫?V1.2.5 task #2
      },
      "doc_id": "WO2026052438A1",
      "resin_system": "MAT_COMPONENT_A1",
      "additives": ["MAT_Hydropalat_WE3650", "MAT_EFKA_SL3035"],
      "process": [
        {"step": "PROC_spray_apply", "condition": "after 30 min induction time"},
        {"step": "PROC_ambient_cure", "condition": "23掳C, 50% RH, 7 days"},
        {"step": "PROC_thermal_cure", "condition": "80掳C, 30 min"}
      ],
      "test_condition": {
        "film_thickness": "45鈥?5 渭m",
        "isocyanate_index": 135
      },
      "result_value": 100.0,
      "result_value_text": "CC-A1B1 (inventive)",
      "comparison_group": "F_001",
      "extraction_confidence": 0.95,
      "example_id": "A1B1"
    },
    ... 鍏辩害 30 facts
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

6 涓?unit 鍏辨娊鍑虹害 **42 涓?facts**锛堝疄闄呮暟鎹祴杩囷級銆?
### Stage 8 鈥?鏋佹€?+ comparison_group + (DB)

**杈撳叆**锛?2 涓?facts in-memory
**澶勭悊**锛?- 8a 鏋佹€цˉ浣嶏細绾鍒欐帹 row label锛屾妸 LLM 杈撳嚭 "unknown" 鐨?8 鏉℃敼鎴?"negative"锛坈omparative锛?- 8b comparison_group 缁戝畾锛? 寮犺〃鍐?fact 鎸?(region_id, property, application) 鍒嗗埌 8 涓?group锛團_001 ~ F_008锛?- 8c DB 鍐欏叆锛歅oC 妯″紡璺宠繃
**浜у嚭**锛歠acts in-memory 鏇存柊锛堝啓鍥?facts.json锛?
### Stage 9 鈥?瀹借〃 CSV

**杈撳叆**锛氭墍鏈?PDF 鐨?facts.json + patent_meta.json锛堝惈 WO2026052438A1 鐨?6 涓?facts.json锛?**澶勭悊**锛氳仛鍚堝埌涓€琛屼竴涓?(doc_id, example_id) 鐨勫琛?**浜у嚭**锛堣妭閫?WO2026052438A1 閮ㄥ垎锛夛細
```csv
output/coatings_wide.csv
patent_id,example_id,polarity,Material:Resin / binder,Material:Crosslinker,Property:Mechanical,...,extraction_confidence_min,extraction_confidence_mean,applicant,filing_date
WO2026052438A1,A1B1,positive,"MAT_COMPONENT_A1@7:100 wt%","MAT_HDI_trimer@4:NCO/OH=1.35","adhesion_cross_cut@3:5B; pendulum_hardness@5:165 s",...,0.85,0.93,BASF Coatings GmbH,2025-09-15
WO2026052438A1,A1B2,positive,"MAT_COMPONENT_A1@7:100 wt%; MAT_COMPONENT_A2@6:50 wt%","MAT_HDI_trimer@4:NCO/OH=1.35","adhesion_cross_cut@3:5B; pendulum_hardness@5:172 s",...,0.88,0.94,BASF Coatings GmbH,2025-09-15
WO2026052438A1,CC-A1+B1,negative,"MAT_COMPONENT_A1@7:100 wt%","MAT_HDI_trimer@4:NCO/OH=1.35","adhesion_cross_cut@3:3B; pendulum_hardness@5:140 s",...,0.83,0.91,BASF Coatings GmbH,2025-09-15
... 鍏?8 琛岋紙4 inventive + 4 comparative锛屽搴?Examples 绔犺妭 8 涓?example_id锛?```

stage 9 杩橀『鎵嬬畻浜?`extraction_confidence_min=0.83 / mean=0.92` 涓ゅ垪锛岀粰 retrieval 绔仛"淇′换鍒嗙骇"銆?
### Stage 10 鈥?KG 瑁呰浇锛圴2 寰呭啓锛?
**杈撳叆**锛?- 鍏ㄩ儴 PDF 鐨?`data/units/*/facts.json`锛?48 绡?脳 ~30 unit 鈮?10000 涓?facts.json锛?- 鍏ㄩ儴 PDF 鐨?`data/passages/<doc_id>/passages.json`锛?48 涓紝姣忎釜鍚?200 娈碉級
- ontology seed canonical_ids锛垀150 涓?MAT_* / ~80 涓?PROP_* / ~30 涓?APP_* / ...锛?
**澶勭悊**锛? 寮?PG 琛?+ pgvector 绱㈠紩锛夛細
1. `node` 琛細鎶?ontology canonical 鐏岃繘鍘伙紙鍚?BGE-M3 embedding 1024 缁达級
2. `fact_hyperedge` 琛細姣忔潯 fact 涓€琛岋紙fact_id PK锛宒oc_id / example_id / result_value / evidence_pointer JSONB锛?3. `hyperedge_node` 琛細姣忔潯 fact 璺熷畠娑夊強鐨?node 鐨勫瀵瑰閾炬帴锛坒act_id, node_id, role锛夛紝role 鈭?{application, property, resin_system, additive, process_step, test_method, ...}
4. `passage_hyperedge` 琛細姣忔 passage 涓€琛岋紙passage_id PK锛宒oc_id / page / text_excerpt / embedding 1024 缁达級
5. `passage_hyperedge_node` 琛細passage 璺?node 鐨勫瀵瑰閾炬帴锛坧assage_id, node_id锛?
**绱㈠紩**锛?```sql
-- node 琛?CREATE INDEX idx_node_type_canonical ON node (type, canonical_id);
CREATE INDEX idx_node_embedding_hnsw ON node USING hnsw (embedding vector_cosine_ops);

-- passage_hyperedge 琛?CREATE INDEX idx_passage_embedding_hnsw ON passage_hyperedge USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_passage_entities_gin ON passage_hyperedge USING gin (entities);

-- fact_hyperedge 琛?CREATE INDEX idx_fact_doc_example ON fact_hyperedge (doc_id, example_id);
CREATE INDEX idx_fact_evidence_gin ON fact_hyperedge USING gin (evidence_pointer);
```

**浜у嚭**锛歅G 鏁版嵁搴?ready锛宺etrieval API锛圴2 鍐欙級鑳借窇锛?- 鍏抽敭瀛楁绱?鈫?GIN(entities) 鍛戒腑 passage / fact
- 璇箟妫€绱?鈫?HNSW(embedding) 鍙?top-k 娈?/ top-k 鑺傜偣
- 璺ㄥ眰 鈫?SQL JOIN by doc_id 鎶?fact + passage 瀵归綈灞曠ず
- 缁撴瀯鍖栬繃婊?鈫?BTree 绱㈠紩鎸?doc_id / example_id / property 鍒囩墖

**LLM 璋冪敤**锛氭棤锛涙湰鍦?BGE-M3 绠?embedding锛坧assage 200 脳 348 鈮?7 涓囨锛孊GE-M3 鍦?A100 涓?~500 娈?绉掞紝绾?2.5 鍒嗛挓璺戝畬鎵€鏈夋钀?embedding锛?
**澶栭儴渚濊禆**锛?- PostgreSQL 16
- `pgvector` extension锛堢紪璇戣 `CREATE EXTENSION vector;`锛?- 鏈湴 BGE-M3 妯″瀷锛圚uggingFace `BAAI/bge-m3`锛?.6 GB safetensors锛?
---

## 6. V1.2.5 鈫?V2.0.5 杩佺Щ娓呭崟

鎸変紭鍏堢骇鎺掑垪锛堝厛璺戦€氱殑浼樺厛锛夛細

| 浼樺厛绾?| 鏀瑰姩 | 娑夊強浠ｇ爜 | 棰勮宸ヤ綔閲?|
|--------|------|---------|-----------|
| P0 | stage 0.5 / 4 / 6 / 7 鍒囨湰鍦?vLLM | 4 涓?client 鐨?base_url / model 鍚?+ 鍏?thinking | 1 澶?|
| P0 | vLLM 鍚姩鍙傛暟鍥哄寲锛坄max-model-len 8192`, `max-num-seqs 32`, `--guided-decoding-backend outlines`锛?| `scripts/start_vllm.sh` | 0.5 澶?|
| **P0** | **Stage 4.5 unit router 涓夎矾鍒嗘祦钀藉湴** 猸?| `pipeline/unit_router.py` 鏂板缓 + `cli.py:185` 鎺ョ嚎 + audit CSV | **1.5 澶?* |
| P1 | Stage 9.5 瀹炶 single per-doc LLM 娴佺▼ + FigureHyperedge | `pipeline/passage_extractor.py` 閲嶅啓鏀寔 passages + figures 鍙?list | 2 澶?|
| P1 | 11 绡?golden set 鍥炲綊娴嬭瘯 | 璺戝墠鍚?fact 鏁?/ canonical 鍛戒腑鏁板姣?| 0.5 澶?|
| P2 | Stage 10 KG loader 瀹炶 | `scripts/load_facts_to_pg.py` + 5 寮犺〃 schema migration | 2 澶?|
| P2 | BGE-M3 璺?vLLM 鍏遍┗ A100 + retrieval API skeleton | `scripts/start_bge.sh` + FastAPI | 2 澶?|
| P3 | propose-and-curate 浜哄鐣岄潰锛圫treamlit锛?| `scripts/curate_ui.py` | 2 澶?|

**鎬诲伐浣滈噺**锛殈10 宸ヤ綔鏃ワ紙绾?2 鍛ㄥ彲婕旂ず V2.0.5锛夈€?
---

## 7. 绔埌绔椂闂?/ 鎴愭湰棰勭畻

### 7.1 V1.2.5锛堜簯 qwen-plus锛?
| 閲忕骇 | 鏃堕棿 | 鎴愭湰 |
|------|------|------|
| 鍗曠瘒 PDF | ~5 min + N脳3 min锛圢=unit 鏁帮級| ~楼0.5 |
| 348 绡?BASF 鍏ㄨ窇 | ~5 灏忔椂锛? 骞跺彂锛?| **~楼150-200** |

### 7.2 V2.0.5锛堟湰鍦?vLLM Qwen3.6-27B-AWQ + BGE-M3锛?
| 閲忕骇 | 鏃堕棿 | 鎴愭湰 |
|------|------|------|
| 鍗曠瘒 PDF | ~3 min锛堟湰鍦?32 骞跺彂鍚炲悙 ~1000 tok/s锛墊 **楼0**锛堢數璐瑰拷鐣ヤ笉璁★級 |
| 348 绡?BASF 鍏ㄨ窇 | **~2 灏忔椂**锛圓100 鍗曞崱璺戞弧锛墊 **楼0** |
| 澧為噺 Stage 9.5 | ~2 鍒嗛挓锛?48 PDF 脳 1 LLM 璋冪敤锛屾湰鍦?32 骞跺彂锛墊 **楼0** |
| 澧為噺 Stage 10 KG load | ~3 鍒嗛挓锛坧g copy + HNSW 寤虹储寮曪級| **楼0** |

**ROI 鎷愮偣**锛氳窇杩?~600 绡?PDF 鍚庯紝浜?API 鎴愭湰灏辫拷骞?A100 涓€骞寸殑鎶樻棫銆侭ASF 涓€瀹?348 绡?+ 鍚庣画澶氬鍏徃璇枡 鈫?鏈湴鎺ㄧ悊鍦?6 涓湀鍐呭洖鏈€?
---

## 8. 鏂囨。鐗堟湰

| 鐗堟湰 | 鏃ユ湡 | 涓昏鏀瑰姩 |
|------|------|---------|
| v1 | 2026-04-22 | 鍒濈锛?0 stage锛?|
| v2 | 2026-05-02 | 鍔?stage 0.5 IPC 闂搁棬 + V1.2.3 propose-and-curate |
| v3 | 2026-05-06 | stage 1.5 鈫?9.5 绉讳綅 + 鍒?`near_units` 瀛楁 |
| v4 | 2026-05-09 | Stage 9.5 鏀?single per-doc LLM锛堝垹 Tier A/B/C 鍒嗘。锛? 琛ュ叏 Stage 10 + 鍔犺縼绉绘竻鍗?|
| **v5** | **2026-05-10** | **鍔?Stage 4.5 unit router 涓夎矾鍒嗘祦 + 鍔?Layer 1 FigureHyperedge schema锛坲nit_id/page/image_path 涓夊瓧娈碉級+ 鏂板 搂3 璺ㄥ眰鏄犲皠浣撶郴锛圴isual Evidence Pointer锛?* |
