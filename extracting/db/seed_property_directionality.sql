-- ============================================================
--  seed_property_directionality.sql
--  22 properties — V1.2.2 § config table 3
--  Order: register PROP_* nodes first, then property_registry rows.
-- ============================================================

-- ---- 1. PROP_* canonical nodes ----
INSERT INTO nodes (canonical_id, node_type, canonical_name, chinese_name, description) VALUES
  ('PROP_water_absorption',             'PROP', 'water_absorption',             '吸水率',           'Mass uptake after immersion'),
  ('PROP_contact_angle',                'PROP', 'contact_angle',                '静态水接触角',     'Sessile-drop static contact angle'),
  ('PROP_adhesion_cross_cut',           'PROP', 'adhesion_cross_cut',           '划格法附着力',     'ISO 2409 / ASTM D3359 cross-cut grade'),
  ('PROP_adhesion_pull_off',            'PROP', 'adhesion_pull_off',            '拉拔法附着力',     'ISO 4624 / ASTM D4541 pull-off MPa'),
  ('PROP_scrub_resistance',             'PROP', 'scrub_resistance',             '耐擦洗性',         'Wet scrub cycles to failure'),
  ('PROP_VOC',                          'PROP', 'VOC',                          'VOC含量',          'Volatile organic compound content g/L'),
  ('PROP_gloss_20deg',                  'PROP', 'gloss_20deg',                  '20度光泽',         '20-degree specular gloss (high-gloss class)'),
  ('PROP_gloss_60deg',                  'PROP', 'gloss_60deg',                  '60度光泽',         '60-degree specular gloss (medium-gloss class)'),
  ('PROP_viscosity',                    'PROP', 'viscosity',                    '黏度',             'Viscosity (KU / mPa·s / ICI poise)'),
  ('PROP_water_whitening',              'PROP', 'water_whitening',              '起白',             'Visual whitening after water exposure'),
  ('PROP_pencil_hardness',              'PROP', 'pencil_hardness',              '铅笔硬度',         '6B..HB..6H pencil scale'),
  ('PROP_weatherability_QUV',           'PROP', 'weatherability_QUV',           'QUV人工加速老化',  'Hours to gloss/colour failure under QUV'),
  ('PROP_chalk_resistance',             'PROP', 'chalk_resistance',             '抗粉化性',         'Chalking grade after weathering'),
  ('PROP_dirt_pickup_resistance',       'PROP', 'dirt_pickup_resistance',       '抗沾污性',         'ΔL or %reflectance loss after carbon-slurry'),
  ('PROP_salt_spray',                   'PROP', 'salt_spray',                   '耐中性盐雾',       'NSS hours to defect'),
  ('PROP_impact',                       'PROP', 'impact',                       '抗冲击性',         'Falling-weight or DuPont impact'),
  ('PROP_flexibility',                  'PROP', 'flexibility',                  '柔韧性',           'Mandrel mm or T-bend grade'),
  ('PROP_freeze_thaw',                  'PROP', 'freeze_thaw',                  '冻融稳定性',       'In-can freeze-thaw cycles passed'),
  ('PROP_opacity',                      'PROP', 'opacity',                      '遮盖力',           'Contrast ratio or g/m^2 to hide'),
  ('PROP_drying_time',                  'PROP', 'drying_time',                  '干燥时间',         'Set-to-touch / through-dry minutes'),
  ('PROP_film_thickness',               'PROP', 'film_thickness',               '干膜厚度',         'DFT in micrometres'),
  ('PROP_scratch_resistance',           'PROP', 'scratch_resistance',           '耐划伤性',         'Residual gloss after Amtec / Crockmeter'),
  -- ---- V1.2.2 supplement (BASF Examples coverage) ----
  ('PROP_composition_weight_percent',   'PROP', 'composition_weight_percent',   '组分重量百分比',   'Component amount in formulation, wt.-%')
ON CONFLICT (canonical_id) DO NOTHING;


