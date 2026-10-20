-- ============================================================
--  seed_canonical_starter.sql — ~30 starter canonical nodes.
--  Bootstraps Material / Application / Substrate / Process / TestMethod.
--  Property nodes live in seed_property_directionality.sql.
-- ============================================================

INSERT INTO nodes (canonical_id, node_type, canonical_name, chinese_name, description) VALUES
  -- ---- Materials (resins) ----
  ('MAT_acrylic_resin',                       'MAT', 'acrylic_resin',                       '丙烯酸树脂',     'Generic acrylic resin (solvent or water)'),
  ('MAT_pure_acrylic_emulsion',               'MAT', 'pure_acrylic_emulsion',               '纯丙乳液',       'No styrene, no silicone modifier'),
  ('MAT_styrene_acrylic_latex',               'MAT', 'styrene_acrylic_latex',               '苯丙乳液',       'St / acrylate copolymer dispersion'),
  ('MAT_silicone_acrylic_emulsion',           'MAT', 'silicone_acrylic_emulsion',           '硅丙乳液',       'Si-modified acrylic dispersion'),
  ('MAT_polyurethane_dispersion',             'MAT', 'polyurethane_dispersion',             '水性聚氨酯',     'Waterborne PUD'),
  ('MAT_2K_PU_OH_acrylic',                    'MAT', '2K_PU_OH_acrylic',                    '双组份OH丙烯酸聚氨酯', '2K solventborne polyurethane with OH-acrylic resin'),
  ('MAT_fluorocarbon_FEVE',                   'MAT', 'fluorocarbon_FEVE',                   'FEVE氟碳',       'Solution-coatable FEVE'),
  ('MAT_fluorocarbon_PVDF',                   'MAT', 'fluorocarbon_PVDF',                   'PVDF氟碳',       'Bake-cure PVDF'),

  -- ---- Materials (crosslinkers / additives) ----
  ('MAT_HDI_trimer',                          'MAT', 'HDI_trimer',                          'HDI三聚体',      'Hexamethylene diisocyanate trimer (NCO crosslinker)'),
  ('MAT_HALS_Tinuvin_292',                    'MAT', 'HALS_Tinuvin_292',                    'HALS光稳定剂292','BASF HALS, polymeric type'),
  ('MAT_UV_absorber_Tinuvin_1577',            'MAT', 'UV_absorber_Tinuvin_1577',            'UV吸收剂1577',   'Triazine UV absorber'),
  ('MAT_silane_kh550',                        'MAT', 'silane_kh550_aminopropyl_triethoxysilane','KH-550硅烷', 'Aminopropyl-triethoxysilane coupling agent'),
  ('MAT_silane_kh560',                        'MAT', 'silane_kh560_glycidoxypropyl_trimethoxysilane','KH-560硅烷','Glycidoxypropyl-trimethoxysilane'),
  ('MAT_titanium_dioxide_rutile',             'MAT', 'titanium_dioxide_rutile',             '金红石型钛白粉', 'R-type TiO2 (exterior grade)'),
  ('MAT_calcium_carbonate',                   'MAT', 'calcium_carbonate',                   '碳酸钙',         'Generic CaCO3 extender'),

  -- ---- Applications ----
  ('APP_automotive_oem_clearcoat',            'APP', 'automotive_oem_clearcoat',            '汽车OEM清漆',    'OEM topcoat clear layer'),
  ('APP_automotive_oem_basecoat',             'APP', 'automotive_oem_basecoat',             '汽车OEM色漆',    'OEM colour basecoat'),
  ('APP_architectural_exterior_coating',      'APP', 'architectural_exterior_coating',      '建筑外墙涂料',   'Exterior wall / facade coating'),
  ('APP_architectural_interior_coating',      'APP', 'architectural_interior_coating',      '建筑内墙涂料',   'Interior wall coating'),
  ('APP_industrial_protective_coating',       'APP', 'industrial_protective_coating',       '工业防腐涂料',   'Heavy-duty corrosion protection'),

  -- ---- Substrates ----
  ('SUB_steel_CRS_phosphated',                'SUB', 'steel_CRS_phosphated',                '磷化冷轧钢板',   'Phosphated cold-rolled steel'),
  ('SUB_steel_HDG',                           'SUB', 'steel_HDG',                           '热镀锌钢板',     'Hot-dip galvanised steel'),
  ('SUB_aluminum',                            'SUB', 'aluminum',                            '铝板',           'Bare aluminium panel'),
  ('SUB_cement_mortar_block',                 'SUB', 'cement_mortar_block',                 '水泥砂浆块',     'Cement-mortar block (architectural)'),
  ('SUB_gypsum_board',                        'SUB', 'gypsum_board',                        '石膏板',         'Gypsum / plasterboard'),
  ('SUB_steel_panel',                         'SUB', 'steel_panel',                         '钢板',           'Generic steel panel (unspecified pretreatment)'),

  -- ---- Processes ----
  ('PROC_spray_apply',                        'PROC', 'spray_apply',                        '喷涂',           'Spray application'),
  ('PROC_thermal_cure',                       'PROC', 'thermal_cure',                       '热固化',         'Bake cure (e.g. 140C × 22 min)'),
  ('PROC_ambient_cure',                       'PROC', 'ambient_cure',                       '常温固化',       '23C / 50%RH × 7 days'),
  ('PROC_CED',                                'PROC', 'CED_cathodic_electrodeposition',     '阴极电泳',       'Cathodic electrocoat'),
  ('PROC_dipping',                            'PROC', 'dipping',                            '浸涂',           'Dip-coating application'),
  ('PROC_basecoat_apply',                     'PROC', 'basecoat_apply',                     '色漆涂装',       'Basecoat application stage in multi-layer system'),

  -- ---- Test methods ----
  ('TEST_ISO_2813',                           'TEST', 'ISO_2813',                           '光泽测试ISO2813', 'Specular gloss 20/60/85'),
  ('TEST_ISO_2409',                           'TEST', 'ISO_2409',                           '划格法ISO2409',   'Cross-cut adhesion grade 0..5'),
  ('TEST_ISO_4624',                           'TEST', 'ISO_4624',                           '拉拔法ISO4624',   'Pull-off adhesion MPa'),
  ('TEST_ISO_9227_NSS',                       'TEST', 'ISO_9227_NSS',                       '中性盐雾ISO9227', 'NSS salt spray hours'),
  ('TEST_ISO_11998',                          'TEST', 'ISO_11998',                          '湿擦洗ISO11998',  'Wet scrub abrasion'),
  ('TEST_AMTEC_Kistler',                      'TEST', 'AMTEC_Kistler',                      'Amtec划痕',       'Amtec-Kistler 10-stroke residual gloss'),
  ('TEST_ISO_2808',                           'TEST', 'ISO_2808',                           '膜厚ISO2808',     'Dry film thickness measurement (DFT)')
ON CONFLICT (canonical_id) DO NOTHING;
