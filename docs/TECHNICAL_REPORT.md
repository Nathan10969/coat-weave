# Coating Patent Knowledge Graph 鈥?鎶€鏈姤鍛?v1.0

> **鑼冨洿**锛欱ASF 348 绡?PCT 娑傛枡涓撳埄鐨勪簨瀹炴娊鍙?+ 鐭ヨ瘑鍥捐氨鏋勫缓 + 闂瓟 retrieval銆?> **鐘舵€?*锛歏1 demo锛坵ide CSV 杈撳嚭锛墌80% 瀹屾垚锛沄2 KG retrieval 璁捐鍐荤粨锛孷2.0.5 姝ｅ湪钀藉湴銆?> **绠楀姏**锛氬崟鍗?A100 80G锛屾湰鍦?vLLM Qwen3.6-27B-AWQ + BGE-M3銆?> **鏂囨。鐗堟湰**锛歷1.0锛?026-05-10锛夈€?
---

## 鐩綍

1. [椤圭洰瀹氫綅 + 鏁翠綋鏋舵瀯](#1-椤圭洰瀹氫綅--鏁翠綋鏋舵瀯)
2. [涓夊眰瓒呭浘妗嗘灦](#2-涓夊眰瓒呭浘妗嗘灦)
3. [Pipeline 14 涓?Stage 璇﹁В](#3-pipeline-14-涓?stage-璇﹁В)
4. [Embedding 璁捐](#4-embedding-璁捐)
5. [鏁版嵁搴撹璁?鈥?PostgreSQL 16 + pgvector](#5-鏁版嵁搴撹璁?-postgresql-16--pgvector)
6. [Q&A 绔埌绔祦绋媇(#6-qa-绔埌绔祦绋?
7. [璺ㄥ眰鏄犲皠浣撶郴锛圴isual Evidence Pointer锛塢(#7-璺ㄥ眰鏄犲皠浣撶郴visual-evidence-pointer)
8. [璺嚎鍥撅細V1 demo / V2 KG / V3 production](#8-璺嚎鍥緑1-demo--v2-kg--v3-production)
9. [Agent Memory 璁捐锛圴2.5+ 璋冪爺涓級](#9-agent-memory-璁捐v25-璋冪爺涓?
10. [鍩哄骇妯″瀷鎺ㄧ悊鍔犻€燂紙V2.0 vLLM 閮ㄧ讲鏃跺惎鐢級](#10-鍩哄骇妯″瀷鎺ㄧ悊鍔犻€焩20-vllm-閮ㄧ讲鏃跺惎鐢? 猸?鏂板
11. [闄勫綍锛氭湳璇?/ 鍏抽敭鍐崇瓥 / spec 鍋忕璁板綍](#11-闄勫綍鏈--鍏抽敭鍐崇瓥--spec-鍋忕璁板綍)

---

## 1. 椤圭洰瀹氫綅 + 鏁翠綋鏋舵瀯

### 1.1 鎴戜滑鍦ㄥ仛浠€涔?
鎶?BASF 鍦?2020-2026 骞村叕寮€鐨?348 绡?PCT 娑傛枡涓撳埄锛?*浠庨潪缁撴瀯鍖?PDF 杞垚鍙煡璇㈢殑鐭ヨ瘑鍥捐氨**锛?
- **V1 demo 杈撳嚭**锛氫竴浠?wide CSV 鈥?姣忚涓€涓?Example 脳 29 鍒楋紙鍚厤鏂广€佸伐鑹恒€佹€ц兘銆佹祴璇曟潯浠躲€乸olarity銆佺疆淇″害锛夛紝缁欐潗鏂欏伐绋嬪笀鎵竴鐪兼暣浣撴牸灞€
- **V2 KG retrieval API**锛氳緭鍏ヨ嚜鐒惰瑷€闂锛?PU 绫绘竻婕嗘湁浠€涔?silane 鏀规€ф柟妗?锛夛紝杩斿洖缁撴瀯鍖栫瓟妗?+ 鍘?PDF 璺宠浆 + 鍙鍖栬瘉鎹?- **V3锛堣繙鏈燂級**锛氳法瀹舵棌锛圓kzo / PPG / 绔嬮偊锛夋墿閲忓埌 ~10000 绡?
### 1.2 鏁翠綋鏁版嵁娴?
```
[鍘?PDF]
    鈫?Stage 1 (MinerU 鐗堥潰瑙ｆ瀽)
[content_list.json + figures + tables]
    鈫?Stage 0.5 (鍏冩暟鎹?+ IPC 闂搁棬 鈥?闈炴秱鏂?reject)
[patent_meta.json锛岀‘璁ゆ槸娑傛枡涓撳埄]
    鈫?Stage 2/3 (鍒?Examples 鍐?figure/table 鍗曞厓 + 鐗╁寲鍒扮鐩?
[data/units/<unit_id>/  鍚?image.png / table.html / caption.txt]
    鈫?Stage 4 (VLM 鐪嬪浘鐪嬭〃)
[vlm_description.json 鈥?鍚?description + entities + subtype]
    鈫?Stage 4.5 (unit router 涓夎矾鍒嗘祦)
    鈹?    鈹溾攢 extract_facts (50%) 鈹€鈹€鈫?Stage 5/6/7/8 鈹€鈹€鈫?Layer 2 FactHyperedge
    鈹?                                             (瀹氶噺 fact JSON)
    鈹?    鈹溾攢 register_layer1 (45%) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈫?Layer 1 FigureHyperedge
    鈹?                                             (鍙嶅簲寮?娴佺▼鍥?SEM 绛?
    鈹?    鈹斺攢 delete (5%) 鈹€鈹€鈫?soft-move 鍒?_routed_out/ (涓嶅叆鍥?

骞惰锛堜笉渚濊禆 stage 4-8锛夛細
    鈫?Stage 9.5 (passage_extractor 鈥?鏁寸瘒 NER)
[Layer 1 PassageHyperedge 鈥?娈佃惤鍘熸枃 + 瀹炰綋鍏辩幇]

姹囨€伙細
    鈫?Stage 9 (build_coatings_csv.py)
[output/coatings_wide.csv 鈥?V1 demo 涓讳骇鍑篯

    鈫?Stage 10 (V2 寰呭啓 鈥?KG loader)
[PostgreSQL 7+ 寮犺〃 + pgvector HNSW 绱㈠紩]
```

### 1.3 涓変釜鏍稿績鏋舵瀯鍐崇瓥

**鍐崇瓥 1 鈥?fact-as-hyperedge锛屼笉鐢?RDF 涓夊厓缁?*锛氭瘡鏉?fact 鏄竴涓?**澶氭Ы浣?JSON**锛?9+1 瀛楁锛夛紝鍚屾椂杩炴帴 application + property + materials + process + test_method + result_value 澶氫釜鑺傜偣銆傚師鍥狅細娑傛枡 fact 鏈川鏄€屽湪 application 涓嬶紝缁?resin + additives 閰嶆柟锛屾寜 process 宸ヨ壓鍋氾紝鐢?test_method 娴嬪嚭 result_value銆嶇殑 N 鍏冨叧绯伙紝寮鸿闄嶄负浜屽厓 RDF 浼氫涪淇℃伅銆?
**鍐崇瓥 2 鈥?涓ゅ眰瓒呭浘锛圠ayer 1 寮卞彫鍥?+ Layer 2 绮剧‘璇佹嵁锛?*锛?- Layer 2 fact 鍛戒腑 = 瀹氶噺璇佹嵁锛岃繘 Evidence Pack 褰?grounded evidence
- 鍙?Layer 1 鍛戒腑 = 娈佃惤/鍥捐〃璇佹嵁锛?*浠呭睍绀猴紝涓嶅綋 grounded evidence**锛屾爣娉ㄣ€岃瘹瀹為檷绾с€?- 閮芥病鍛戒腑 = 璇氬疄鎷掔瓟锛屼笉鑳＄紪

**鍐崇瓥 3 鈥?鍗曚竴 PostgreSQL 16 + pgvector**锛氫笉鍒嗗叧绯诲簱 + 鍚戦噺搴撱€備竴鏉?SQL 鍚屾椂璺戠簿纭繃婊?+ JSONB 鍖呭惈 + 鏁板€兼帓搴?+ 鍚戦噺鐩镐技搴︺€傝瑙?搂5銆?
---

## 2. 涓夊眰瓒呭浘妗嗘灦

### 2.1 Layer 0 鈥?鑺傜偣灞傦紙canonical 瀹炰綋锛?
**瀹氫綅**锛氭墍鏈?hyperedge 寮曠敤鐨?鍩虹鍘熷瓙"銆傛瘡涓妭鐐瑰搴斾竴涓?canonical_id锛堝 `MAT_polyurethane_resin`锛夛紝鏈夊埆鍚嶅瓙鍥俱€乫orbidden_merge / must_merge 绾︽潫銆乸olarity directionality 绛夈€?
**鑺傜偣 7 澶х被**锛堟潵鑷?`02_ontology_v0.1.md`锛夛細

| 鍓嶇紑 | 绫诲埆 | 绀轰緥 |
|------|------|------|
| `MAT_` | Material | `MAT_polyurethane`, `MAT_HDI_trimer`, `MAT_Tinuvin_292` |
| `PROP_` | Property | `PROP_gloss_retention_after_amtec`, `PROP_adhesion_cross_cut` |
| `APP_` | Application | `APP_automotive_oem_clearcoat`, `APP_marine_protective` |
| `PROC_` | Process | `PROC_spray_apply`, `PROC_uv_cure`, `PROC_ambient_cure` |
| `SUB_` | Substrate | `SUB_aluminum`, `SUB_concrete`, `SUB_steel` |
| `TEST_` | TestMethod | `TEST_ISO_2409`, `TEST_ASTM_D3359` |
| `COMP_` | Composition slot | `COMP_resin_system`, `COMP_pigment` |

V1.2.5 宸叉湁 ~150 canonical锛堟潵鑷?ontology seed锛夛紱V2 璺戝畬 348 绡囦細琛?~50 propose-and-curate 鏂拌妭鐐广€?
**瀛楁**锛坄nodes` 琛級锛?
```sql
CREATE TABLE nodes (
    canonical_id TEXT PRIMARY KEY,           -- e.g. 'MAT_polyurethane'
    type TEXT,                                -- 'MAT' / 'PROP' / 'APP' / 'PROC' / 'SUB' / 'TEST' / 'COMP'
    label TEXT,                               -- 'polyurethane resin' (灞曠ず鐢?
    aliases JSONB,                            -- ['PU', '鑱氭皑閰?, 'polyurethane resin']
    description TEXT,                         -- 鑷劧璇█鎻忚堪锛堢粰 embedding 鐢級
    polarity_directionality TEXT,             -- only for PROP_*: 'higher_is_better' / 'lower_is_better' / 'depends'
    status TEXT,                              -- 'approved' / 'proposed' / 'rejected'
    scope TEXT,                               -- 'generic' / 'patent_local'
    name_embedding VECTOR(1024)               -- 鈽?BGE-M3 绠楃殑璇箟鍚戦噺
);
CREATE INDEX ON nodes (type, canonical_id);
CREATE INDEX ON nodes USING hnsw (name_embedding vector_cosine_ops);
```

### 2.2 Layer 1 鈥?寮卞彫鍥炲眰

鐢?**涓ょ被 hyperedge** 缁勬垚锛?
#### 2.2.1 PassageHyperedge 鈥?娈佃惤瓒呰竟

**鏉ユ簮**锛歋tage 9.5锛坧assage_extractor锛夋暣绡囦笓鍒╁仛 NER 鎶藉嚭鏉ャ€?
**浣滅敤**锛氬綋 Layer 2 fact 娌″懡涓椂锛屽仛娈佃惤绾?fallback銆傛瘮濡傜敤鎴烽棶銆孭U 绫绘竻婕嗛噷 silane 鏀规€х殑鎬濊矾鏈夊摢浜涖€嶏紝Layer 2 娌℃湁缁撴瀯鍖?fact锛屼絾 description / claims 娈佃惤閲屾湁澶ч噺鎻忚堪鎬ф彁鍙婏紝闈?PassageHyperedge 鍏滃簳銆?
**瀛楁**锛坄passages` 琛級锛?
```sql
CREATE TABLE passages (
    passage_id TEXT PRIMARY KEY,              -- 'WO..._p18_b237' = doc_id + page + block_idx
    doc_id TEXT REFERENCES patents,
    page INT,
    section_type TEXT,                        -- 'DESCRIPTION' / 'CLAIMS' / 'EXAMPLES' / 'ABSTRACT'
    text TEXT,                                -- 娈佃惤鍘熸枃锛堚墹500 瀛楃鎴柇锛?    tagged_entities JSONB,                    -- canonical_ids 鏁扮粍锛歔"MAT_PUD", "MAT_silane", ...]
    entity_phrases JSONB,                     -- LLM 鎶藉嚭鐨?surface phrase 鈫?canonical_id 鏄犲皠
    confidence FLOAT,                         -- LLM 骞冲潎缃俊搴︼紙鐢ㄤ簬 rerank锛?    text_embedding VECTOR(1024)               -- 鈽?BGE-M3 娈佃惤鍘熸枃鍚戦噺
);
CREATE INDEX ON passages USING hnsw (text_embedding vector_cosine_ops);
CREATE INDEX ON passages USING gin (tagged_entities);
CREATE INDEX ON passages (doc_id, page);
```

#### 2.2.2 FigureHyperedge 鈥?鍥捐〃瓒呰竟

**鏉ユ簮**锛歋tage 4.5 璺敱涓?`register_layer1` 鐨?unit锛堝弽搴斿紡 / 娴佺▼鍥?/ SEM / 璁惧鍥剧瓑鏃犲畾閲忔暟鎹絾鏈夎瑙変环鍊肩殑鍥撅級銆?
**浣滅敤**锛歳etrieval 鏃跺鏋滃懡涓紙濡傜敤鎴烽棶銆孊ASF 寮傛鞍閰搁叝鍙嶅簲鍘熺悊銆嶏級锛岃繑鍥炲師鍥?+ VLM 鎻忚堪 + reference numerals 鏍囧彿銆傝繖鏄?v1 demo 澶氭ā鎬佽兘鍔涚殑鏍稿績 鈥?鑰佹澘鐪嬪埌銆岄棶鍙嶅簲鍘熺悊 鈫?鐩存帴鍑哄浘銆嶄細瑙夊緱闇囨捈銆?
**瀛楁**锛圴2.0.5 鎻愯鍚堝苟鍒?figure_table_units 琛?+ `route` 鍒楋紱杩欓噷灞曠ず閫昏緫瑙嗗浘 schema锛夛細

```sql
CREATE VIEW figures AS
SELECT
    'FIG_' || unit_id AS figure_id,
    unit_id,
    doc_id,
    page,
    figure_subtype AS subtype,                -- 'scheme' / 'plot' / 'sem' / 'apparatus' / 'structure' / 'other'
    image_path,
    vlm_description,
    caption_footnote_text AS caption,
    tagged_entities,
    reference_numerals,                       -- [{"numeral": "101", "label": "polyacrylate"}, ...]
    description_embedding,
    0.9 AS confidence
FROM figure_table_units
WHERE route = 'register_layer1';
```

### 2.3 Layer 2 鈥?绮剧‘璇佹嵁灞傦紙FactHyperedge锛?
**鏉ユ簮**锛歋tage 7锛坒act_extractor锛変粠 table HTML + matched paragraphs 鎶藉嚭銆?
**浣滅敤**锛氬敮涓€杩?Evidence Pack 鐨?grounded evidence 褰㈠紡銆俽etrieval 鍛戒腑鍚庣敤 `evidence_pointer` 璺冲洖 PDF 鍘?cell + bbox銆?
**瀛楁**锛坄facts` 琛級锛?
```sql
CREATE TABLE facts (
    fact_id TEXT PRIMARY KEY,                 -- 'F_<unit_id>_<seq:03d>'
    doc_id TEXT REFERENCES patents,
    example_id TEXT,                          -- 'A1B1' / 'C1' / 'I1'

    -- 涓昏涔夋Ы浣?    application TEXT REFERENCES nodes,        -- 'APP_automotive_oem_clearcoat'
    property TEXT REFERENCES nodes,           -- 'PROP_adhesion_cross_cut'
    polarity_hint TEXT,                       -- 'positive' (inventive) / 'negative' (comparative) / 'unknown'
    comparison_group TEXT,                    -- 鍚岃〃鍐呭彲瀵规瘮 fact 鐨?group_id

    -- 浜嬪疄鏁版嵁
    result_value FLOAT,                       -- 鏁板€?(鐢ㄤ簬 ORDER BY)
    result_value_text TEXT,                   -- '5B' / 'pass' / '0.3 渭m' (瀹氭€?fallback)
    test_condition JSONB,                    -- {film_thickness: '45-55 渭m', ...}

    -- 閰嶆柟涓庡伐鑹?    resin_system TEXT REFERENCES nodes,       -- 'MAT_COMPONENT_A1'
    additives JSONB,                          -- ['MAT_Tinuvin_292', 'MAT_HALS_770']
    process JSONB,                            -- [{"step": "PROC_spray_apply", "condition": "..."}, ...]
    test_method TEXT REFERENCES nodes,        -- 'TEST_ISO_2409'

    -- 璇佹嵁鎸囬拡锛堣法灞傛槧灏勪富閿級
    evidence_pointer JSONB,                   -- {doc_id, page, region_id, row, column, cell, bbox, unit_id}
    evidence_text TEXT,                       -- caption + matched paragraph 鎽樿
    extraction_confidence FLOAT,
    source_section_type TEXT,                 -- 'TABLE' / 'FIGURE_CAPTION' / 'TABLE_FOOTNOTE'

    -- embedding
    evidence_embedding VECTOR(1024)           -- 鈽?BGE-M3 evidence_text 鍚戦噺
);
CREATE INDEX ON facts (doc_id, example_id);
CREATE INDEX ON facts (application, property, polarity_hint);
CREATE INDEX ON facts USING hnsw (evidence_embedding vector_cosine_ops);
CREATE INDEX ON facts USING gin (evidence_pointer);
CREATE INDEX ON facts USING gin (additives);
```

### 2.4 涓夊眰鍏崇郴灏忕粨

```
Layer 0 (canonical 鑺傜偣)
   鈫?寮曠敤
   鈹?Layer 1 (寮卞彫鍥?         Layer 2 (绮剧‘璇佹嵁)
   鈹溾攢 Passage              鈹斺攢 Fact
   鈹? 鈥?text                  鈥?result_value
   鈹? 鈥?tagged_entities       鈥?application/property
   鈹?                         鈥?evidence_pointer 鈫?unit
   鈹斺攢 Figure
      鈥?image_path            鈫?      鈥?vlm_description     瑙嗚璇佹嵁 JOIN
      鈥?tagged_entities    figure_table_units
                          (image / table / bbox)
```

**retrieval 鏃?*锛?- Layer 2 鍛戒腑 鈫?鍙?fact + JOIN figure_table_units 鎷垮師 cell 鍥?- 鍙?Layer 1 鍛戒腑 鈫?娈佃惤鍘熸枃 / 鍘熷浘锛屾爣"璇氬疄闄嶇骇"
- 璺?doc 鑱氬悎 鈫?鎸?entities 鍏变韩 canonical_id锛堜笉鏄?unit_id锛夊仛 JOIN

---

## 3. Pipeline 14 涓?Stage 璇﹁В

鏁翠釜 pipeline 鐢?**14 涓?stage** 缁勬垚銆傜矑搴﹀垎涓夋。锛?*per-PDF**锛堟瘡绡囦笓鍒╄窇 1 娆★級銆?*per-unit**锛堟瘡寮犲浘/琛ㄨ窇 1 娆★級銆?*cross-PDF**锛堟墍鏈?PDF 璺戝畬鍚庤窇 1 娆★級銆?
### Stage 0 鈥?缂栨帓锛圤rchestration锛?
**绮掑害**锛歱er-PDF
**涓讳唬鐮?*锛?- `src/coating_kg/cli.py:ingest_cmd`锛坙ine 26-300锛屽崟 PDF 鍏ュ彛锛?- `scripts/run_pipeline.py:main`锛堝 PDF 鎵归噺椹卞姩锛?
**鑱岃矗**锛氭妸 stage 1鈫?.5 涓茶捣鏉ワ紝鍋氶敊璇殧绂?+ 杩涘害鏃ュ織銆?
**浜溂璁捐**锛?1. **涓夌杩愯妯″紡**锛氶粯璁ゅ叏璺戯紙鍚?DB 鍐欏叆锛夈€乣--skip-db`锛圥oC 妯″紡涓嶈繘 DB锛屾墍鏈変骇鐗╄惤纾佺洏锛夈€乣--dry-run`锛堝彧璺?stage 1-3锛屼笉璋?LLM锛?2. **閿欒闅旂涓夊眰绮掑害**锛氬崟 stage warn / 鍗?unit rollback / 鏁寸瘒 PDF return 鈥?閬垮厤涓€涓?unit 鍧忔帀姹℃煋鏁存壒
3. **`nullcontext(None)` 璁?PoC 鍜岀敓浜у叡鐢ㄤ唬鐮?*锛欴B 妯″紡 `with get_conn() as conn`锛孭oC 妯″紡 `with nullcontext(None) as conn`锛屼笅娓?if conn is not None 鍒ゆ柇鏄惁璧?DB 璺緞锛屾棤闇€鍙屼唤浠ｇ爜

**LLM 璋冪敤**锛氭棤锛堢紪鎺掑眰锛?
---

### Stage 1 鈥?MinerU 鐗堥潰瑙ｆ瀽

**绮掑害**锛歱er-PDF
**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/pdf_layout.py:parse_pdf`锛堜簯 API 璺緞锛?- `scripts/a100_stages_0_3/01_batch_mineru.py:run_mineru_for_pdf`锛堟湰鍦?6 璺?GPU 骞跺彂锛?
**鑱岃矗**锛氭妸 PDF 瑙ｆ瀽鎴愮粨鏋勫寲 JSON锛屾瘡椤佃嫢骞?blocks锛坱ype 鍚?text / title / image / table / equation锛屾瘡涓甫 bbox + page_idx锛夛紝骞舵妸 figure 鍒囨垚 PNG銆乼able OCR 鎴?HTML銆?
**浜溂璁捐**锛?1. **鏈湴 6 璺?GPU 骞跺彂**锛欰100 鍗曞崱璺?6 涓?mineru 杩涚▼锛?2GB 鏄惧瓨婊¤浇锛屽悶鍚愰噺 ~30 PDF/灏忔椂
2. **缂撳瓨鏈哄埗**锛歚_load_layout_json` 妫€鏌ュ凡鏈?content_list.json锛屾湰鍦拌窇瀹屽悗浜戣矾寰勪笉閲嶅瑙﹀彂
3. **bbox 鍏ㄧ▼閫忎紶**锛氫粠 stage 1 鐨?block 绾?bbox 鈫?stage 7 fact 鐨?evidence_pointer.bbox锛?*璁╂渶缁堢瓟妗堣兘绮剧‘鐢绘鍑?PDF 鍝竴鏍?*

**LLM 璋冪敤**锛歁inerU 鍐呴儴鐢?doclayout-yolo + ViT锛屼絾杩欐槸 MinerU 鑷繁鐨勪簨锛屼笉绠楁垜浠殑 LLM stage銆?
---

### Stage 0.5 鈥?鍏冩暟鎹?+ IPC 闂搁棬 猸?
**绮掑害**锛歱er-PDF
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/patent_metadata_extractor.py`锛堢害 360 琛岋級

**鑱岃矗**锛氫粠棣栭〉鏂囨湰鎶?title / IPC / applicant / abstract / filing_date锛屽苟璺?4 淇″彿 `is_coating_patent` 鍒嗙被鍣ㄣ€?*闈炴秱鏂欎笓鍒╁湪姝?return锛岀渷鎺?stage 4-7 鐨?LLM 閽?*銆?
**浜溂璁捐**锛?1. **4 淇″彿 IPC 鍒嗙被鍣?*锛坙ine 196 `is_coating_patent()`锛夛細
   - 淇″彿 鈶?鍙嶅叧閿瘝榛戝悕鍗曪紙CO2 / amine / detergent / agrochemical 绛?13 绫伙級鈫?鐩存帴 reject
   - 淇″彿 鈶?涓?IPC 鏄?C09D 绯诲垪 鈫?鐩存帴 accept
   - 淇″彿 鈶?鏍囬鍚秱鏂欏叧閿瘝 鈫?accept
   - 淇″彿 鈶?鍓?IPC + abstract 鑱斿悎鍒?鈫?defer 鐢?LLM 鍐崇瓥
2. **鏃╂湡闂搁棬 = 榛樿寮€闂?*锛坄meta.get("is_coating_patent", True)`锛夆€?瀹归敊浼樺厛锛氭娊鍙栧け璐ユ椂涓?kill 鏁存壒锛岃蒋閫€鍖栧埌榛樿閫氳繃
3. **section_split mode 鏍囪**锛氬湪 patent_meta 閲岃 'found' / 'fallback'锛岃鎵硅窇鍙娴嬪摢浜涗笓鍒╃殑 Examples 绔犺妭瀹氫綅澶辫触锛屽悗缁?audit
4. **鐪侀挶鏁堢泭**锛?48 绡?BASF 瀹炴祴 ~50% 鏄潪娑傛枡锛堣〃闈㈡椿鎬у墏銆佸啘鍖栥€佸鏂欏洖鏀讹級锛屾棭鏈?reject **鐪?~楼87** + 鍑忓皯涓€鍗?LLM 璋冪敤鏃堕棿

**LLM 璋冪敤**锛欴ashScope qwen-plus锛屾瘡 PDF 涓€娆★紙V2 鍒囨湰鍦?vLLM Qwen3.6-27B-AWQ锛夈€?
---

### Stage 2 鈥?unit 鍒囧垎

**绮掑害**锛歱er-PDF锛坧er-unit 杈撳嚭锛?**涓讳唬鐮?*锛歚src/coating_kg/pipeline/unit_extractor.py:iter_figure_table_units`

**鑱岃矗**锛氫粠 layout.content_list 閲屾寫鍑?type=figure / table 涓?page_idx 钀藉湪 Examples 绔犺妭鍐呯殑 block锛屾瀯閫?`FigureTableUnit` Python 瀵硅薄銆?
**浜溂璁捐**锛?1. **`examples_only=True` 鏄‖绾︽潫**锛歏1.2.5 璁捐鍐荤粨锛孡ayer 2 fact 鍙潵鑷?Examples 绔犺妭鍐呯殑 figure/table銆傝儗鏅?/ claims / 鎽樿閲岀殑鍥捐〃涓嶆娊锛堥噺澶ぇ鍣０澶氾紝鍙堜笉鍦ㄨ€佹澘瑕佺殑 demo 鑼冨洿鍐咃級
2. **caption 缁戝畾灏卞湪 stage 2 瀹屾垚**锛歚caption_footnote_text` 瀛楁宸茬粡鎶?figure / table 鍛ㄥ洿鐨?caption + footnote 鏂囨湰鎷煎ソ缁戝埌 unit 涓婏紝涓嬫父 stage 3 鐗╁寲鏃剁洿鎺ュ啓鐩?3. **`region_id` 鎸佷箙鍖?*锛歁inerU 缁欐瘡涓?figure/table 涓€涓法椤?region_id锛屽悗缁?stage 7 鐢ㄥ畠鍒ゆ柇 fact 鏉ヨ嚜鍝紶琛紝stage 8 comparison_group 涔熸寜 region_id 鍒嗙粍

**LLM 璋冪敤**锛氭棤锛堢函瑙勫垯锛?
---

### Stage 3 鈥?unit 鐗╁寲

**绮掑害**锛歱er-unit
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/unit_materializer.py:materialize_unit`

**鑱岃矗**锛氭妸 in-memory 鐨?FigureTableUnit 瀵硅薄鍐欏埌纾佺洏锛屾瘡涓?unit 涓€涓嫭绔嬬洰褰?`data/units/<unit_id>/`锛屽啓 4 绫绘枃浠讹細
- `meta.json` 鈥?unit 鍏冩暟鎹?- `caption.txt` 鈥?caption + footnote 鏂囨湰
- `image.png` (figure) 鈥?浠?MinerU 杈撳嚭澶嶅埗
- `table.html` (table) 鈥?琛ㄦ牸 HTML 搴忓垪鍖?
**浜溂璁捐**锛?1. **鍏变韩宸ヤ綔鐩綍**锛氭瘡涓?unit 鍏ㄩ儴杈撳叆/杈撳嚭閮借惤鍚屼竴涓洰褰曪紝涓嬫父 stage 4/5/6/7/8 鎶婁骇鐗╄拷鍔犲埌杩欓噷锛坴lm_description.json, matched_paragraphs.json, facts.json, coverage.json, proposed_canonicals.json锛夆€?**涓€涓?unit 鍏ㄩ儴鐣欑棔鍦ㄤ竴澶?*锛岃皟璇?prompt 鏃剁洿鎺?grep
2. **fail-fast 绛栫暐**锛氱墿鍖栧け璐?propagate up锛堟棤 try/except锛夛紝纾佺洏閿欒灞炰簬鍩虹璁炬柦澶辫触锛屽簲绔嬪嵆鍋滆€屼笉鏄悶寮傚父寰€鍚庤窇
3. **閲嶈窇瀹夊叏**锛歚if folder.exists(): shutil.rmtree(folder)` 鎬昏鐩栵紝閲嶈窇浜у嚭骞插噣蹇収

**LLM 璋冪敤**锛氭棤锛堢函纾佺洏 IO锛?
---

### Stage 4 鈥?VLM 鐪嬪浘鐪嬭〃

**绮掑害**锛歱er-unit
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/vlm_describe.py:QwenVLClient`

**鑱岃矗**锛氭瘡涓?unit 璋?VLM/LLM 鐢熸垚鑷劧璇█鎻忚堪 + 璇嗗埆宸茬煡瀹炰綋 + 鍒嗙被 subtype銆?
**浜溂璁捐**锛?1. **figure / table 鍙岃矾寰?*锛歠igure 璧?qwen-vl-plus锛堝妯℃€侊紝杈撳叆 base64 鍥撅級銆乼able 璧?qwen-plus锛堢函鏂囨湰锛岃緭鍏?OCR 鍚庣殑 HTML锛夈€傚墠鑰呮瘡 figure 楼0.05锛屽悗鑰呮瘡 table 楼0.005锛?*鐪?90% VLM 璋冪敤閽?*
2. **闂泦 subtype 寮虹害鏉?*锛歚vlm_figure.txt` prompt 瑕佹眰 subtype 蹇呴』浠?6 绫婚棴闆嗗嚭锛坰tructure/scheme/plot/sem/apparatus/other锛夛紝璁?stage 4.5 璺敱鑳藉彲闈犲喅绛?3. **reference_numerals 鎻愬墠鎶?*锛坄vlm_figure.txt:11-14`锛夛細璁?VLM 椤烘墜鎶藉嚭鍥句笂鐨勬爣鍙?+ label锛?101 鈫?polyacrylate"锛夛紝retrieval 鏃跺墠绔彲鐩存帴鍦ㄥ浘涓婄敾鏍囧彿
4. **tenacity 4 娆?retry**锛氱綉缁滄姈鍔ㄤ笅鑷姩鎸囨暟閫€閬?+ 瑙ｆ瀽澶辫触 raise VLMError 瑙﹀彂 retry

**LLM 璋冪敤**锛欴ashScope qwen-vl-plus锛坒igure锛? qwen-plus锛坱able锛夛紝姣?unit 涓€娆°€俈2 鍒囨湰鍦?vLLM Qwen3.6-27B-AWQ锛堝悓瀹炰緥澶勭悊涓ょ 鈥?27B 鏄?VLM锛夈€?
---

### Stage 4.5 鈥?unit router锛堜笁璺垎娴侊級猸?V2.0.5 鏂板姞

**绮掑害**锛歱er-unit
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/unit_router.py`锛堢害 250 琛岋級

**鑱岃矗**锛氬熀浜?stage 4 宸茬粡浠樿繃閽辩殑 VLM 杈撳嚭鍋?*绾鍒欒矾鐢?*锛屾妸 unit 鍒嗘垚涓夎矾锛?- `extract_facts`锛垀50%锛夛細table 绫诲惈瀹氶噺鏁版嵁 鈫?缁х画璧?stage 5/6/7/8 鎶?fact
- `register_layer1`锛垀45%锛夛細figure 绫绘棤瀹氶噺鏁版嵁 鈫?璺宠繃 stage 6/7锛屾敞鍐?FigureHyperedge 杩?Layer 1
- `delete`锛垀5%锛夛細浣庝俊鎭啑浣欙紙logo / 瑁呴グ锛夆啋 **soft-move** 鍒?`data/units/_routed_out/<unit_id>/`锛圴1.2.7 鏀规垚鍙€嗭級

**浜溂璁捐**锛?1. **澶嶇敤 stage 4 杈撳嚭鍋氬厤璐瑰垎娴?*锛氭湰韬?0 LLM 璋冪敤锛屼絾鎴帀浜?~50% 鐨勪笅娓?stage 6/7 LLM 璋冪敤 鈥?surgical 浼樺寲
2. **VLM-fail fallback to extract_facts**锛圴1.2.7锛夛細VLM 澶辫触鏃朵笉 delete锛岃蛋 extract_facts 璁?stage 7 鐢?table_html + matched_paragraphs 鍏滃簳锛宲reserve recall
3. **soft-delete 鑰岄潪 rmtree**锛圴1.2.7锛夛細璺敱閿欒鍙仮澶嶏紝30 绉掑洖婊?vs rmtree 閲嶈窇鍏?PDF
4. **audit CSV 鍏ㄧ▼鐣欑棔**锛坄output/unit_routing_audit.csv`锛夛細姣忎釜璺敱鍐崇瓥璁板綍 unit_id / doc_id / route / reason / description_excerpt锛?*鎶芥牱 100 涓汉瀹￠獙璇?false-negative < 5%**
5. **闂泦 + 闃插尽鎬у厹搴?*锛歶nknown subtype 榛樿 register_layer1锛堜繚鐣欒瑙夎瘉鎹級锛寀nknown table_subject 涔熼粯璁?register_layer1锛?*涓嶄涪鏁版嵁鏄簳绾?*

**LLM 璋冪敤**锛氭棤锛堢函 Python if/else锛?
**鏀剁泭**锛?48 绡?脳 15 unit锛夛細褰撳墠 15660 娆?LLM 璋冪敤 鈫?鏀归€犲悗 7830 娆★紙鐪?50%锛夛紝A100 32 骞跺彂鎺ㄧ悊鏃堕棿 16 鍒嗛挓 鈫?8 鍒嗛挓銆?
---

### Stage 5 鈥?瀹炰綋鍏滃簳鎵撴爣

**绮掑害**锛歱er-unit
**涓讳唬鐮?*锛歚src/coating_kg/pipeline/entity_tagger.py:tag_entities`

**鑱岃矗**锛氬 VLM 娌¤瘑鍒嚭鐨勫凡鐭?canonical锛岀敤 aho-corasick 瀛楃涓?鍒悕鍖归厤琛ヤ竴閬撱€?
**浜溂璁捐**锛?1. **DB-aware**锛氬彧鍦?`conn is not None`锛堢敓浜фā寮忥級鎵嶈窇锛屽洜涓洪渶瑕佹煡 DB 鐨?alias 琛紱PoC 妯″紡锛坄--skip-db`锛夋暣娈?skip 涓嶆氮璐规椂闂?2. **璺?stage 9.5 Tier A 鍏辩敤鏈哄埗**锛歛ho-corasick 澶氭ā寮忓尮閰嶏紝鍖哄埆鍙槸浣滅敤瀵硅薄锛坲nit 绾?vs 娈佃惤绾э級
3. **瀹炴祴琛?~5-15% 婕忔娊**锛歏LM 鍥犱负 prompt token 闄愬埗缁忓父婕忔娊闀垮熬鍖栧鍝侊紝aho-corasick 鎶婂瓧鍏搁噷鏈夌殑銆乂LM 婕忕殑鍏ㄦ崱鍥炴潵

**LLM 璋冪敤**锛氭棤锛圖B 鏌ヨ + 瀛楃涓插尮閰嶏級

---

### Stage 6 鈥?娈佃惤鍖归厤锛圥oC-2锛?
**绮掑害**锛歱er-unit锛堜粎 extract_facts 璺敱锛?**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/paragraph_matcher.py:ParagraphMatcher.match`
- `src/coating_kg/pipeline/paragraph_extractor.py`
- `prompts/paragraph_match.txt`

**鑱岃矗**锛氫负姣忎釜 unit 浠?Examples 绔犺妭鐨?~200 娈甸噷閫?top-10 鏈€鐩稿叧鐨勬钀斤紝缁?stage 7 fact_extractor 褰撲笂涓嬫枃銆?
**浜溂璁捐**锛?1. **`top_k=10` 鏄疄娴嬬敎铚滅偣**锛? 涓嶅锛坧rompt 閲屼笂涓嬫枃澶杽锛夈€?0 澶锛坒act_extractor token 瓒咃級
2. **寮轰俊鍙蜂紭鍏堢骇**锛坧rompt 閲屽啓姝伙級锛?   - 浼樺厛绾?1锛氭钀芥槑纭紩鐢?region_label锛?Table 1" / "Fig. 7"锛?   - 浼樺厛绾?2锛氬悓 example_id锛?E1" / "C1"锛?   - 浼樺厛绾?3锛氭弿杩?preparation / conditions / measurement
   - **鏄庝护绂佹**锛氬彧鍏变韩閫氱敤璇嶏紙"coating" "polymer"锛夌殑娈佃惤涓嶇畻 match
3. **strict JSON output**锛歚response_format={"type": "json_object"}` + `{"matches": [...]}`锛屾柟渚?stage 7 鐩存帴娑堣垂
4. **score + reason 瀛楁**锛氭瘡涓?match 甯︾疆淇″害 + 涓€鍙ヨ瘽 reason锛?*璋?prompt 鏃朵汉瀹¤兘鐩存帴鐪嬪嚭鍝潯 match 閿欎簡**

**LLM 璋冪敤**锛欴ashScope qwen-plus锛屾瘡 unit 涓€娆★紙浠?extract_facts 璺敱锛夈€?
---

### Stage 7 鈥?fact 鎶藉彇锛圥oC-3锛夆瓙 鏈€璐?
**绮掑害**锛歱er-unit锛堜粎 extract_facts 璺敱锛?**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/fact_extractor.py:FactExtractor.extract`锛堢害 600 琛岋級
- `prompts/fact_extract.txt`

**鑱岃矗**锛氫粠 unit锛坱able HTML + caption + matched paragraphs + VLM description锛夋娊鍑?19+1 瀛楁鐨勭粨鏋勫寲 fact JSON list銆?
**浜溂璁捐**锛?1. **propose-and-curate 杞害鏉?*锛圴1.2.3 鍏抽敭鍐崇瓥锛夛細LLM 鎵句笉鍒板悎閫?canonical_id 鏃?*涓嶅己鍑?* ontology 宸叉湁 ID锛岃€屾槸 propose 鏂?ID 钀?sidecar `proposed_canonicals.json` 绛変汉瀹°€傚疄娴嬫妸 11 鏉″姪鍓備粠璇爣 `MAT_acrylic_resin` 鏁戝嚭鏉ャ€?2. **unit_context 鍏变韩瀛楁**锛圴1.2.3 Tier-0 淇锛夛細涔嬪墠姣忎釜 fact ~900 token 脳 36 fact 瓒呰繃 8192 max_tokens 鎴柇锛屾妸 application / process / test_condition 杩欎簺璺?fact 鍏变韩鐨勫瓧娈垫彁鍒?unit_context 娈碉紝姣忎釜 fact 鍙～宸紓瀛楁銆?*Table 2 cell 鎶藉彇鐜?33% (12/36) 鈫?100% (36/36)**
3. **fact_id 鍏ㄥ眬鍞竴绾﹀畾**锛歚F_<unit_id>_<seq:03d>`锛宲ipeline 绔己鍒惰鐩?LLM 杈撳嚭鐨?fact_id 闃插啿绐?4. **evidence_pointer 鍏ㄥ瓧娈?*锛氬惈 doc_id / page / region_id / row / column / cell / bbox / unit_id锛岃绛旀鑳界簿纭烦鍥?PDF 鍝竴鏍?+ 鐢绘
5. **coverage.json 钀界洏**锛圴1.2.5 Tier-0 Fix 3锛夛細鏍囨敞 `total_rows / rows_extracted / rows_skipped / truncated / completeness_score`锛岃 stage 9 鍖哄垎"鐪熸病鏈夋暟鎹?vs"鎶藉彇鎴柇"
6. **scope 瀛楁锛坓eneric / patent_local锛?*锛歏1 鍗犱綅锛孷2 杩?KG 鏃舵寜 scope 鍐冲畾瑕佷笉瑕佽法涓撳埄鍚堝苟

**LLM 璋冪敤**锛欴ashScope qwen-plus锛屾瘡 unit 涓€娆★紙杈撳叆 ~3K tokens锛岃緭鍑?~500-1500 tokens JSON facts锛夆€?鍗?unit 鏈€璐电殑涓€娈?~楼0.3-0.5銆?
---

### Stage 8 鈥?鏋佹€?+ comparison_group + (DB 鍐欏叆)

**绮掑害**锛歱er-unit
**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/polarity.py:classify_polarity`
- `src/coating_kg/pipeline/comparison_group.py:resolve_comparison_group`
- `src/coating_kg/db/insert.py:bulk_insert_facts`

**鑱岃矗**锛?- **8a 鏋佹€у悗澶勭悊**锛歀LM 涓嶇‘瀹氾紙"unknown"锛夋椂鐢ㄨ鍒欐帹 inventive / comparative
- **8b comparison_group 缁戝畾**锛氭妸鍚岃〃鍐呭彲瀵规瘮 fact 褰掑埌鍚屼竴涓?group_id锛堟寜 region_id + property + application锛?- **8c DB 鍐欏叆**锛歜ulk_insert_facts 杩?PG锛圥oC 妯″紡璺宠繃锛?
**浜溂璁捐**锛?1. **瑙勫垯鍙湪 LLM 涓嶇‘瀹氭椂琛ヤ綅**锛歀LM 涓婁笅鏂囨洿鍏紙鐪嬩簡 row label + caption锛夛紝浼樺厛鐢?LLM 鏋佹€э紱瑙勫垯鎸?row label 鍚?Comp/Reference/Vergleich"绛夊叧閿瘝鎺ㄦ柇锛岀粰 unknown 鐨勫厹搴?2. **per-unit 浜嬪姟**锛歭ine 232 `try` 鎶?figure_table_unit insert + facts insert 鏀惧悓涓€浜嬪姟锛?*鍧?unit 涓嶆薄鏌撴暣鎵?*
3. **facts.json 鎬绘槸钀界洏**锛欴B 妯″紡涔熷啓銆丳oC 妯″紡涔熷啓锛岃 stage 9 build_csv 鍦?PoC 妯″紡涔熻兘璺?
**LLM 璋冪敤**锛氭棤锛堝叏瑙勫垯锛?
---

### Stage 9 鈥?瀹借〃 CSV

**绮掑害**锛歝ross-PDF
**涓讳唬鐮?*锛歚scripts/build_coatings_csv.py`锛堢害 790 琛岋級

**鑱岃矗**锛氭妸鎵€鏈?PDF 鐨?facts.json 鑱氬悎鎴愪竴琛屼竴涓?(doc_id, example_id) 鐨?wide CSV锛屾瘡涓?sub_type 涓€鍒椼€?*杩欐槸 V1 demo 鐨勪富浜у嚭**銆?
**浜溂璁捐**锛?1. **wide table pivot**锛氫竴琛屼竴 Example 脳 29 鍒?鈥?姣忎釜 Material sub_type / Property sub_type / Process step / Test condition 鍚勫崰涓€鍒楋紝**鏉愭枡宸ョ▼甯堣兘鐩存帴鎵竴鐪兼暣浣撴牸灞€**
2. **`extraction_confidence_min/mean` 涓ゅ垪**锛圴1.2.5 鍔狅級锛氳 retrieval 绔仛"淇′换鍒嗙骇"锛屼綆缃俊搴?fact 杩?pending review queue
3. **qualitative result_value_text fallback**锛圴1.2.5锛夛細鏁板€间笉鍙В鏋愭椂锛?5B" "pass"锛変繚鐣欏師鏂囷紝CSV 涓嶄涪淇℃伅
4. **example_id 褰掍竴鍖?*锛圴1.2.4 鍔狅級锛氬鐞?"I1" / "Inv1" / "Example 1" / "瀹炴柦渚?1" 澶氬啓娉曞綊涓€

**LLM 璋冪敤**锛氭棤

---

### Stage 9.5 鈥?passage_extractor锛圠ayer 1 鍚堟垚鍣級猸?V2.0.5

**绮掑害**锛歱er-PDF锛堢嫭绔嬪垎鏀紝璺?stage 4-8 骞惰锛?**涓讳唬鐮?*锛?- `src/coating_kg/pipeline/passage_extractor.py`
- `scripts/run_passage_extractor.py`

**鑱岃矗**锛?- **A. 娈佃惤 NER**锛氭暣绡囦笓鍒╂墍鏈夋钀借窇 single-pass per-doc LLM NER 鈫?PassageHyperedge
- **B. 鏀?stage 4.5 鐨?figures**锛氳 `data/layer1/<doc>/pending_figures.jsonl` 鈫?FigureHyperedge
- **C. 鍚堝苟钀界洏**锛歚data/layer1/<doc>/layer1.json` 鍚?`passages` + `figures` 涓や釜 list

**浜溂璁捐**锛?1. **per-doc 鍗曟 LLM**锛圴1.2.6 redesign锛夛細浠庡師 per-passage 璋冪敤 119 娆?doc 鏀规垚鏁寸瘒 1 娆°€?*60脳 蹇€?8脳 渚垮疁**锛堜簯 楼2.4 鈫?楼0.05锛夛紝涓?LLM 鐪嬪叏鏂囦笂涓嬫枃姣旂湅瀛ょ珛娈佃惤鏇村噯锛堝尯鍒?"polyurethane" 鍦ㄤ笉鍚屾钀芥槸 resin / binder / 娑傚眰锛?2. **Tier A 闈欐€佸瓧鍏?+ Tier B per-doc LLM 浜掕ˉ**锛歍ier A 100% precision 0 鎴愭湰锛孴ier B 琛?4脳 鏇村鍙樹綋锛堝疄娴?101 vs 405 entities锛夈€備袱鑰呭懡涓悎骞?+ provenance 鏍囨敞锛宺etrieval 鏃跺彲鍒嗙骇淇′换
3. **conscious deviation from spec 搂淇 6**锛歴pec 褰撴椂瀹氫箟 Layer 1 "鏃?LLM"锛孷2.0.5 瀹炴祴鍚庢敼鐢?LLM銆傜悊鐢憋細A100 鏈湴鎺ㄧ悊 cost 鈮?0锛岃川閲忔彁鍗?4脳銆傝瑙?搂9 鍋忕璁板綍

**LLM 璋冪敤**锛歲wen-plus 1 娆?/ PDF锛圴2 鍒囨湰鍦?Qwen3.6-27B-AWQ锛夈€?
---

### Stage 10 鈥?KG 瑁呰浇 馃敎 V2.x 寰呭啓

**绮掑害**锛歝ross-PDF
**涓讳唬鐮?*锛歚scripts/load_facts_to_pg.py`锛堝緟寤猴級

**鑱岃矗**锛氭妸鎵€鏈?facts.json + layer1.json + ontology canonical_ids 鐏岃繘 PostgreSQL锛屽缓 7 寮犺〃 + HNSW/GIN/BTree 绱㈠紩銆?
**浜溂璁捐**锛堝緟钀藉湴锛夛細
1. **idempotent loader**锛氱敤 `INSERT ... ON CONFLICT DO UPDATE` + checksum 鍒楄閲嶈窇瀹夊叏
2. **鎵归噺 embedding**锛欱GE-M3 涓€娆℃€х畻鎵€鏈?nodes / passages / facts / figure_units 鐨?embedding锛坧gvector copy 鐏屽叆锛?3. **HNSW 鍚庡缓绱㈠紩**锛氬厛 INSERT 鍏ㄩ儴鏁版嵁锛屾渶鍚?`CREATE INDEX CONCURRENTLY` 寤?HNSW锛屾瘮杈规彃杈瑰缓蹇?5-10脳

**LLM 璋冪敤**锛氭棤锛堟湰鍦?BGE-M3 绠?embedding锛?
---

## 4. Embedding 璁捐

### 4.1 5 涓?embedding 瀛楁

| 琛?| 瀛楁 | embed 鍐呭 | 鐢ㄩ€?|
|----|------|-----------|------|
| `nodes` | `name_embedding` | label + aliases 鎷兼帴 | 鑷劧璇█ 鈫?canonical_id 鏄犲皠锛圦uery Parser 鐢級 |
| `passages` | `text_embedding` | 娈佃惤鍘熸枃锛堚墹500 瀛楃锛?| Layer 1 娈佃惤璇箟妫€绱?|
| `facts` | `evidence_embedding` | caption + matched paragraphs 鎽樿 + cell 鍛ㄥ洿鏂囨湰 | Layer 2 fact 璇箟妫€绱?+ 鏁板€?ORDER BY 鍚庣殑娆℃帓搴?|
| `figure_table_units` | `description_embedding` | VLM description + caption 鎷兼帴 | Layer 1 figure 璇箟妫€绱?|
| `patents` | `metadata_embedding`锛圴2.5 鍊欓€夛級| title + abstract + ipc_codes 鎷兼帴 | 妯＄硦鎵句笓鍒╋細"BASF 2024 鑰愬€欐秱鏂欎笓鍒? |

**涓轰粈涔堟槸 5 涓笉鏄?50 涓?*锛歟mbedding 鍙敤浜?鑷劧璇█鏌ヨ 鈫?鎵炬渶鐩稿叧 entity / passage / fact / figure"鐨勮涔夊尮閰嶃€?*缁撴瀯鍖栧瓧娈碉紙application銆乸roperty銆乸olarity銆乺esult_value锛?*鐢?BTree / GIN 绱㈠紩灏卞锛屽姞 embedding 鍙嶈€岀█閲婄簿纭€с€?
### 4.2 妯″瀷閫夊瀷

**涓婚€?BGE-M3锛圚uggingFace `BAAI/bge-m3`锛?*锛?- 1024 缁达紙pgvector HNSW 鍙嬪ソ缁村害锛?- 涓嫳鍙岃锛堟秱鏂欎笓鍒╂贩鏉備腑鑻辨湳璇紝BASF 涓撳埄閲屼笓涓氳瘝澶у鑻辨枃锛屼絾 ontology 娉ㄩ噴鍚腑鏂囷級
- 鏈湴 GPU 鎺ㄧ悊 ~500 娈?绉掞紙A100 80G 璺?vLLM 鍏遍┗锛?- 3.6 GB safetensors锛坴LLM Qwen3.6-27B-AWQ 鍗?19 GB锛孊GE-M3 鍗?~3 GB锛孉100 80G 浣?58 GB 瀹夊叏锛?
**澶囬€?Qwen3-Embedding-8B**锛?- 4096 缁达紙淇℃伅瀵嗗害楂橈級
- 鍚屾牱涓嫳鍙岃
- 浣?8B 姣?BGE-M3 澶?2脳 鏄惧瓨
- ~150 娈?绉掞紙鎱?3脳锛?
**缁撹**锛氶€?BGE-M3銆傝川閲忓樊璺濆湪娑傛枡 domain 涓?< 2%锛圔GE-M3 鍦?MTEB 涓枃鎺掑悕闈犲墠锛夛紝浣嗘樉瀛?+ 閫熷害浼樺娍鏄庢樉銆俀wen3-Embedding-8B 鐣欑粰 V3 production 闃舵鑰冭檻銆?
### 4.3 index 绛栫暐

```sql
-- pgvector HNSW 榛樿鍙傛暟锛堝鐢級
CREATE INDEX ON nodes USING hnsw (name_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
-- 妫€绱㈡椂 SET hnsw.ef_search = 40;
```

`m=16, ef_construction=64`锛氬湪 Recall@10 0.95+ 鐨勫悓鏃跺缓绱㈠紩鏃堕棿 < 5 鍒嗛挓锛?0 涓囧悜閲忕骇锛夈€?
---

## 5. 鏁版嵁搴撹璁?鈥?PostgreSQL 16 + pgvector

### 5.1 7+ 寮犳牳蹇冭〃

| 琛?| 琛屾暟浼扮畻锛?48 绡囷級| 涓昏瀛楁 | 绱㈠紩 |
|----|-------------------|----------|------|
| `patents` | 174锛圛PC 闂搁棬鍚庯級| doc_id, title, ipc_codes, applicant, filing_date, abstract | BTree(doc_id) |
| `nodes` | ~200 | canonical_id, type, label, aliases, name_embedding | BTree(type, canonical_id) + HNSW(name_embedding) |
| `aliases` | ~600 | alias TEXT, canonical_id REFERENCES nodes | BTree(lower(alias)) |
| `forbidden_merge` | ~58 | id_a, id_b | BTree(id_a, id_b) |
| `must_merge` | ~63 | id_a, id_b | BTree(id_a, id_b) |
| `figure_table_units` | ~5000锛?74 脳 15 脳 65% non-delete锛墊 unit_id, doc_id, unit_type, page, image_path, vlm_desc, route, ... | BTree(doc_id, page) + HNSW(description_embedding) + GIN(tagged_entities) |
| `facts` | ~10000锛?74 脳 15 脳 50% 脳 8 fact/unit锛墊 fact_id, doc_id, application, property, result_value, evidence_pointer, ... | BTree(doc_id, example_id) + BTree(application, property) + GIN(evidence_pointer) + GIN(additives) + HNSW(evidence_embedding) |
| `passages` | ~50000锛?74 脳 200 娈?脳 60% 鍚?entity锛墊 passage_id, doc_id, page, text, tagged_entities, text_embedding | HNSW(text_embedding) + GIN(tagged_entities) + BTree(doc_id, page) |
| `property_registry` | ~80 | property_id, unit, directionality, valid_range | BTree(property_id) |

**鏁版嵁瑙勬ā棰勪及**锛氱害 **6.5 涓囧悜閲?+ 7 涓囪缁撴瀯鍖栨暟鎹?*锛宲gvector HNSW 鍦ㄨ繖閲忕骇姣绾у搷搴斻€?*杩滄病鍒拌鎷嗕笓鐢?vector DB 鐨勮妯?*锛圡ilvus / Qdrant 璁捐鐩爣鏄嚎绾у悜閲忥級銆?
### 5.2 涓轰粈涔堝崟涓€ PostgreSQL + pgvector

**鏍稿績濂藉锛氫竴鏉?SQL 鍚屾椂璺?4 绫绘搷浣?*锛?
```sql
SELECT fact_id, result_value, evidence_text, evidence_pointer,
       1 - (evidence_embedding <=> $query_vec) AS sim_score
FROM facts
WHERE application = 'APP_automotive_clearcoat'             -- 鈶?绮剧‘绛夊€?(BTree)
  AND property   = 'PROP_gloss_retention_after_amtec'      -- 鈶?绮剧‘绛夊€?  AND polarity_hint = 'positive'                            -- 鈶?绮剧‘绛夊€?  AND additives @> '["MAT_HALS_Tinuvin_292"]'               -- 鈶?JSONB 鍖呭惈 (GIN)
  AND extraction_confidence >= 0.7                          -- 鈶?鑼冨洿杩囨护
ORDER BY result_value DESC,                                 -- 鈶?鏁板€兼帓搴?(BTree)
         evidence_embedding <=> $query_vec ASC              -- 鈶?鍚戦噺鐩镐技搴?(HNSW)
LIMIT 10;
```

**瀵规瘮涓撶敤鍚戦噺搴?*锛歁ilvus / Qdrant 鍙噦鍚戦噺 + 绠€鍗?metadata filter锛屽仛涓嶄簡 JSONB 鍖呭惈 / 鏁板€?ORDER BY / BTree 绮剧‘杩囨护銆傝鍦ㄥ悜閲忓簱澶栭潰鍐嶅啓 SQL 鎷夊叧绯绘暟鎹紝缁撴灉闆嗗悎骞讹紝宸ョ▼澶嶆潅搴︾炕鍊嶃€?
**ACID 浜嬪姟**锛歠act 鍐欏叆 + entities 鍐欏叆 + embedding 鍐欏叆鍘熷瓙鍖栥€備竴浠藉け璐ュ叏 rollback銆?
### 5.3 涓嶇敤浠€涔?
| 涓嶇敤 鉂?| 鍘熷洜 |
|---------|------|
| MySQL | JSONB / pgvector 閮戒笉濡?PG |
| Milvus / Qdrant / Weaviate | 涓撶敤鍚戦噺搴?鈥?缁欐垜浠繖閲忕骇鏄繃搴﹀伐绋嬶紝涓斿仛涓嶄簡 hybrid SQL |
| Elasticsearch | 閲嶏紝鍏ㄦ枃妫€绱笉闇€瑕?|
| Django / SQLAlchemy ORM | SQL 鐩存帴鍐欐洿濂芥帶锛汷RM 瀵?hybrid query / pgvector 鎿嶄綔鏀寔宸?|
| 涓ゅ搴擄紙鍏崇郴 + 鍚戦噺锛墊 澧炲姞杩愮淮澶嶆潅搴︼紝鏃犳敹鐩?|

---

## 6. Q&A 绔埌绔祦绋?
### 6.1 瀹屾暣 10 姝ユ祦绋?
```
[1] 鐢ㄦ埛闂锛堝惈浼氳瘽鍘嗗彶锛?        鈫?[2] Query Parser (LLM #1)
        鈫?Qwen3.6-27B 瑙ｆ瀽鎴?Query Frame
        鈫?[3] Hybrid Retrieval - Layer A (鍗曟潯 SQL)
        鈫?鍚屾椂璺戯細绮剧‘杩囨护 + JSONB 鍖呭惈 + 鏁板€兼帓搴?+ 鍚戦噺鐩镐技搴?        鈫?[4] Hyperedge Matching - Layer B (璺ㄥ眰 fallback)
        鈫?Layer 2 鍛戒腑锛?        鈫?  YES 鈫?涓荤敤 fact 浣?Evidence Pack
        鈫?  NO  鈫?Layer 1 passage / figure 浣滀笂涓嬫枃锛堜笉杩?grounded evidence锛?        鈫?  閮?NO 鈫?璇氬疄鎷掔瓟
        鈫?[5] Evidence Pack 缁勮
        鈫?鍚? 涓?evidence (Layer 2 fact) + context_only (Layer 1 娈佃惤/鍥?
        鈫?[6] Answer Planner (LLM #2)
        鈫?瑙勫垝绛旀缁撴瀯锛堟寜 intent 绫诲瀷锛?        鈫?[7] Grounded Generation (LLM #3)
        鈫?姣忎釜 claim 蹇呴』寮?fact_id锛圚TML <sup> 鏍囪锛?        鈫?[8] Verifier (LLM #4)
        鈫?claim-level support_status 鏍￠獙
        鈫?[9] Memory Update (鏈湴 sqlite)
        鈫?浠呭啓 session_memory锛涚粷涓嶅啓 patent KG
        鈫?[10] 杩斿洖绛旀锛堝惈 fact_id 寮曠敤 + PDF 璺宠浆閾炬帴 + 瑙嗚璇佹嵁锛?```

### 6.2 涓ゅ眰 hybrid 鐨勫叧绯?
**Layer A锛圫QL 鍐咃級**锛氬崟鏉?SQL 鍚屾椂璺戠粨鏋勫寲 + 鍚戦噺銆傝繖鏄?PG + pgvector 鐨勬牳蹇冭兘鍔涖€?
**Layer B锛堣法灞?fallback锛?*锛歀ayer 2 澶辫触 鈫?Layer 1 passage 鈫?Layer 1 figure 鈫?鎷掔瓟銆傝繖鏄瘹瀹為檷绾ф満鍒躲€?
```
                Hybrid Retrieval
                       鈹?    鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹粹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈫?                                    鈫?Layer A (SQL 鍐?                  Layer B (璺ㄥ眰)
绮剧‘ + JSONB + 鏁板€?+ 鍚戦噺         Layer 2 鈫?Layer 1 鈫?鎷掔瓟
鍚屾椂璺?                             閫愬眰 fallback
```

姣忎竴姝ラ兘鐢?Layer A锛屼笁姝ヤ覆璧锋潵鏄?Layer B銆?
### 6.3 5 绫?demo 闂绀轰緥锛堝惈瀹屾暣 SQL锛?
#### 6.3.1 fact_lookup 鈥斻€宨nventive A1B1 閰嶆柟涓?cross-cut adhesion 澶氬皯銆?
**Query Frame**:
```json
{
  "intent": "fact_lookup",
  "doc_id_hint": null,
  "example_id": "A1B1",
  "polarity": "positive",
  "property": "PROP_adhesion_cross_cut",
  "free_text_query": "inventive A1B1 cross-cut adhesion"
}
```

**SQL**:
```sql
SELECT fact_id, result_value, result_value_text, evidence_pointer
FROM facts
WHERE example_id = 'A1B1'
  AND polarity_hint = 'positive'
  AND property = 'PROP_adhesion_cross_cut';
```

**杩斿洖**锛氬崟涓€ fact锛屽惈 result_value=5.0銆乧ell="5B"銆乥box 缁欏墠绔敾妗嗐€?
#### 6.3.2 comparison 鈥斻€宨nventive vs comparative 鐨?gloss 瀵规瘮銆?
**Query Frame**:
```json
{
  "intent": "comparison",
  "property": "PROP_gloss_retention_after_amtec",
  "groups": ["positive", "negative"]
}
```

**SQL**:
```sql
SELECT comparison_group, polarity_hint, example_id,
       result_value, evidence_pointer
FROM facts
WHERE property = 'PROP_gloss_retention_after_amtec'
  AND comparison_group IN (
    SELECT DISTINCT comparison_group
    FROM facts
    WHERE property = 'PROP_gloss_retention_after_amtec'
    GROUP BY comparison_group
    HAVING COUNT(DISTINCT polarity_hint) >= 2
  )
ORDER BY comparison_group, polarity_hint, result_value DESC;
```

**杩斿洖**锛氭寜 group 鍒嗙粍鐨?fact 鍒楄〃锛屽墠绔覆鏌撳姣旇〃鏍笺€?
#### 6.3.3 improvement_suggestion 鈥斻€孭U 绫绘竻婕嗘€庝箞鎻愰珮鑰愬€欍€?
**Query Frame**:
```json
{
  "intent": "improvement_suggestion",
  "application_type": "APP_automotive_oem_clearcoat",
  "resin_class": "MAT_polyurethane",
  "target_property": "PROP_gloss_retention_after_amtec",
  "free_text_query": "polyurethane clearcoat weathering improvement"
}
```

**SQL锛圠ayer 2锛?*:
```sql
WITH high_gloss_pu AS (
  SELECT fact_id, doc_id, additives, process, result_value, evidence_pointer,
         1 - (evidence_embedding <=> $query_vec) AS sim
  FROM facts
  WHERE application = 'APP_automotive_oem_clearcoat'
    AND property = 'PROP_gloss_retention_after_amtec'
    AND polarity_hint = 'positive'
    AND resin_system = 'MAT_polyurethane'
    AND result_value >= 80
    AND extraction_confidence >= 0.7
  ORDER BY result_value DESC,
           evidence_embedding <=> $query_vec
  LIMIT 10
)
SELECT * FROM high_gloss_pu;
```

**SQL锛圠ayer 1 鍏滃簳锛屽鏋?Layer 2 鍛戒腑 < 3 鏉★級**:
```sql
SELECT passage_id, text, page,
       1 - (text_embedding <=> $query_vec) AS sim
FROM passages
WHERE tagged_entities @> '["MAT_polyurethane", "APP_automotive_oem_clearcoat"]'
ORDER BY text_embedding <=> $query_vec
LIMIT 5;
```

**杩斿洖**锛歀ayer 2 涓?evidence锛堝惈鍏蜂綋 additive + process锛? Layer 1 娈佃惤涓婁笅鏂囷紙"璇氬疄闄嶇骇"鏍囨敞锛夈€?
#### 6.3.4 evidence_lookup 鈥斻€孊ASF 寮傛鞍閰搁叝鍙嶅簲鍘熺悊銆?
**Query Frame**:
```json
{
  "intent": "evidence_lookup",
  "topic_entities": ["MAT_HDI_trimer", "PROC_urethane_formation"],
  "applicant": "BASF",
  "free_text_query": "BASF isocyanate reaction mechanism"
}
```

**SQL锛圠ayer 1 figure 涓昏矾锛?*:
```sql
SELECT figure_id, image_path, vlm_description, reference_numerals, page,
       1 - (description_embedding <=> $query_vec) AS sim
FROM figure_table_units
WHERE route = 'register_layer1'
  AND figure_subtype = 'scheme'
  AND tagged_entities @> '["MAT_HDI_trimer"]'
ORDER BY description_embedding <=> $query_vec
LIMIT 3;
```

**杩斿洖**锛氬弽搴斿紡鍥?+ VLM 鎻忚堪 + 鏍囧彿 label锛屽墠绔洿鎺ユ覆鏌撳師鍥俱€?
#### 6.3.5 honest_refuse 鈥斻€岀壒鏂媺鐢垫睜娑傚眰鎬庝箞鍋氥€?
**Query Frame**:
```json
{
  "intent": "fact_lookup",
  "applicant_hint": "Tesla",
  "application_hint": "battery coating"
}
```

**SQL**:
```sql
SELECT COUNT(*) FROM patents WHERE applicant ILIKE '%tesla%';  -- 0
```

**杩斿洖**锛氥€屾湭鍦ㄥ凡绱㈠紩涓撳埄涓壘鍒?Tesla 鐩稿叧鏁版嵁銆傚綋鍓嶇煡璇嗗簱鑼冨洿锛欱ASF 348 绡?PCT 娑傛枡涓撳埄锛?020-2026锛夈€傘€?*涓嶈儭缂?*銆?
---

## 7. 璺ㄥ眰鏄犲皠浣撶郴锛圴isual Evidence Pointer锛?
### 7.1 涓夊眰鏄犲皠绮掑害

| 鍛戒腑灞?| 鏄犲皠鐩爣 | 绮掑害 | 涓婚敭 |
|--------|---------|------|------|
| Layer 1 PassageHyperedge | 娈佃惤鍘熸枃 + page | 娈佃惤绾?| passage_id |
| Layer 1 FigureHyperedge | image.png + VLM 鎻忚堪 + page | 鍥炬暣寮?| figure_id 鈫?unit_id |
| Layer 2 FactHyperedge | image.png/table.html + cell + bbox + page | **cell 绾?* 猸?| fact_id 鈫?evidence_pointer.unit_id |

### 7.2 unit_id 鏄?traceability 鑰岄潪 cross-layer JOIN

**閲嶈婢勬竻**锛氭瘡涓?unit 缁?stage 4.5 璺敱鍚庡彧鑳借繘**涓€鏉?*璺緞锛?- 鈫?Layer 2 facts锛坋xtract_facts 璺敱锛?- 鈫?Layer 1 figure锛坮egister_layer1 璺敱锛?- 鈫?鍒犻櫎

鎵€浠?`unit_id` **涓嶄細鍚屾椂**鍑虹幇鍦?Layer 1 figure 鍜?Layer 2 fact 閲岋紝娌℃湁"璺ㄥ眰 JOIN by unit_id"鐨勫満鏅€?
`unit_id` 瀹為檯浣滅敤鏄?**per-unit traceability**锛?- 鍓嶇璺?PDF 鏃舵壘鍥炲師鍥?鍘熻〃
- 瀹¤鍥炴函锛歠acts.fact_id 鈫?unit_id 鈫?audit CSV 鈫?鍘熷 vlm_description.json

**鐪熸鐨勮法灞傝涔夊叧鑱?*闈?**canonical_id 鍏辩幇**锛?```sql
-- "鐢ㄦ埛闂?silane" 鍚屾椂鍙洖 Layer 2 fact + Layer 1 passage + Layer 1 figure
SELECT 'fact' AS source, fact_id AS id FROM facts
   WHERE additives @> '["MAT_silane"]'
UNION ALL
SELECT 'passage', passage_id FROM passages
   WHERE tagged_entities @> '["MAT_silane"]'
UNION ALL
SELECT 'figure', 'FIG_' || unit_id FROM figure_table_units
   WHERE route = 'register_layer1'
     AND tagged_entities @> '["MAT_silane"]';
```

---

## 8. 璺嚎鍥撅細V1 demo / V2 KG / V3 production

| 鐗堟湰 | 鐩爣 | 鐘舵€?| 鍏抽敭浜у嚭 |
|------|------|------|----------|
| **V1.2.7** | wide CSV demo + Layer 1 闆嗗悎钀界洏 | ~80% 瀹屾垚锛坰tage 4.5 宸茶惤鍦帮紝9.5 寰呰窇鎵癸級 | `coatings_wide.csv` + `data/layer1/<doc>/layer1.json` |
| **V2.0.5** | KG 鐏屽簱 + retrieval API skeleton | 璁捐鍐荤粨锛屾湭瀹炴柦 | PG 7 寮犺〃 + FastAPI `/v1/retrieve` |
| **V2.5** | retrieval API 鐢熶骇鍖?+ 5 绫婚棶棰橀棴鐜?| 璺嚎鍥鹃樁娈?| 鑰佹澘鍙紨绀?|
| **V3** | 璺ㄥ鏃忔墿閲忥紙Akzo / PPG / 绔嬮偊锛?+ 澶氱鎴?| 杩滄湡瑙勫垝 | ~10000 绡囦笓鍒?|

### V1 鈫?V2 P0 宸ヤ綔娓呭崟

| 浼樺厛绾?| 鏀瑰姩 | 鏂囦欢 | 宸ヤ綔閲?|
|--------|------|------|--------|
| P0 | stage 0.5 / 4 / 6 / 7 鍒囨湰鍦?vLLM | 4 涓?client base_url + 鍏?thinking | 1 澶?|
| P0 | vLLM 鍚姩鍙傛暟鍥哄寲 | `scripts/start_vllm.sh` | 0.5 澶?|
| P0 | Stage 4.5 unit router 钀藉湴 | `unit_router.py` 鉁?宸插畬鎴?V1.2.7 | 鈥?|
| P1 | Stage 9.5 瀹炶 single per-doc LLM + FigureHyperedge | `passage_extractor.py` 鉁?宸插畬鎴?V2.0.5 | 鈥?|
| P1 | 11 绡?golden set 鍥炲綊娴嬭瘯 | `scripts/eval_golden_set.py`锛堟柊寤猴級| 0.5 澶?|
| P2 | Stage 10 KG loader 瀹炶 | `scripts/load_facts_to_pg.py` | 2 澶?|
| P2 | BGE-M3 璺?vLLM 鍏遍┗ + retrieval API | FastAPI + pgvector hybrid SQL | 2 澶?|
| P3 | propose-and-curate 浜哄鐣岄潰 | Streamlit | 2 澶?|

**鎬诲伐浣滈噺**锛殈10 宸ヤ綔鏃ワ紙绾?2 鍛ㄥ彲婕旂ず V2.0.5锛?
---

## 9. Agent Memory 璁捐锛圴2.5+ 璋冪爺涓級

> **鐘舵€佹爣娉?*锛氭湰绔犳槸 V2.5 retrieval API 涓婄嚎鍓嶇殑璁捐璋冪爺銆傚綋鍓?V1.2.7 demo 涓嶄緷璧?memory锛堟瘡娆￠棶绛?single-turn锛夈€傛墍鏈夎璁＄偣绛夌湡瀹炲鐢ㄦ埛鍦烘櫙璺戣繃 1-2 鍛ㄥ悗鍥炲ご淇銆?*涓嶆槸 final design**銆?
### 9.1 鑳屾櫙锛氱幇鏈?doc 鐨?memory 缂哄彛

V1.0 璁捐绋垮湪 `02_ontology` + `07_v121_amendments` 绔嬩簡銆孧emory 鈫?Graph 闅旂銆嶅師鍒欙紝鎰忔€濇槸 **session 璁板繂涓嶈鍐欏洖 patent KG**銆備絾鍙珛浜?negative rule锛堜笉璁稿仛浠€涔堬級锛屽畬鍏ㄦ病鏈?positive design锛堝簲璇ユ€庝箞鍋氾級銆?
鍏蜂綋鐜扮姸锛?
- 娌¤ session memory 鍑犲眰
- 娌¤ schema
- 娌¤ retrieval 鏃舵€庝箞娉ㄥ叆
- 娌¤澶氱敤鎴峰満鏅?
**杩欐槸 V2.5 production blocker**锛氱涓€娆℃湁鐢ㄦ埛闂€屾垜鍒氭墠闂繃 PU 娓呮紗锛屽啀甯垜鎵?silane 鏀规€х殑銆嶏紝娌?memory 灏辩瓟涓嶅嚭銆?
### 9.2 涓変釜鍙傝€冩潗鏂欏鐓?
杩戞湡璋冪爺涓変唤鏉愭枡瀹氬瀷浜嗘垜浠殑 memory 璁捐鏂瑰悜銆?
#### 9.2.1 MAGMA paper锛坅rxiv:2601.03236, 2026-04锛?
UT Dallas 鐨勮鏂囷紝鏍稿績锛?*澶氬浘鍏崇郴寤烘ā + intent-aware traversal + 鍙屾祦鍐欏叆**銆?
**Data Structure 鈥?4 涓浜ゅ叧绯诲浘**锛?
| 瀛愬浘 | edge 鎬庝箞寤?| 鐢ㄦ潵绛?|
|------|-------------|--------|
| Semantic (Esem) | `cos(vi, vj) > 胃` 鑷姩 | 绫讳技鎬ч棶棰?|
| Temporal (Etemp) | `蟿i < 蟿j` 涓嶅彲鍙樻椂搴忛摼 | "浠€涔堟椂鍊? / WHEN |
| Causal (Ecausal) | LLM 寮傛鎺ㄦ柇鐨勫洜鏋滆暣鍚?| **"涓轰粈涔? / WHY** 猸?|
| Entity (Eent) | 浜嬩欢 鈫?鎶借薄瀹炰綋鑺傜偣 | "鍏充簬璋?浠€涔? / ENTITY |

**Query Process 鈥?4 闃舵**锛?
1. Query Analysis & Decomposition 鈥?intent 鍒嗙被锛圵HY/WHEN/ENTITY锛? 鏃堕棿绐楀彛 + 鍙?representation
2. Multi-Signal Anchor Identification 鈥?RRF 铻嶅悎 vec / keyword / time
3. Adaptive Traversal Policy 鈥?Heuristic Beam Search锛屾寜 intent 鍔ㄦ€佽皟 edge type 鏉冮噸
4. Narrative Synthesis via Graph Linearization 鈥?鎷撴墤鎺掑簭 + provenance + 鏄捐憲鎬?token 棰勭畻

**Memory Evolution 鈥?鍙屾祦鍐欏叆**锛?
- **Fast Path**锛氬悓姝ャ€佷綆寤惰繜锛屽彧鍋?segment + vector index + temporal edge锛?*涓嶈皟 LLM**锛?- **Slow Path**锛氬紓姝?LLM worker锛屾帹鏂?latent causal + entity edges

**鍏抽敭 metric锛圠oCoMo benchmark锛?*锛?
| 鏂规硶 | Judge Score | Latency | Tokens/Query |
|------|-------------|---------|--------------|
| **MAGMA** | **0.700** | **1.47s** | 3.37k |
| Nemori | 0.590 | 2.59s | 3.46k |
| A-MEM | 0.580 | 2.26s | 2.62k |
| Full Context | 0.481 | 1.74s | 8.53k |

**鍏抽敭娑堣瀺鍙戠幇**锛欰daptive Policy 鏄渶澶у崟涓€璐＄尞鑰咃紙鍘绘帀鎺?0.063锛夛紝鎰忓懗銆?*鎬庝箞閬嶅巻**銆嶆瘮銆?*鏈変粈涔堝浘**銆嶆洿閲嶈銆?
#### 9.2.2 璞嗗寘瑙嗛 鈥?4 闂鏋?
璁捐 Agent memory 鍓嶅厛鍥炵瓟 4 涓棶棰橈細

1. **璁扮粰璋?* 鈥?鍗曠敤鎴?/ 澶氱敤鎴?/ Agent 鑷粡楠?2. **璁颁粈涔?* 鈥?浜嬪疄 / 鍋忓ソ / 琛屼负妯″紡 / 鍏崇郴
3. **璁板涔?* 鈥?鏃堕棿绐楀彛 + 杩囨湡绛栫暐锛堥伩鍏?stale锛?4. **鎬庝箞鍙?* 鈥?绮剧‘ / 妯＄硦 / 澶氳烦

涓変釜瀵规爣妗嗘灦锛?
| 妗嗘灦 | 寮哄湪 | 寮卞湪 |
|------|------|------|
| Mem0 | 鎺ュ彛绠€鍗曘€佸欢杩熶綆锛堟枃浠剁郴缁?+ 鍚戦噺锛墊 鍏崇郴鎺ㄧ悊寮?|
| Zep | 澶氳烦鎺ㄧ悊锛圞G-based锛墊 宸ョ▼澶嶆潅銆佸啓鍏ユ參 |
| Letta | Agent 鑷不鍒嗗眰 | 榛戠洅闅?debug |

鏍稿績寤鸿锛氥€?*鍒繃搴﹁璁★紝鍏堣窇璧锋潵鍐嶈**銆嶃€?
#### 9.2.3 Karpathy Wiki + Compaction锛圙PT 宸ヤ綔娴侊級

浠?Karpathy 鐨?wiki / Hermes 宸ヤ綔娴佷负钃濇湰锛?
1. **Wiki 鍋氶暱鏈熻蹇?*锛氭妸闆舵暎鏂囦欢 + 鑱婂ぉ璁板綍缁撴瀯鍖栨垚 Technical Claims + Claim Evidence + Benchmark Results + Knowledge Deltas
2. **Compaction锛堜笂涓嬫枃鍘嬬缉锛?*锛歐iki 涓嶇洿鎺ュ杺 LLM 鍏ㄩ儴锛屽厛鍘嬫垚銆宑ontext packet銆嶁€斺€?灏忚€岀簿鐨勪笂涓嬫枃
3. **璇诲彇娴佺▼**锛歲uery router 鈫?鎷夌浉鍏?hub/claims/evidence/deltas 鈫?compact 鈫?context packet 鈫?LLM
4. **鍒涙柊鐐?*锛氭椿鐨勮蹇?+ Knowledge Delta锛堟柊浜嬪疄濡備綍鏀瑰彉鏃х悊瑙ｏ級+ 鍥炲啓 wiki + validation 闂幆

#### 9.2.4 涓夊瀵圭収琛?
| 缁村害 | MAGMA | 璞嗗寘 4 闂?| Karpathy Wiki + Compaction |
|------|-------|-----------|---------------------------|
| 璁颁粈涔?| 4 graphs (sem/temp/causal/entity) | facts / prefs / behavior / relations | Technical Claims + Evidence + Benchmark + Deltas |
| 璁板涔?| Temporal Graph 涓嶅彲鍙?+ Slow Path 閲嶇粍 | 鏃堕棿绐楀彛 + 杩囨湡 | Knowledge Delta锛堟柊瑕嗙洊鏃э級|
| 鎬庝箞鍙?| Intent Router + Beam Search 澶氬浘閬嶅巻 + RRF | exact / fuzzy / multi-hop | Query Router 鈫?Compact 鈫?Packet |
| 鎬庝箞鍐?| Fast/Slow 鍙屾祦 | (娌″己璋?| 涓诲姩鍥炲啓 wiki + Validation |
| 鏍稿績鍒涙柊 | **Causal graph** + Adaptive intent traversal | 绛栫暐閫夊瀷 | **Compaction** + Delta + 鍥炲啓 |

**鍏抽敭鍙戠幇**锛歁AGMA Stage 4 Linearization锛堟嫇鎵戞帓搴?+ token 棰勭畻锛夎窡 Karpathy Compaction **鏈川鍚屼竴浠朵簨** 鈥斺€?閮芥槸鎶婂ぇ瑙勬ā raw memory 鍘嬬缉鎴?LLM 鍙嬪ソ鐨?context packet銆傚樊鍒彧鏄細

- MAGMA锛?*缁撴瀯鍖?compaction**锛堟寜鍥炬嫇鎵戦『搴忥級
- Karpathy锛?*璇箟 compaction**锛堜繚 high-salience claim锛?
### 9.3 鎴戜滑鐨勫彇鑸?
鐩存帴鎶?MAGMA 椋庨櫓楂橈紙澶氬浘 + agentic 鏈哄埗瀵规秱鏂?demo 杩囧害锛夛紝浣嗘湁 **4 涓?idea 蹇呴』鍙栵紝2 涓?idea 涓诲姩涓嶅彇**銆?
#### 9.3.1 鍙栫殑 4 涓?idea

| idea | 鏉ユ簮 | 鎴戜滑鎬庝箞鐢?|
|------|------|-----------|
| **Intent-Aware Router** | MAGMA Stage 1 | 澶嶇敤 搂6.1 鐨?Query Parser intent 瀛楁鍋?memory 妫€绱㈠垎鍙?|
| **RRF 铻嶅悎 vec + keyword + time** | MAGMA Stage 2 | Layer A SQL hybrid 宸茬粡鏈?vec + keyword锛屽姞 time 淇″彿 |
| **鍙屾祦鍐欏叆锛堢畝鍖栫増锛?* | MAGMA 搂3.4 | Fast Path 鍐?raw 瀵硅瘽锛汼low Path 寮傛 LLM 鎶?user preference锛?*涓嶆娊 causal**锛墊
| **Compaction锛圞arpathy 鎬濊矾锛?* | Karpathy + MAGMA Stage 4 | Q&A 搂6.1 绗?5 姝?Evidence Pack 缁勮鍋氱粨鏋勫寲鍘嬬缉 + provenance + token 棰勭畻 |

#### 9.3.2 涓嶅彇鐨?2 涓?idea + 璇︾粏鐞嗙敱 猸?
**涓嶅彇 #1锛歁AGMA 4 graphs 澶氬叧绯诲浘**

| 鍘熷洜 | 璇︾粏瑙ｉ噴 |
|------|----------|
| 娑傛枡 query intent 闆嗕腑搴﹂珮 | demo 5 绫婚棶棰橈紙fact_lookup / improvement / comparison / evidence_lookup / honest_refuse锛夐噷 4 绫婚兘涓嶉渶瑕?涓轰粈涔?鍥犳灉閾炬帹鐞嗐€?*澶嶆潅澶氳烦鍥犳灉闇€姹傚疄娴?< 5%** |
| 鎴戜滑宸叉湁鏇村己缁撴瀯 | Layer 2 FactHyperedge 鏄?N 鍏?hyperedge锛?*fact 鍐呴儴鐨?application + property + process + result_value 妲戒綅鏈韩灏辨槸 MAGMA Entity Graph 鐨勮秴闆?*銆傚啀鍗曞缓澶氬浘绛変簬鍙岀储寮?|
| 宸ョ▼澶嶆潅搴︾垎鐐?| 4 鍥?脳 348 绡?脳 骞冲潎 50 events/绡?= ~70K 鑺傜偣 脳 4 绉?edge type锛岀嫭绔?traversal + RRF 铻嶅悎锛?*production 鐏板害鎴愭湰 > 瀹炴祴鏀剁泭** |
| MAGMA paper advantage 鍦ㄩ暱瀵硅瘽 | 璁烘枃 LoCoMo benchmark 9K tokens/conversation锛屾槸**澶?session 闀垮璇濆満鏅?*銆傛垜浠敤鎴峰満鏅槸**鐭璇?+ 闀挎枃妗ｆ绱?*锛宎dvantage 涓?ground |
| Mem0 鍝插锛氬厛璺戣捣鏉?| 璞嗗寘瑙嗛鏄庣‘璇淬€?*鍒繃搴﹁璁?*銆嶏紝澶氬浘鏄?V3 鎵嶈€冭檻鐨勬柟鍚?|

**鐭湡鍐?*澶嶇敤 patent KG 鐨?entities + 涓€涓畝鍗?sqlite session_history 宸茬粡澶熴€?*鏈潵鎵╁埌澶氱敤鎴疯法 session 鐨勫鏉?personalization 鏃跺啀鍥炴潵鍔?MAGMA**銆?
**涓嶅彇 #2锛欳ausal Graph**

| 鍘熷洜 | 璇︾粏瑙ｉ噴 |
|------|----------|
| 鍥犳灉鍦?fact 鍐呴儴宸茬粡缂栫爜 | Layer 2 fact 鐨?`polarity_hint`锛坕nventive vs comparative锛? `comparison_group` 宸茬粡琛ㄨ揪浜嗐€岃繖涓厤鏂规瘮閭ｄ釜濂姐€嶇殑鍥犳灉鏂█銆?*N 鍏?hyperedge 鏄洜鏋滀俊鎭殑澶╃劧瀹瑰櫒**锛屼笉闇€瑕佸闈㈠啀寤哄浘 |
| LLM 鎺ㄦ柇鍥犳灉 = 寮曞叆骞昏 | MAGMA Slow Path 鐢?LLM 鎺?causal edges锛岃鏂?搂6 鑷繁鎵胯銆宻usceptible to extraction errors and hallucinations銆嶃€?*鎴戜滑 V1.2.5 宸茬粡鍥犱负 LLM 骞昏鍋氫簡 propose-and-curate 杞害鏉燂紝涓嶆兂鍐嶅涓€灞?LLM 涓嶅彲淇＄殑 causal 鎺ㄦ柇** |
| ROI 涓嶅垝绠?| MAGMA 娑堣瀺瀹為獙锛氬幓鎺?causal links 鎺?0.056锛屽幓鎺?entity links 鎺?0.034銆?*causal 涓嶆槸 dominant signal**鈥斺€旀垜浠殑鍦烘櫙閲?dominant signal 鏄?fact `result_value` 鏁板€兼帓搴忥紝璺?causal 鏄笉鍚岀淮搴?|
| 娑傛枡鐢ㄦ埛涓嶉棶娣卞洜鏋?| 瀹炴祴 demo 5 绫婚棶棰橀噷 0% 鏄?"涓轰粈涔?PU 姣?epoxy 鑰愬€欏ソ杩欑娣卞洜鏋滄満鐞嗘帰璇?銆侭ASF 涓撳埄閲岃繖绉嶉棶棰樼殑绛旀鏄畾鎬ф弿杩帮紙鍐欏湪 description锛夛紝宸茶蛋 Layer 1 PassageHyperedge銆?*涓嶉渶瑕佸啀鍗曞缓鍥犳灉鍥?* |
| 鎴戜滑鐨?鍥犳灉"闈?fact_id 寮曠敤瑙ｅ喅 | 鐢ㄦ埛闂€屼负浠€涔堣繖涓厤鏂瑰ソ銆嶁啋 鐩存帴杩斿洖 `polarity=positive` 鐨?fact + `evidence_pointer` 鈫?LLM 鐢ㄥ師 patent description 娈佃惤鐢熸垚"涓轰粈涔?瑙ｉ噴銆?*Causal 涓嶆槸鏁版嵁缁撴瀯闂锛屾槸 retrieval 娓叉煋闂** |

**鎬荤粨鍝插**锛歁AGMA 鏄负銆?*闀垮璇濆満鏅笅鐨勫鏉傚璺冲洜鏋滄帹鐞?*銆嶈璁＄殑锛涙垜浠槸銆?*鐭璇?+ 娴烽噺缁撴瀯鍖?fact + 鍋跺皵 fallback 娈佃惤**銆嶅満鏅€?*涓嶅悓鍦烘櫙锛屼笉鍚?trade-off**銆?
### 9.4 鎴戜滑鐨?3 灞?memory 鏋舵瀯

```
鈹屸攢 Layer 1: Session Memory (Hot, ~1h)
鈹? 鈥?鏈€杩?5-10 杞璇?raw 鏂囨湰
鈹? 鈥?sqlite 鍗曟枃浠?+ per-process LRU
鈹? 鈥?Fast Path 鍚屾鍐欏叆
鈹? 鈥?鐢ㄩ€? query rewrite 涓婁笅鏂囨秷瑙?("閭ｄ釜 PU 閰嶆柟")
鈹?鈹溾攢 Layer 2: Summary Memory (Warm, ~7d)
鈹? 鈥?Slow Path 寮傛 LLM 娴撶缉 session 鈫?<100 瀛?summary
鈹? 鈥?sqlite锛屼笅娆″悓 user 杩涙潵褰?system prompt 涓€閮ㄥ垎
鈹? 鈥?鐢ㄩ€? 璺?session 涓€鑷存€?鈹?鈹斺攢 Layer 3: Knowledge Memory (Cold, 姘镐箙 + 90d 澶嶇‘璁?
   鈥?鐢ㄦ埛鍋忓ソ鎶藉嚭鏉ョ殑缁撴瀯鍖?fact:
       (user_id, fact_type, value, timestamp, confidence, status)
   鈥?fact_type 鈭?{application_focus, priority_property, deal_breaker, expertise_level}
   鈥?鐢ㄩ€? retrieval 鏃朵綔 SQL filter 娉ㄥ叆 (涓嶆槸濉?prompt)
```

**Schema 鑽夌**锛?
```sql
-- Layer 1: Session Memory
CREATE TABLE session_memory (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    last_activity_at TIMESTAMP NOT NULL,
    raw_messages JSONB,                      -- 鏈€杩?5-10 杞?    referenced_fact_ids TEXT[]               -- 鐢ㄦ埛闂繃 / 纭杩囩殑 fact_id
);
CREATE INDEX ON session_memory (user_id, last_activity_at DESC);

-- Layer 2: Summary Memory
CREATE TABLE session_summary (
    session_id TEXT PRIMARY KEY REFERENCES session_memory,
    user_id TEXT NOT NULL,
    summary_text TEXT,                        -- LLM 鎶藉嚭鐨?<100 瀛楁€荤粨
    created_at TIMESTAMP,
    expires_at TIMESTAMP                      -- 榛樿 7 澶?);

-- Layer 3: Knowledge Memory
CREATE TABLE user_knowledge (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    fact_type TEXT NOT NULL,                  -- 'application_focus' / 'priority_property' / ...
    value JSONB,                              -- 'APP_automotive_oem_clearcoat' 鎴?['PROP_gloss']
    confidence FLOAT,                         -- 0.0-1.0
    status TEXT,                              -- 'active' / 'expired' / 'pending_confirmation'
    timestamp TIMESTAMP,
    last_confirmed_at TIMESTAMP,
    supersedes_id BIGINT REFERENCES user_knowledge   -- conflict 鏃舵柊瑕嗙洊鏃э紙涓嶅垹锛?);
CREATE INDEX ON user_knowledge (user_id, fact_type, status);
```

### 9.5 Compaction 鏈哄埗锛圗vidence Pack 缁勮鐨勭粏鍖栵級

搂6.1 绗?5 姝?Evidence Pack 缁勮褰撳墠娌¤鎬庝箞鍘嬬缉銆傚€?MAGMA Stage 4 + Karpathy compaction 鎬濊矾瀹氫箟 **3 姝ュ崗璁?*锛?
**Step 1: Topological Ordering锛堟寜 intent 鎺掑簭锛?*

- intent=fact_lookup 鈫?鎸?`result_value DESC + sim ASC`锛圧RF 铻嶅悎锛岃 搂6.3锛?- intent=comparison 鈫?鎸?`comparison_group + polarity_hint` 鍒嗙粍
- intent=evidence_lookup 鈫?鎸?`page` 椤哄簭

**Step 2: Context Scaffolding with Provenance**

姣忔潯 evidence 搴忓垪鍖栨垚缁撴瀯鍖栧潡锛?
```
<evidence ref="F_xxx_001" page="25" confidence="0.95">
  Inventive A1B1 閰嶆柟涓嬶紝cross-cut adhesion = 5B
  鏉ユ簮: Table 1 绗?3 琛岀 5 鍒?[bbox=120,340,480,360]
</evidence>
```

LLM 蹇呴』鐢?`<sup><a href="#F_xxx_001">鈶?/a></sup>` 寮曠敤銆?
**Step 3: Salience-Based Token Budgeting**

- 楂樼疆淇″害 fact (`confidence > 0.85`) 鈫?鍏ㄥ瓧娈靛
- 涓疆淇″害 (`0.7-0.85`) 鈫?鍙 application + property + result_value + evidence_pointer
- 浣庣疆淇″害 (`< 0.7`) 鈫?鍘嬬缉鎴?`"...3 similar facts in same comparison_group..."`
- 鎬?token 棰勭畻: **4K**锛堢暀 4K 缁?LLM 鎬濊€?+ 杈撳嚭锛?
### 9.6 涓?patent KG 鐨勯殧绂昏竟鐣岋紙spec 搂淇 6 绾㈢嚎锛?
**鍙厑璁哥殑閫氶亾**锛堝崟鍚戯級锛?
```
patent KG (PG facts/passages/figures)
        鈫?璇诲彇
retrieval API
        鈫?铻嶅悎
Evidence Pack (鍚?user.preferences 娉ㄥ叆鐨?SQL filter)
        鈫?鍠傚叆
LLM 绛旀鐢熸垚
        鈫?鐩戠潱
session_memory write (Layer 1)
        鈫?娴撶缉
summary / knowledge memory (Layer 2/3, 鐙珛 sqlite)
```

**缁濆绂佹**锛?
- 鉂?session memory 鐨勫唴瀹瑰啓鍥?PG facts/passages
- 鉂?鐢ㄦ埛瀵?fact 鐨?thumbs up/down 鏀?`fact.extraction_confidence`锛堥槻姹℃煋锛?- 鉂?鎶?user.preferences 褰?fact.application 杩?KG锛坲ser 涓嶆槸 patent 鐨?entity锛?
**杩濅緥鍚庢灉**锛歶nfettered memory write 浼氳 patent KG 澶卞幓瀹㈣鐪熷€肩殑鍙俊搴︼紝瀵艰嚧 retrieval 缁撴灉鍦ㄤ笉鍚?user / 涓嶅悓 session 涓嬭緭鍑轰笉鍚?鈥?**宕╁淇′换閿?*銆?
### 9.7 瀹炴柦璺嚎锛圴1 鈫?V2 鈫?V3锛?
```
V1.2.7 (鐜板湪):
  鉂?璺宠繃 memory锛宻ingle-turn retrieval 鈥?wide CSV demo 涓嶄緷璧?
V2.0.5 retrieval API skeleton (~1.5 澶?:
  鉁?Layer 1 session_memory锛坰qlite + LRU锛?  鉁?Query Parser intent 瀛楁鍔?memory router
  鉁?Fast Path 鍐欏叆锛氭瘡杞璇濊惤 sqlite

V2.5 production (~5 澶?:
  鉁?Layer 2 summary_memory锛圫low Path 寮傛 LLM 娴撶缉锛?  鉁?Layer 3 knowledge_memory锛堜粠 session 鎶?user.preferences锛?  鉁?Compaction 鍗忚锛圱opological + Scaffolding + Token Budgeting锛?  鉁?Knowledge Delta: 鍋忓ソ鍐茬獊鏃舵柊瑕嗙洊鏃э紙淇濈暀 expired 璁板綍锛?  鉁?90 澶?stale 澶嶇‘璁ゆ満鍒?  鉁?retrieval API 娉ㄥ叆 user.preferences 褰?SQL filter

V3 scale (鏈潵):
  馃 澶氱敤鎴?partition by team_id
  馃 璺ㄧ敤鎴?preference 缁ф壙锛堝悓 team 鍏变韩锛?  馃 濡傛灉瀹炴祴澶氳烦鍥犳灉闇€姹?> 10%锛岃€冭檻鍔?MAGMA 椋庢牸 Causal Graph
  馃 濡傛灉鐢ㄦ埛閲?> 1000锛岃€冭檻 Letta 椋庢牸 Agent 鑷鐞?memory
```

### 9.8 璋冪爺涓殑寮€鏀鹃棶棰?
涓嬮潰 4 鏉?V1 demo 涓嶅奖鍝嶏紝浣?V2.5 蹇呴』鍥炵瓟锛?
**Q1: Knowledge Delta 绠楁硶閫変粈涔?*
- 鏂规 A锛氭柊鍐欏叆 supersedes 鏃х殑锛堜繚鐣?expired 璁板綍锛夛紝retrieval 鏃舵寜 `status='active'` 杩囨护
- 鏂规 B锛歁AGMA 椋庢牸 LLM 鎺ㄦ柇銆屾柊浜嬪疄鏄惁鍐茬獊鏃т簨瀹炪€嶏紝鍐茬獊鎵?supersede
- 鏂规 A 绠€鍗曞伐绋嬶紝鏂规 B 鍑嗕絾鎱?+ 寮曞叆 LLM 骞昏
- **鍊惧悜 A锛屽厛璺戣捣鏉ュ啀鍗囩骇**

**Q2: stale detection 瑙﹀彂鏉′欢**
- 90 澶╂病纭灏变富鍔ㄩ棶锛熷お鐑︿汉
- 鐢ㄦ埛 30 澶╂病鐢ㄩ」鐩紝鍥炴潵鍚庡己鍒?confirm锛熷悎鐞嗕絾 cold start 浣撻獙宸?- 搴旇鐢?**decay function**锛歚confidence 脳 exp(-days/180)`锛宺etrieval 鏃朵綆浜庨槇鍊兼墠涓诲姩纭

**Q3: 澶氱敤鎴?conflict 濡備綍 partition**
- 鍗?sqlite 鏂囦欢鎸?user_id 绱㈠紩灏卞锛圴2.5 鐢ㄦ埛鏁?~10 浜猴級
- V3 鐢ㄦ埛鏁颁笂 100+ 鏃跺垏鍒?Postgres + RLS锛坮ow-level security锛?
**Q4: Memory 鍗?prompt 澶氬皯 token 鍚堥€?*
- MAGMA 瀹炴祴 3.37K tokens/query 鏄敎铚滅偣锛堝灏?lost-in-the-middle锛屽皯灏?missing context锛?- 鎴戜滑寤鸿锛欵vidence Pack 4K + memory 1K + system prompt 2K + free 1K = **8K input total**
- Qwen3.6-27B max_model_len 8K 鍒氬ソ鑳借

### 9.9 璺?MAGMA / 璞嗗寘 / Karpathy 鐨勬€诲鐓э紙鏈€缁堢増锛?
| 鎴戜滑鏂规 | MAGMA | 璞嗗寘瑙嗛 | Karpathy |
|---------|-------|----------|----------|
| 3 灞傛灦鏋勶紙Session/Summary/Knowledge锛墊 鍗曞浘澶氱被鍨?| 妯＄硦鎻愬埌鍒嗗眰 | Wiki 鍗?hub |
| 涓嶅垎 sem/temp/causal/entity 澶氬浘 | 4 鍥?| (娌¤瀹? | 鍗?wiki |
| Evidence Pack 鍋?Compaction | Linearization | (娌¤瀹? | Compaction |
| Fast/Slow Path 绠€鍖栫増锛堜笉鎺?causal锛墊 Fast/Slow 鍙屾祦 | (娌¤瀹? | (娌¤瀹? |
| user.preferences 褰?SQL filter | (娌¤瀹? | (娌¤瀹? | (娌¤瀹? |
| Knowledge Delta supersedes 涓?delete | Causal edge supersede | "鏂拌鐩栨棫" | Knowledge Delta |
| 90 澶?stale 澶嶇‘璁?| (娌¤瀹? | "鏃堕棿绐楀彛閬垮厤杩囨湡" | (娌¤瀹? |
| 涓ョ鍥炲啓 patent KG | Slow Path 鍐欏洖 graph | (娌¤瀹? | 涓诲姩鍥炲啓 wiki |

**鎬荤粨**锛氭垜浠槸 MAGMA 绠€鍖栫増 + Karpathy compaction + 璞嗗寘 4 闂鏋剁殑缁勫悎銆?*鏈€澶х嫭鏈夌偣鏄?patent KG 涓ョ鍥炲啓** 鈥斺€?鍥犱负 patent fact 鏄瑙傜湡鍊硷紝璺?user-specific memory 蹇呴』涓ユ牸闅旂銆?
---

## 10. 鍩哄骇妯″瀷鎺ㄧ悊鍔犻€燂紙V2.0 vLLM 閮ㄧ讲鏃跺惎鐢級

> **鐘舵€佹爣娉?*锛氭湰绔犳槸 V2.0 鎶婁簯 qwen-plus 鍒囧埌鏈湴 vLLM Qwen3.6-27B-AWQ 鏃剁殑浼樺寲娓呭崟銆傚熀浜?2026 骞村垵绀惧尯瀹炴祴鏁版嵁锛圦wen3.6 + 4090 / 3090 瀹炴祴瑙嗛锛夈€?*A100 80G 姣?4090 鏇村己**锛屾墍鏈夋暟瀛楁寜 A100 鎺ㄦ柇鍚庣粰鍑恒€?
### 10.1 鑳屾櫙锛歅IPELINE_STAGES_v5 搂4 AWQ 绔犺妭鐨勫眬闄?
PIPELINE_STAGES_v5 搂4 鍐欎簡 vLLM 鍚姩鍙傛暟锛坄max_model_len`, `enable_prefix_caching`, `guided-decoding-backend outlines`锛夛紝浣?*娌¤鐩?*锛?
- 鉂?MTP锛圡ulti-Token Prediction锛屾姇鏈鸿В鐮侊級
- 鉂?KV cache 閲忓寲锛堥暱 context 鍏抽敭锛?- 鉂?DiffuFlash / DD-tree锛堟墿鏁ｅ紡骞惰鐢熸垚 鈥?璇勪及鍚庝笉鍙栵級
- 鉂?璺熷叿浣?stage 宸ヤ綔璐熻浇鐨勫尮閰嶆€у垎鏋?
鏈珷琛ヨ繖閮ㄥ垎銆?
### 10.2 瑙嗛瀹炴祴璺嚎鍥撅細20 鈫?184 token/s锛?090 鍗曞崱锛?
绀惧尯鎶?Qwen3.6-27B 鍦?4090 鍗曞崱浠庡熀纭€ FP8 涓€璺紭鍖栧埌 184 token/s 鐨勮繃绋嬶細

```
鍩虹: FP8 + 鍗曞崱 4090            20 token/s    鈹佲攣 鐜╁叿绾?        鈫?閲忓寲锛圵16 鈫?W4锛?+ AWQ INT4                       48 token/s    +140%锛堝甫瀹界摱棰堢紦瑙ｏ級
        鈫?+ 鎶曟満瑙ｇ爜
+ MTP n=5                        108 token/s   +124%锛圙PU 绌洪棽濉厖锛?        鈫?+ 鎵╂暎寮忚В鐮?+ DiffuFlash + DD-tree           184 token/s   +70%锛堝苟琛屽璺緞涓嬫敞锛?        鈫?+ KV 閲忓寲锛堢嫭绔嬩紭鍖栵級
+ TurboCount KV cache 3-4 bit    鏀寔 200K 涓婁笅鏂囷紙@24G 鏄惧瓨锛?```

**鎬绘彁鍗?~9脳**锛屼笖**涓嶆崯绮惧害**锛團P8 鍑犱箮鏃犳崯 + AWQ ~1-2% 绮惧害鎹熷け锛夈€?
**鍏抽敭 trade-off**锛?1. **DiffuFlash 涓嶆敮鎸佸骞跺彂** + 鍒涙剰鍐欎綔鍦烘櫙澶辨晥 鈫?閫傚悎浠ｇ爜 / 鏁板 / 缁撴瀯鍖栬緭鍑?2. **MTP 鍛戒腑鐜囪窡浠诲姟鐩稿叧**锛氭暟瀛?> 浠ｇ爜 > 鍒涙剰锛堝懡涓巼褰卞搷瀹為檯鍔犻€燂級

### 10.3 绠楀瓙灞傛繁鍏ヨВ鏋?
#### 10.3.1 AWQ INT4 鈥?Activation-aware Weight Quantization

**鏈寸礌 INT4 閲忓寲**锛圙PTQ 鐨勫垵鐗堬級鐨勯棶棰橈細
- 16 涓瓑绾ц〃杈惧師鏈?256 涓瓑绾?鈫?涓?16脳 绮惧害
- 1% outlier channel 涓诲 quantization range锛屾甯告潈閲嶇簿搴︾垎鐑?
**AWQ trick**锛圠in et al. 2023锛夛細
1. 璺?calibration set 娴嬫瘡涓?channel 鐨?activation magnitude `|a|`
2. 鎵?`|a|` 鏈€澶х殑 1% channel锛?鏄捐憲鏉冮噸"锛?3. 瀵规樉钁?channel 鍋?**per-channel scaling**锛?   ```
   W' = W * s   (鎺ㄧ悊鏃跺彉鎹?
   a' = a / s   (杈撳叆绔€嗗彉鎹?
   ```
   鏁板涓?`W' 路 a' = W 路 a` 绛変环锛屼絾 `W'` 钀借繘鏇寸獎鐨勯噺鍖栬寖鍥?鈫?绮惧害鏇撮珮
4. 鎺ㄧ悊鏃?`s` 鏄父鏁帮紝铻嶅悎杩涚浉閭?LayerNorm 鐨?weight 閲?鈫?**0 棰濆寮€閿€**

**绠楀瓙灞傞潰**锛?- W4A16 = 鏉冮噸 INT4锛屾縺娲?FP16
- 鐭╅樀涔橈細`Y = (W_int4 * scale_per_group) @ X_fp16`锛?*dequant on-the-fly + tensor core fp16 GEMM**
- vLLM 鐢?`marlin` kernel 鍋?W4A16 GEMM锛屾瘮鏈寸礌 dequant 蹇?3-5脳

**鍏抽敭娲炲療**锛氱簿搴︽崯澶变粠 GPTQ 鐨?-2.1pt MMLU 闄嶅埌 AWQ 鐨?-1.0pt锛岀煩闃典箻閫熷害 INT4 姣?FP16 蹇?~2脳锛坱ensor core 鍒╃敤鐜囨媺婊★級+ 鏄惧瓨甯﹀鍑忓崐銆?
#### 10.3.2 MTP 鈥?Multi-Token Prediction锛堟姇鏈鸿В鐮侊級

**鏈寸礌鑷洖褰?*鐨勭摱棰堬細姣忎釜 token 涓€娆″畬鏁?forward pass銆侴PU 绠楀姏 鈮?鏄惧瓨甯﹀锛?*澶ч儴鍒嗘椂闂?GPU 鍦ㄧ瓑鏄惧瓨鎶婃潈閲嶅姞杞借繘鏉?*锛坢emory-bound锛夈€?
**MTP 鎬庝箞鍋?*锛?1. **Draft 闃舵**锛氭ā鍨嬩富浣撹窇涓€娆?forward 鍚庯紝鐢ㄤ竴涓?*杞婚噺 head**锛堝嚑灞?transformer锛変竴娆￠娴?N 涓?future tokens锛?*鍗曟骞惰棰勬祴 N 涓綅缃?*锛?2. **Verify 闃舵**锛氭妸 [original_input, draft_token_1, ..., draft_token_N] 鏁翠綋閫佽繘 model 鍋?forward锛?*涓€娆?forward pass 鍚屾椂楠岃瘉 N 涓?token 鐨?logits**
3. **Accept-Reject**锛氫粠宸﹀埌鍙抽€愪釜姣旇緝 draft 鍜?model 鐨勫疄闄呴娴嬶細
   - 涓€鑷?鈫?accept锛宼oken 鍏ュ簭鍒?   - 涓嶄竴鑷?鈫?鐢?model 棰勬祴鏇挎崲 draft锛?*鍚庨潰鐨?draft tokens 鍏ㄩ儴涓㈠純**

**涓轰粈涔堝揩**锛?- Verify 闃舵涓€娆?forward pass 澶勭悊 N+1 涓?position锛屾樉瀛樺甫瀹界敤婊?- N=5 鏃跺鏋滃懡涓巼 60%锛屽钩鍧囨瘡娆?verify 鎺ュ彈 3 涓?token 鈫?**3脳 鍔犻€?*
- Attention 鏄?O(N虏) 浣?N=5 鏋佸皬锛屽嚑涔庝笉澧炲姞 latency

**涓轰粈涔堝懡涓巼璺熶换鍔＄浉鍏?*锛?
| 浠诲姟绫诲瀷 | 鍏稿瀷鍛戒腑鐜?| 瀹炴祴鍔犻€?|
|---------|-----------|---------|
| 鏁板棰橈紙鍏紡绗﹀彿楂樺害鍙娴嬶級| 70-80% | **3-4脳** |
| 缁撴瀯鍖?JSON 杈撳嚭锛坘ey 蹇呯劧鍛戒腑锛墊 **70%+** 猸?鎴戜滑 stage 7 | **2.5-3脳** |
| 浠ｇ爜锛堝彉閲忓懡鍚嶈嚜鐢憋級| 50-60% | 2-2.5脳 |
| 鍒涙剰鍐欎綔锛堣嚜鐢卞害澶珮锛墊 < 20% | **鍙兘鍙嶈€屾參** |

#### 10.3.3 DiffuFlash + DD-tree 鈥?鎵╂暎寮忓苟琛岀敓鎴?
**鑷洖褰掔殑鏍规湰闄愬埗**锛氭瘡涓?token 渚濊禆鍓嶉潰鎵€鏈?tokens锛?*鏃犳硶骞惰**銆?
**Diffusion 鎬濊矾**锛?1. 鎶婅鐢熸垚鐨?N 涓?future tokens **鍚屾椂鍒濆鍖栦负 mask token**
2. 姣忎竴姝?*骞惰棰勬祴鎵€鏈?N 涓綅缃殑 token logits**锛堜竴娆?forward pass锛宎ttention 鍏ㄥ埌鍏級
3. 閫?confidence 鏈€楂樼殑鍑犱釜 token "钀藉畾"锛坲nmask锛夛紝鍓╀笅鐨勭户缁?mask
4. 閲嶅鍑犳鐩村埌鍏?unmask
5. 鎬绘鏁?鈮?N锛堝吀鍨?N=64 姝ユ暟 8-12锛夛紝鎬?latency 姣旇嚜鍥炲綊 N 姝ュ皯寰堝

**DD-tree锛圖iffusion Draft Tree锛?*锛?- 涓嶅彧鐢熸垚涓€鏉＄嚎锛?*鐢熸垚 22 鏉″閫?token 璺緞**
- 姣忔潯璺緞鐙珛鎵撳垎
- 涓€娆℃€?verify 22 鏉′腑**鏈€闀跨殑 prefix match** 鈫?涓€娆?forward 鎺ュ彈鍗佸嚑涓?token

**绠楀瓙绾?deep**锛欴iffuFlash 鎶?transformer forward pass 鏀规垚 **bidirectional attention锛堝弻鍚戯級**锛屽洜涓虹洰鏍?tokens 鏄悓鏃剁敓鎴愮殑銆佷簰鐩歌兘鐪嬭銆傝繖璺熶紶缁熷洜鏋?mask 涓嶄竴鏍枫€?
**涓轰粈涔堜笉鏀寔澶氬苟鍙?*锛歜idirectional attention 璺?vLLM 鐨?PagedAttention 涓嶅吋瀹癸紙PagedAttention 鍋囪鍥犳灉锛夛紝鎵€浠?DiffuFlash 鐜板湪鍙兘 batch_size=1 璺戙€?*杩欐槸瀹冪殑鏈€澶ц蒋鑲嬶紝涔熸槸鎴戜滑涓嶅彇鐨勬牳蹇冪悊鐢?*銆?
#### 10.3.4 KV cache 閲忓寲锛圱urboCount锛?
**KV cache 鏄粈涔?*锛歍ransformer 鎺ㄧ悊鏃讹紝姣忕敓鎴愪竴涓?token 鎶婇偅涓綅缃殑 K锛坘ey锛夊拰 V锛坴alue锛夌煩闃靛瓨璧锋潵缁欏悗闈?token 鐢ㄣ€傞暱涓婁笅鏂囷紙128K tokens锛変笅鑳藉崰鍑犲崄 GB 鏄惧瓨銆?
**涓轰粈涔?KV cache 閲忓寲浠ュ墠闅惧仛**锛?- KV 鍒嗗竷璺熸潈閲嶄笉涓€鏍凤細**鏈夊ぇ閲?outlier**锛堜釜鍒?token / head 鐨?KV 鍊肩壒鍒ぇ锛?- 鏈寸礌 INT4 閲忓寲 KV 鈫?outlier 鎶婇噺鍖栬寖鍥存媺鐖?鈫?catastrophic accuracy loss

**TurboCount 鎬庝箞鍋?*锛堝弬鑰?KIVI / KVQuant 涓€绫绘柟娉曪級锛?1. **Per-token + Per-channel mixed quantization**锛?   - K 鐭╅樀锛歱er-channel 閲忓寲锛堟瘡涓?head_dim 缁村害鐙珛 scale锛?   - V 鐭╅樀锛歱er-token 閲忓寲锛堟瘡涓?token 鐙珛 scale锛?2. **Outlier 鍗曠嫭 FP16 瀛?*锛?% 鐨勬瀬鍊?token 涓嶉噺鍖?3. **Group 閲忓寲**锛氭瘡 128 涓€煎叡浜竴涓?scale锛屽钩鎽婄簿搴︽崯澶?
**绠楀瓙绾у疄鐜?*锛?- vLLM 0.20+ 鏀寔锛岄渶瑕?`--kv-cache-dtype fp8_e5m2` 鎴?`--kv-cache-dtype int4`
- attention kernel 鍦ㄥ仛 `softmax(Q @ K^T) @ V` 鏃?**dequant on-the-fly**
- FlashAttention-3 鏀寔 INT4/INT8 KV锛屾崯澶?< 1% accuracy

**涓轰粈涔堝闀?context 浠峰€煎法澶?*锛?
| 閰嶇疆 | 24G 4090 鏈€澶?context | A100 80G 鏈€澶?context |
|------|----------------------|----------------------|
| FP16 KV | ~32K | ~128K |
| **INT4 KV** | **~200K** | **~500K** |

**鎴戜滑 stage 9.5 杈撳叆 30K tokens 鏁寸瘒 patent锛孠V 閲忓寲鏄繀椤?*锛堜笉閲忓寲鐨勮瘽 max_model_len=8K 瑁呬笉涓嬶級銆?
#### 10.3.5 CUDA kernel fusion锛堟彁鍒颁絾涓嶆繁鍏ワ級

LoseBox 鍥㈤槦鎶?24 灞傜綉缁滆瀺鍚堟垚鍗?CUDA kernel锛岃揪鍒?M5 Max 鑳芥晥姣斻€?
**铻嶅悎鐨勬湰璐?*锛?- 鏍囧噯 PyTorch锛氭瘡涓?op (LayerNorm / GEMM / Softmax / RoPE) 鏄嫭绔?CUDA kernel
- 姣忎釜 kernel 鍚姩鏈?~5 渭s overhead锛?4 灞?脳 7 op/灞?= **168 娆?kernel launch / token**
- 168 脳 5 渭s = 840 渭s / token 鍏ㄦ槸 launch overhead锛孏PU 閮藉湪绛?driver

**Fusion 鏂规**锛?- FlashAttention锛歚Q @ K^T 鈫?softmax 鈫?@ V` 铻嶆垚涓€涓?kernel
- LayerNorm + GEMM fusion锛氱浉閭?op 鍚堝苟
- 鏋佺鐗堬細鏁翠釜 transformer block 涓€涓?kernel

**瀹炲姟**锛歷LLM 宸茬粡鍋氫簡澶ч儴鍒?fusion锛涜嚜宸卞啓寰堥毦瓒呰繃瀹冦€?*淇濇寔 vLLM up-to-date 灏卞**銆?
### 10.4 鎴戜滑椤圭洰鐨勫彇鑸?
#### 10.4.1 宸叉湁锛圥IPELINE_STAGES_v5 搂4 宸插啓锛?
| 浼樺寲 | 閫傜敤 stage | 瀹炴柦鐘舵€?|
|------|-----------|---------|
| AWQ INT4 閲忓寲 | 0.5 / 4 / 6 / 7 / 9.5 | V2.0 鍒囨湰鍦?vLLM 鏃跺惎鐢?|
| Outlines guided decoding | stage 7 | JSON 寮哄埗鍚堟硶 |
| Prefix caching | 鍏ㄩ儴 | system prompt 澶嶇敤 |
| 鍏抽棴 thinking 妯″紡 | 鍏ㄩ儴 | 鐪?30-50% 寤惰繜 |

#### 10.4.2 搴旇鍔犵殑锛堣棰戞暀鐨勶紝绔嬪埢鑳界敤锛?
| 浼樺寲 | 閫傜敤 stage | 棰勮鎻愬崌 | 宸ヤ綔閲?|
|------|-----------|---------|--------|
| **MTP n=3** | stage 7锛坒act JSON 缁撴瀯鍖栬緭鍑猴紝鍛戒腑鐜囬珮锛? stage 4锛圴LM table 鎻忚堪锛墊 **+100%** 閫熷害 | 1 琛?vLLM 閰嶇疆 |
| **KV cache INT4 閲忓寲** | **stage 9.5锛坧er-doc 鏁寸瘒 30K tokens 杈撳叆锛夆瓙 蹇呴』** | 鏄惧瓨鐪?4脳锛岃兘瑁呬笅澶т笓鍒?| 1 琛?vLLM 閰嶇疆 |
| **MTP n=1** for table 绫?stage | stage 4 table銆乻tage 6 娈佃惤鍖归厤 | +50% | 鍚屼笂 |

**MTP n=3 瀵?stage 7 鐨勫疄娴嬮鏈?*锛?- stage 7 杈撳叆 ~3K + 杈撳嚭 ~1.5K 涓ユ牸 JSON
- JSON 缁撴瀯楂樺害鍙娴嬶紙`{"fact_id": "F_xxx_001", "application": "APP_..."}` 杩欑 key 鍑犱箮蹇呯劧鍛戒腑锛?- 搴旇 **MTP 鍛戒腑鐜?70%+** 鈫?鎻愰€?2-2.5脳
- **stage 7 鍗?unit 4 sec 鈫?1.5-2 sec**

**KV INT4 瀵?stage 9.5 鐨勫叧閿綔鐢?*锛?- stage 9.5 鍗曟 LLM 璋冪敤杈撳叆 30K tokens
- FP16 KV @ 30K = ~7.5 GB锛孉100 杩樻拺寰椾綇浣?batch_size 鍙楅檺
- INT4 KV @ 30K = ~2 GB锛岃兘寮€ batch_size=16 鎻愰珮鍚炲悙 4脳
- 瀵?348 绡?stage 9.5 鍏ㄨ窇 7 鍒嗛挓 鈫?2 鍒嗛挓

#### 10.4.3 鏆備笉鍙栫殑锛堣棰戦噷鐑絾涓嶉€傚悎鎴戜滑锛?
| 浼樺寲 | 涓嶅彇鐞嗙敱 |
|------|---------|
| **DiffuFlash + DD-tree** | 鉂?涓嶆敮鎸佸骞跺彂锛涙垜浠?348 绡囪骞惰璺戞壒锛屽繀椤?vLLM continuous batching锛沚idirectional attention 璺?PagedAttention 涓嶅吋瀹?|
| **n-gram speculative decoding** (llama.cpp) | 鉂?鎴戜滑 stage 7 鏄?fresh context锛堟瘡 unit 鍏ㄦ柊 prompt锛夛紝涓嶅儚澶氳疆瀵硅瘽鏈?n-gram 澶嶇敤绌洪棿 |
| **闄嶅姛鐜囪拷 M5 鑳芥晥姣?* | 鉂?鎴戜滑 A100 涓嶇己鐢碉紝缂虹殑鏄悶鍚?|
| **GGUF / Q4_K_M**锛坙lama.cpp 璺緞锛墊 鉂?llama.cpp 涓嶄负楂樺苟鍙戣璁★紱鎴戜滑 vLLM 璺緞宸查€夊畾 |

### 10.5 vLLM 鍚姩鍛戒护锛堝疄鍔＄増锛?
V2 閮ㄧ讲鏃朵竴娆￠厤榻愭墍鏈変紭鍖栵細

```bash
vllm serve ~/models/Qwen3.6-27B-AWQ-INT4 \
  --port 8000 \
  --max-model-len 32768 \                                                     # KV 閲忓寲鍚庤兘瑁呭埌 32K
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.7 \
  --enable-prefix-caching \                                                   # 猸?system prompt 澶嶇敤
  --guided-decoding-backend outlines \                                        # 猸?stage 7 寮哄埗 JSON
  --speculative-config '{"method": "mtp", "num_speculative_tokens": 3}' \     # 猸?V2.0 鏂板 MTP
  --kv-cache-dtype fp8_e5m2 \                                                 # 猸?V2.0 鏂板 KV 閲忓寲
  --chat-template ~/templates/qwen3_no_thinking.jinja                         # 猸?鍏?thinking
```

**閰嶅**锛欱GE-M3 璺?vLLM 鍏遍┗ A100锛屾樉瀛橀厤缃細
- vLLM AWQ + KV INT4 鈮?19 GB 鏉冮噸 + 8 GB KV 姹狅紙@ max_model_len=32K, max_num_seqs=32锛? 27 GB
- BGE-M3 鈮?4 GB
- CUDA runtime + buffer 鈮?10 GB
- **A100 80G 鎬诲崰 41 GB锛屼綑 39 GB 瀹夊叏 margin**

### 10.6 浼扮畻鎬绘敹鐩?
348 绡?BASF 鍏ㄨ窇锛圛PC 闂搁棬鍚?174 绡囩湡娑傛枡锛夛細

| 閰嶇疆 | stage 7 鍗?unit | stage 9.5 鍗?doc | 鎬绘椂闂达紙32 骞跺彂锛墊
|------|-----------------|-------------------|------------------|
| V2 baseline (AWQ + outlines + prefix) | 4 sec | 30 sec | ~16 鍒嗛挓 |
| **鍔?MTP n=3** | 1.7 sec (-58%) | 13 sec (-57%) | **~7 鍒嗛挓** |
| **鍐嶅姞 KV INT4** | 1.7 sec | 13 sec锛堜絾鑳借 32K context锛墊 ~7 鍒嗛挓锛坙atency 涓嶅彉锛屼絾绋冲畾鎬?+锛墊

**鏈€澶у疄闄呮敹鐩?*锛?*348 绡囧叏璺戜粠 16 鍒嗛挓 鈫?7 鍒嗛挓锛岀渷 56% 鏃堕棿**銆?
鏇撮噸瑕佺殑鏄?**stage 9.5 鐨?30K context 涓嶅啀 OOM**锛圞V 閲忓寲璁?max_model_len=32K 钀藉湴锛夈€?
### 10.7 鐩戞帶涓庡洖婊?
**MTP 鍛戒腑鐜囩洃鎺?*锛?```python
# vLLM 鎻愪緵 metrics endpoint
# 鍏虫敞: spec_decode_acceptance_rate
# 鏈熸湜: stage 7 > 0.7锛堢粨鏋勫寲 JSON锛夛紝stage 9.5 > 0.5锛堣嚜鐒惰瑷€ NER锛?```

**鍥炴粴鏉′欢**锛?- MTP 鍛戒腑鐜?< 0.4 鈫?鍙嶈€屾參锛屽叧 MTP锛堝幓鎺?`--speculative-config`锛?- KV 閲忓寲鍚?11 绡?golden set 鎶芥牱鍑嗙‘鐜囨帀 > 5% 鈫?鍥為€€ FP16 KV
- 鎺ㄧ悊鍑虹幇 NaN / inf 鈫?鍥為€€鎵€鏈夐噺鍖栧埌 FP8

### 10.8 璺熻棰戠殑鎬诲鐓?
| 瑙嗛寤鸿 | 鎴戜滑閲囩撼 | 鐞嗙敱 |
|---------|---------|------|
| AWQ INT4 閲忓寲 | 鉁?V2.0 鍚敤 | 宸插湪 搂4 |
| MTP n=3 (V2.0 鏂板姞) | 鉁?绔嬪埢鍔?| 鎴戜滑 stage 7 鏄?JSON 楂樺懡涓巼鍦烘櫙 |
| MTP n=5 | 馃煛 涓嶄笂鏋侀檺 | 楠岃瘉寮€閿€ vs 鏀剁泭鎷愮偣鍦?n=3锛宯=5 杈归檯鏀剁泭灏?|
| DiffuFlash + DD-tree | 鉂?涓嶅彇 | 涓嶆敮鎸佸骞跺彂 |
| KV INT4 閲忓寲 (V2.0 鏂板姞) | 鉁?蹇呴』 | stage 9.5 闀?context 涓嶈兘娌℃湁 |
| 鍙屽崱 4090 P2P | N/A | 鎴戜滑 A100 鍗曞崱锛屼笉娑夊強 |
| 闄嶅姛鐜囪拷 M5 鑳芥晥姣?| 鉂?| 鎴戜滑瑕佸悶鍚愪笉瑕佺渷鐢?|

---

## 11. 闄勫綍锛氭湳璇?/ 鍏抽敭鍐崇瓥 / spec 鍋忕璁板綍

### 11.1 鏈琛?
| 鏈 | 鍚箟 |
|------|------|
| spec | private V1.2 design notes |
| Layer 0 / 1 / 2 | 鑺傜偣灞?/ 寮卞彫鍥炲眰 / 绮剧‘璇佹嵁灞?|
| FactHyperedge | Layer 2 澶氭Ы浣?JSON fact锛孨 鍏冨叧绯?|
| PassageHyperedge | Layer 1 娈佃惤鍘熸枃 + tagged_entities |
| FigureHyperedge | Layer 1 鍥捐〃锛堝弽搴斿紡 / 娴佺▼鍥剧瓑锛? VLM 鎻忚堪 |
| canonical_id | ontology 娉ㄥ唽鐨勫疄浣撳敮涓€ ID锛堝 `MAT_polyurethane`锛墊
| evidence_pointer | fact 鎸囧洖 PDF 鐨勭簿纭潗鏍囷紙page, region, row, col, cell, bbox, unit_id锛墊
| Layer A hybrid | 鍗曟潯 SQL 鍚屾椂璺戠簿纭繃婊?+ 鍚戦噺鐩镐技搴?|
| Layer B hybrid | 璺ㄥ眰 fallback锛歀ayer 2 鈫?Layer 1 鈫?鎷掔瓟 |
| propose-and-curate | LLM 鎵句笉鍒板悎閫?canonical 鏃?propose 鏂?ID 钀?sidecar 绛変汉瀹?|
| polarity_hint | inventive / comparative 鏍囪 |
| comparison_group | 鍚岃〃鍐呭彲瀵规瘮 fact 鐨?group_id |

### 11.2 鍏抽敭鍐崇瓥鐐?
| 鍐崇瓥 | 鏃堕棿 | 鐞嗙敱 |
|------|------|------|
| 閫?fact-as-hyperedge 涓嶇敤 RDF 涓夊厓缁?| V1.0 璁捐绋?| 娑傛枡 fact 鏄?N 鍏冨叧绯伙紝浜屽厓 RDF 涓俊鎭?|
| 鍗曚竴 PG + pgvector 涓嶇敤涓撶敤鍚戦噺搴?| V1.2.1 淇 7 | 6.5 涓囧悜閲忕骇杈句笉鍒颁笓鐢ㄥ簱闂ㄦ锛宧ybrid SQL 涓€鏉℃悶瀹?|
| Stage 0.5 IPC 闂搁棬 | V1.2.5 | 174 绡囬潪娑傛枡鏃?reject 鐪?楼87 |
| propose-and-curate 杞害鏉?| V1.2.3 | 11 鏉″姪鍓傝鏍?鏁戝嚭鏉?|
| Stage 9.5 鐢?LLM 涓嶇敤闈欐€佸瓧鍏?| V2.0.5 | 瀹炴祴 Tier B 4脳 浼樹簬 Tier A锛孉100 鏈湴鎺ㄧ悊 cost 鈮?0 |
| Stage 4.5 涓夎矾鍒嗘祦 | V2.0.5 | 50% 鐨?figure 绫?unit 涓嶈璺?stage 6/7 |
| Stage 4.5 soft-delete 鏇夸唬 rmtree | V1.2.7 (cli review) | 璺敱閿欒鍙仮澶嶏紝30 绉掑洖婊?|
| Stage 4.5 VLM-fail fallback to extract_facts | V1.2.7 (cli review) | preserve recall锛宻tage 7 鏈?graceful degradation |

### 11.3 spec 鍋忕璁板綍锛坈onscious deviation锛?
| 鍋忕鐐?| spec 瑕佹眰 | V2.0.5 瀹為檯 | 鐞嗙敱 |
|--------|-----------|-------------|------|
| Stage 9.5 Layer 1 鎶藉彇 | "NER + entity_tagger 澶嶇敤锛屾棤 LLM"锛坰pec 搂淇 6锛墊 single-pass per-doc LLM | 瀹炴祴 Tier A 闈欐€佸瓧鍏稿彧 101 entities锛宲er-doc LLM 405 entities锛?脳锛夈€侫100 鏈湴鎺ㄧ悊 cost 鈮?0锛屾棤闇€濡ュ崗 |
| PassageHyperedge schema | 5 瀛楁锛坱ext + page + para_index + tagged_entities + text_embedding锛墊 8 瀛楁锛堝 section_type / entity_phrases / confidence锛?| retrieval 璋冭瘯鍜屽彲瑙ｉ噴鎬ч渶瑕佽繖 3 瀛楁锛宻pec 绠€鐗堜笉澶?|
| `figures` 琛ㄧ嫭绔?| 鐙珛 figures 琛?| 鍚堝苟鍒?`figure_table_units` + `route` 瀛楁锛堣鍥撅級| 鍑忓皯鍐椾綑锛屽崟琛ㄧ储寮曟洿娓呮櫚 |
| `near_units` 瀛楁 | 鐗╃悊椤佃窛閭昏繎 卤10 椤?unit list | **鍒犻櫎** | 鐗╃悊閭昏繎 鈮?璇箟鐩稿叧锛宺etrieval 鏃舵寜 entity overlap 绠楁洿鍑?|
| Tier A/B/C 涓夋。铻嶅悎 | 瀛楁 `entities_provenance` + `source_tier` | 绠€鍖栦负 single-pass LLM锛屼絾淇濈暀 `entities_provenance` 瀛楁 | LLM 鍏ㄧ瘒鐪嬩竴娆℃瘮鐪嬪垎娈垫洿鍑嗭紱淇濈暀 provenance 缁?audit |

**鍋忕鍘熷垯**锛氬疄娴嬮┍鍔?> spec 鏁欐潯銆備换浣曞啿绐?spec 鏀?amendment锛?*涓嶆敼瀹炴柦**銆?
---

**鏂囨。缁撴潫**

| 鏂囨。鐗堟湰 | 鏃ユ湡 | 涓昏鏀瑰姩 |
|----------|------|---------|
| v1.0 | 2026-05-10 | 棣栫増瀹屾暣鎶€鏈姤鍛婏細14 stage 璇﹁В + 涓夊眰瓒呭浘 + embedding 5 瀛楁 + PG 7 琛?+ Q&A 5 绫荤ず渚?+ 璺ㄥ眰鏄犲皠 + 璺嚎鍥?+ spec 鍋忕璁板綍 |
| v1.1 | 2026-05-10 | 鏂板 搂9 Agent Memory 璁捐绔犺妭锛圴2.5+ 璋冪爺涓級锛歁AGMA / 璞嗗寘 / Karpathy 涓夊瀵圭収 + 鍙栬垗璇存槑锛堟槑鍐欎笉鍙栧鍏崇郴鍥惧拰 Causal Graph 鐨?5 涓悊鐢憋級+ 3 灞?memory 鏋舵瀯 + Compaction 鍗忚 + 涓?patent KG 闅旂杈圭晫 + V1/V2/V3 璺嚎鍥?+ 4 涓皟鐮斾腑寮€鏀鹃棶棰?|
| **v1.2** | **2026-05-10** | **鏂板 搂10 鍩哄骇妯″瀷鎺ㄧ悊鍔犻€熺珷鑺傦紙V2.0 vLLM 閮ㄧ讲鏃跺惎鐢級锛氳棰戝疄娴嬭矾绾垮浘 20鈫?84 token/s + 5 涓畻瀛愬師鐞嗘繁鍏ワ紙AWQ / MTP / DiffuFlash / KV閲忓寲 / CUDA fusion锛? 鎴戜滑椤圭洰鐨勫彇鑸嶏紙鍙?MTP n=3 + KV INT4锛屼笉鍙?DiffuFlash 鐨?4 鏉＄悊鐢憋級+ vLLM 鍚姩鍛戒护瀹炲姟鐗?+ 16鈫? 鍒嗛挓鏀剁泭浼扮畻 + MTP 鍛戒腑鐜囩洃鎺т笌鍥炴粴绛栫暐** |