-- ---- 2. property_registry rows ----
INSERT INTO property_registry
    (property_id, english_name, chinese_name, typical_unit, directionality,
     requires_baseline, comparable_test_methods, related_but_not_equivalent,
     is_surface_property, is_aging_property, notes) VALUES
  ('PROP_water_absorption',         'water absorption',         '吸水率',
     '% (mass)', 'lower_is_better', TRUE,
     '["TEST_GB_T_5208","TEST_GB_T_9755_6","TEST_ISO_62"]'::jsonb,
     '["PROP_water_resistance","PROP_water_whitening","PROP_contact_angle"]'::jsonb,
     FALSE, FALSE, '24h vs 7d immersion materially differ; record duration.'),

  ('PROP_contact_angle',            'static water contact angle','静态水接触角',
     'degree', 'higher_is_better', FALSE,
     '["TEST_ASTM_D7334"]'::jsonb,
     '["PROP_water_absorption"]'::jsonb,
     TRUE, FALSE, '>90 hydrophobic; >150 superhydrophobic.'),

  ('PROP_adhesion_cross_cut',       'cross-cut adhesion',       '划格法附着力',
     'grade', 'depends', FALSE,
     '["TEST_ISO_2409","TEST_GB_T_9286","TEST_ASTM_D3359"]'::jsonb,
     '["PROP_adhesion_pull_off"]'::jsonb,
     FALSE, FALSE, 'ISO 0=best,5=worst; ASTM 5B=best,0B=worst — DO NOT mix.'),

  ('PROP_adhesion_pull_off',        'pull-off adhesion',        '拉拔法附着力',
     'MPa', 'higher_is_better', TRUE,
     '["TEST_ISO_4624","TEST_GB_T_5210","TEST_ASTM_D4541"]'::jsonb,
     '["PROP_adhesion_cross_cut"]'::jsonb,
     FALSE, FALSE, 'Failure mode (cohesive/adhesive) is part of the fact.'),

  ('PROP_scrub_resistance',         'wet scrub resistance',     '耐擦洗性',
     'cycles', 'higher_is_better', TRUE,
     '["TEST_GB_T_9266","TEST_ISO_11998","TEST_ASTM_D2486"]'::jsonb,
     '["PROP_dirt_pickup_resistance"]'::jsonb,
     FALSE, FALSE, 'ISO 11998 reports loss in um — direction reverses.'),

  ('PROP_VOC',                      'VOC content',              'VOC含量',
     'g/L', 'lower_is_better', FALSE,
     '["TEST_EPA_Method_24","TEST_ISO_11890_2","TEST_GB_18582"]'::jsonb,
     '[]'::jsonb,
     FALSE, FALSE, 'Method 24 vs ISO 11890 can differ by tens of g/L.'),

  ('PROP_gloss_20deg',              '20-degree specular gloss', '20度光泽',
     'GU', 'depends', FALSE,
     '["TEST_ISO_2813","TEST_GB_T_9754","TEST_ASTM_D523"]'::jsonb,
     '["PROP_gloss_60deg"]'::jsonb,
     TRUE, FALSE, 'High-gloss class — higher is better; matte class reverses.'),

  ('PROP_gloss_60deg',              '60-degree specular gloss', '60度光泽',
     'GU', 'depends', FALSE,
     '["TEST_ISO_2813","TEST_GB_T_9754","TEST_ASTM_D523"]'::jsonb,
     '["PROP_gloss_20deg"]'::jsonb,
     TRUE, FALSE, 'Geometry MUST be recorded.'),

  ('PROP_viscosity',                'viscosity',                '黏度',
     'mPa·s / KU / poise', 'depends', FALSE,
     '["TEST_GB_T_9269","TEST_GB_T_9751","TEST_ASTM_D562","TEST_ASTM_D4287"]'::jsonb,
     '[]'::jsonb,
     FALSE, FALSE, 'KU vs ICI not interconvertible.'),

  ('PROP_water_whitening',          'water whitening / blushing','起白',
     'grade or dE', 'lower_is_better', TRUE,
     '["TEST_visual_after_immersion"]'::jsonb,
     '["PROP_water_resistance","PROP_water_absorption"]'::jsonb,
     FALSE, FALSE, '0 (none) – 5 (severe).'),

  ('PROP_pencil_hardness',          'pencil hardness',          '铅笔硬度',
     'pencil scale', 'higher_is_better', FALSE,
     '["TEST_GB_T_6739","TEST_ISO_15184","TEST_ASTM_D3363"]'::jsonb,
     '[]'::jsonb,
     FALSE, FALSE, 'Different physics from pendulum hardness.'),

  ('PROP_weatherability_QUV',       'accelerated weathering (QUV)','QUV人工加速老化',
     'h to dE/dGloss', 'higher_is_better', TRUE,
     '["TEST_ISO_16474_3","TEST_ASTM_G154","TEST_GB_T_14522"]'::jsonb,
     '["PROP_chalk_resistance"]'::jsonb,
     FALSE, TRUE, 'UVA-340 vs UVB-313 changes hours-to-failure.'),

  ('PROP_chalk_resistance',         'chalk resistance',         '抗粉化性',
     'grade 0-10', 'higher_is_better', TRUE,
     '["TEST_ASTM_D4214","TEST_GB_T_1766"]'::jsonb,
     '["PROP_weatherability_QUV"]'::jsonb,
     FALSE, TRUE, 'TiO2 grade dominates (rutile vs anatase).'),

  ('PROP_dirt_pickup_resistance',   'dirt pickup resistance',   '抗沾污性',
     'dL or %', 'higher_is_better', TRUE,
     '["TEST_GB_T_9780","TEST_ASTM_D3719"]'::jsonb,
     '["PROP_scrub_resistance"]'::jsonb,
     TRUE, FALSE, 'Major selling point in Chinese architectural patents.'),

  ('PROP_salt_spray',               'neutral salt spray',       '耐中性盐雾',
     'h to defect', 'higher_is_better', TRUE,
     '["TEST_ISO_9227_NSS","TEST_GB_T_1771","TEST_ASTM_B117"]'::jsonb,
     '[]'::jsonb,
     FALSE, FALSE, 'For coated metal substrates.'),

  ('PROP_impact',                   'impact resistance',        '抗冲击性',
     'kg·cm or J', 'higher_is_better', TRUE,
     '["TEST_GB_T_1732","TEST_ASTM_D2794","TEST_ISO_6272"]'::jsonb,
     '["PROP_flexibility"]'::jsonb,
     FALSE, FALSE, 'Method (DuPont vs falling weight) MUST be recorded.'),

  ('PROP_flexibility',              'flexibility',              '柔韧性',
     'mm or T-bend', 'depends', TRUE,
     '["TEST_GB_T_1731","TEST_ISO_1519","TEST_ASTM_D522","TEST_ASTM_D4145"]'::jsonb,
     '["PROP_impact"]'::jsonb,
     FALSE, FALSE, 'Mandrel: lower mm better. T-bend: lower T better.'),

  ('PROP_freeze_thaw',              'in-can freeze-thaw',       '冻融稳定性',
     'cycles', 'higher_is_better', FALSE,
     '["TEST_GB_T_9268","TEST_ASTM_D2243"]'::jsonb,
     '[]'::jsonb,
     FALSE, FALSE, 'In-can vs dry-film F-T resistance are distinct.'),

  ('PROP_opacity',                  'opacity / hiding power',   '遮盖力',
     'CR or g/m^2', 'depends', TRUE,
     '["TEST_ISO_6504_3","TEST_GB_T_13452_4","TEST_ASTM_D2805"]'::jsonb,
     '[]'::jsonb,
     FALSE, FALSE, 'Direction inverts with metric.'),

  ('PROP_drying_time',              'drying time',              '干燥时间',
     'min', 'depends', TRUE,
     '["TEST_GB_T_1728","TEST_ASTM_D1640","TEST_ISO_9117"]'::jsonb,
     '[]'::jsonb,
     FALSE, FALSE, 'Stage MUST be specified.'),

  ('PROP_film_thickness',           'dry film thickness',       '干膜厚度',
     'um', 'depends', FALSE,
     '["TEST_ISO_2808","TEST_GB_T_13452_2","TEST_ASTM_D7091"]'::jsonb,
     '[]'::jsonb,
     FALSE, FALSE, 'Both a test condition and a dependent variable.'),

  ('PROP_scratch_resistance',       'scratch resistance',       '耐划伤性',
     'GU or %', 'higher_is_better', TRUE,
     '["TEST_AMTEC_Kistler","TEST_Crockmeter"]'::jsonb,
     '["PROP_gloss_20deg","PROP_gloss_60deg"]'::jsonb,
     TRUE, FALSE, 'Reported as residual gloss after N strokes.')
ON CONFLICT (property_id) DO NOTHING;
