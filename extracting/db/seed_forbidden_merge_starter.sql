-- ============================================================
--  seed_forbidden_merge_starter.sql — V1.2.2 § config 1 (subset)
--  ~15 starter rows, drawn from the 58-row config table 1.
--  Convention: store with entity_a < entity_b (lexicographic) so the
--  pair is canonical regardless of insertion order.
-- ============================================================

INSERT INTO forbidden_merge (entity_a, entity_b, reason_type, explanation, risk_severity) VALUES
  ('acrylic_emulsion',              'styrene_acrylic_latex',
     'chemistry_diff',
     'Pure acrylic (BA/MMA/AA) ≠ styrene-acrylic (St/BA/AA); UV stability profile differs.',
     'high'),

  ('acrylic_emulsion',              'silicone_acrylic_emulsion',
     'chemistry_diff',
     'Silicone-modified acrylic carries siloxane segments → different weatherability and hydrophobicity.',
     'high'),

  ('aminosilane_KH550',             'epoxysilane_KH560',
     'chemistry_diff',
     '-NH2 vs glycidyloxy reactive group → totally different cure mechanism.',
     'high'),

  ('architectural_coating',         'automotive_coating',
     'scope_diff',
     'Cure schedule (RT vs bake), substrate, and standards differ.',
     'high'),

  ('architectural_exterior_coating','architectural_interior_coating',
     'scope_diff',
     'Different durability requirements (UV, chalking).',
     'high'),

  ('adhesion_cross_cut_ASTM_D3359', 'adhesion_cross_cut_ISO2409',
     'standard_diff',
     'ISO 0=best→5=worst; ASTM 5B=best→0B=worst — meaning inverted.',
     'high'),

  ('adhesion_cross_cut',            'adhesion_pull_off',
     'measurement_diff',
     'ISO 2409 grade (0-5, lower better) ≠ ISO 4624 MPa (higher better).',
     'high'),

  ('comparative_example',           'example',
     'polarity_hint',
     'Comparative example = baseline / often worse-performing. Treating it as positive evidence flips answer direction.',
     'high'),

  ('film_thickness_dry',            'film_thickness_wet',
     'measurement_diff',
     'WFT vs DFT — related by solids; mixing them in tables corrupts dosage maths.',
     'high'),

  ('fluorocarbon_FEVE',             'fluorocarbon_PVDF',
     'chemistry_diff',
     'FEVE solution-coatable / RT-curable; PVDF requires bake.',
     'high'),

  ('fluorocarbon_resin',            'fluorosilicone_resin',
     'chemistry_diff',
     'F-Si hybrid (Si-O backbone with F sidechains) ≠ FEVE/PVDF fluorocarbon.',
     'high'),

  ('TiO2_anatase',                  'TiO2_rutile',
     'chemistry_diff',
     'Rutile is exterior-grade (UV-stable); anatase is photocatalytic / chalking-prone.',
     'high'),

  ('water_absorption',              'water_resistance',
     'measurement_diff',
     'Directionality opposite (high WR good, low WA good).',
     'high'),

  ('hydrophobicity',                'water_resistance',
     'measurement_diff',
     'Bulk vs surface property; different test methods.',
     'high'),

  ('water_repellency',              'water_resistance',
     'measurement_diff',
     'Immersion resistance ≠ surface beading.',
     'high'),

  ('weatherability_QUV',            'weatherability_xenon_arc',
     'standard_diff',
     'UVB/UVA fluorescent vs full-spectrum xenon — acceleration factors differ.',
     'high')
ON CONFLICT (entity_a, entity_b) DO NOTHING;
