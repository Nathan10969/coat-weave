-- ============================================================
--  seed_must_merge_starter.sql — V1.2.2 § config 2 (subset)
--  ~15 starter rows; canonical_ids must already exist
--  in `nodes` (loaded by seed_canonical_starter.sql /
--  seed_property_directionality.sql).
-- ============================================================

INSERT INTO must_merge (alias_text, canonical_id, merge_type, confidence, source_evidence) VALUES
  ('KH-550',                                 'MAT_silane_kh550',                'trade_name_of',         'high',
    'Domestic Chinese silane naming (APTES = γ-aminopropyltriethoxysilane)'),
  ('A-1100',                                 'MAT_silane_kh550',                'trade_name_of',         'high',
    'Momentive/GE legacy designation'),
  ('APTES',                                  'MAT_silane_kh550',                'abbreviation_of',       'high',
    'Standard literature acronym'),
  ('γ-氨丙基三乙氧基硅烷',                    'MAT_silane_kh550',                'chinese_translation_of','high',
    'Chinese chemical name'),
  ('KH-560',                                 'MAT_silane_kh560',                'trade_name_of',         'high',
    'GPTMS / glycidoxypropyl-trimethoxysilane'),
  ('TiO2',                                   'MAT_titanium_dioxide_rutile',     'formula_to_name',       'high',
    'When a patent says "TiO2" without grade, default to rutile for exterior context'),
  ('TiO₂',                                   'MAT_titanium_dioxide_rutile',     'formula_to_name',       'high',
    'Subscript Unicode variant'),
  ('钛白粉',                                  'MAT_titanium_dioxide_rutile',     'chinese_translation_of','med',
    '"Titanium-white powder" — colloquial; rutile/anatase unspecified'),
  ('CaCO3',                                  'MAT_calcium_carbonate',           'formula_to_name',       'high',
    '—'),
  ('碳酸钙',                                  'MAT_calcium_carbonate',           'chinese_translation_of','high',
    '—'),
  ('Tinuvin 292',                            'MAT_HALS_Tinuvin_292',            'trade_name_of',         'high',
    'BASF polymeric HALS'),
  ('HALS-292',                               'MAT_HALS_Tinuvin_292',            'abbreviation_of',       'high',
    'Common abbreviation in tables'),
  ('Tinuvin 1577',                           'MAT_UV_absorber_Tinuvin_1577',    'trade_name_of',         'high',
    'BASF triazine UV absorber'),
  ('UV-1577',                                'MAT_UV_absorber_Tinuvin_1577',    'abbreviation_of',       'high',
    'Common abbreviation'),
  ('HDI trimer',                             'MAT_HDI_trimer',                  'synonym_of',            'high',
    'Same as MAT_HDI_trimer canonical'),
  ('Hexamethylene diisocyanate trimer',      'MAT_HDI_trimer',                  'synonym_of',            'high',
    'Spelled-out form'),
  ('苯丙乳液',                                'MAT_styrene_acrylic_latex',       'chinese_translation_of','high',
    '苯=styrene 丙=acrylate'),
  ('硅丙乳液',                                'MAT_silicone_acrylic_emulsion',   'chinese_translation_of','high',
    '硅=silicone 丙=acrylate'),
  ('实施例',                                  'APP_architectural_exterior_coating', 'chinese_translation_of', 'low',
    'Polarity-only token; bound to APP for now and will be promoted to a polarity rule')
ON CONFLICT (alias_text) DO NOTHING;
