Add Durable Medical Equipment (DME) data to lora_training local Postgres database. Enables PT Assistant to reference post-operative equipment recommendations when chatting about total knee arthroplasty (TKA) recovery.

**Goal**: Patient can ask "What equipment do I need after my knee replacement?" → system references DME table with clinical recommendations, pricing, duration, FAQs.

**Data Source**: `/Users/metatron3/repos/lora_training/symlink_docs/equipment/dme_tkr.md`

---

## Part 1: Create DME Table Schema

```sql
CREATE TABLE dme_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  category VARCHAR(100) NOT NULL,  -- 'Ambulatory Aids', 'Cold Therapy', 'Bracing', etc.
  name VARCHAR(255) NOT NULL,       -- 'Front-Wheel Walker', 'Polar Care Wave', etc.
  description TEXT,                  -- Purpose and key notes
  common_use_duration VARCHAR(100), -- '1–3 weeks', '21 days', 'as-needed'
  typical_frequency VARCHAR(100),   -- '20–30 minutes, several times per day'
  insurance_coverage VARCHAR(50),   -- 'Typically covered', 'Not covered', 'Varies'
  cash_price_low DECIMAL(10,2),    -- Min cost if uninsured
  cash_price_high DECIMAL(10,2),   -- Max cost/rental period
  pricing_unit VARCHAR(50),         -- 'One-time', 'Per week', 'Rental'
  faqs JSONB,                       -- Store FAQ Q&A pairs
  clinical_indication TEXT,         -- When/why prescribed
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_dme_category ON dme_items(category);
```

---

## Part 2: Insert DME Data from dme_tkr.md

```sql
BEGIN;

-- Ambulatory Aids Category

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency, 
  insurance_coverage, cash_price_low, pricing_unit, faqs, clinical_indication
) VALUES (
  'Ambulatory Aids',
  'Front-Wheel Walker (Standard)',
  'Provides stability, balance, and fall prevention during early recovery. Most commonly recommended ambulatory aid after TKA. Helps offload weight and protect the surgical knee.',
  '1–3 weeks',
  'As needed for ambulation',
  'Typically covered',
  NULL,
  NULL,
  '{"How long will I need the walker?": "Most patients use a walker for 1–3 weeks, then transition to a cane or walking independently.", "Can I use stairs with a walker?": "Stairs are usually navigated without the walker using proper technique taught by physical therapy.", "Is this required?": "For most patients, yes, at least initially, for safety."}'::JSONB,
  'Safety and stability during early post-op recovery'
);

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, cash_price_low, pricing_unit, faqs, clinical_indication
) VALUES (
  'Ambulatory Aids',
  'Crutches',
  'Alternative mobility support for patients with good balance and upper-body strength. Less common than walkers.',
  'Variable',
  'As needed for ambulation',
  'Typically covered',
  NULL,
  NULL,
  '{"Why would crutches be used instead of a walker?": "Some patients prefer crutches or are already trained in their use.", "Are crutches common after knee replacement?": "Less common than walkers."}'::JSONB,
  'Alternative for patients with upper-body strength and preference'
);

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, cash_price_low, pricing_unit, faqs, clinical_indication
) VALUES (
  'Ambulatory Aids',
  'Cane (Later Transition)',
  'Assists balance during later stages of recovery.',
  '2–8 weeks (after walker)',
  'As needed for ambulation',
  'Typically covered',
  NULL,
  NULL,
  '{"When do I switch to a cane?": "Typically after walker use, when strength and balance improve.", "Is a cane used right after surgery?": "Rarely, unless approved by physical therapy."}'::JSONB,
  'Balance support during mid-recovery phase'
);

-- Bathroom Safety Equipment

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, faqs, clinical_indication
) VALUES (
  'Bathroom Safety',
  'Bedside Commode / 3-in-1 Commode',
  'Improves toilet safety and reduces risk of falls. Includes bedside commode, elevated toilet seat, and safety rails over toilet.',
  'Temporary (until mobility improves)',
  'As needed',
  'Typically covered',
  '{"Why do I need this?": "Standard toilets are often too low after knee surgery.", "How long will I use it?": "Usually temporary, until mobility improves."}'::JSONB,
  'Safety during bathroom use post-op'
);

-- Cold Therapy Units

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, cash_price_low, cash_price_high, pricing_unit, faqs, clinical_indication
) VALUES (
  'Cold Therapy',
  'Cold Therapy Unit (Ice & Water) - Polar Care Cube',
  'Motorized circulating cold water system using ice and water. No compression. Reduces swelling and pain, helps decrease reliance on pain medication.',
  'Several weeks',
  '20–30 minutes at a time, several times per day',
  'Typically not covered',
  270.00,
  270.00,
  'One-time purchase',
  '{"How often should I use it?": "20–30 minutes at a time, several times per day.", "Can I sleep with it on?": "No. Use only while awake.", "What if it stops cooling?": "Check ice level, water level, and ensure tubing is connected.", "Is it covered by insurance?": "Typically not covered."}'::JSONB,
  'Pain and swelling management'
);

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, cash_price_low, cash_price_high, pricing_unit, faqs, clinical_indication
) VALUES (
  'Cold Therapy',
  'Cold Therapy with Pneumatic Compression - Polar Care Wave',
  'Circulating cold water with pneumatic compression to reduce swelling. Improves circulation and useful after activity or physical therapy.',
  'Several weeks',
  '20–30 minutes at a time, several times per day',
  'Typically not covered',
  450.00,
  450.00,
  'One-time purchase',
  '{"How often should I use it?": "20–30 minutes at a time, several times per day.", "Can I sleep with it on?": "No. Use only while awake.", "Does it still use ice and water?": "Yes.", "What does compression do?": "Helps move fluid away from the surgical area.", "Is compression required?": "Physician-dependent."}'::JSONB,
  'Enhanced swelling reduction with circulation improvement'
);

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, cash_price_low, cash_price_high, pricing_unit, faqs, clinical_indication
) VALUES (
  'Cold Therapy',
  'Cold Compression Unit Without Ice - Therm-X',
  'Does not require ice or water. Provides cold therapy with optional heat component (used later in recovery). Convenient and portable.',
  'Several weeks (cold), later post-op (heat)',
  '20–30 minutes at a time, several times per day',
  'Typically not covered',
  375.00,
  375.00,
  'One-time purchase',
  '{"How often should I use it?": "20–30 minutes at a time, several times per day.", "Can I sleep with it on?": "No. Use only while awake.", "When is heat used?": "Typically several weeks post-op if recommended.", "Can I use heat early after surgery?": "No, unless directed by your physician.", "Is it covered by insurance?": "Typically not covered."}'::JSONB,
  'Convenient cold therapy without ice management; heat for later recovery'
);

-- DVT Prophylaxis

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, cash_price_low, cash_price_high, pricing_unit, faqs, clinical_indication
) VALUES (
  'DVT Prevention',
  'Sequential Compression Pump - Breg DVT Guardian',
  'Reduces risk of blood clots by improving circulation in the legs.',
  '2–4 weeks',
  'Several hours daily (per physician instructions)',
  'Coverage varies',
  295.00,
  295.00,
  'One-time purchase',
  '{"Why do I need this at home?": "Blood clot risk remains after discharge.", "How many hours a day should I use it?": "Follow physician instructions, often several hours daily.", "Can I walk while wearing it?": "No. Remove before ambulation.", "What if it alarms or stops working?": "Check tubing, power source, and garment placement."}'::JSONB,
  'Blood clot prevention post-op'
);

-- Knee Bracing

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, faqs, clinical_indication
) VALUES (
  'Knee Bracing',
  'Knee Immobilizer',
  'Temporary stabilization if weakness or instability is present.',
  'Short-term (variable)',
  'As ordered',
  'Typically covered if ordered',
  '{"Do all patients need this?": "No. Only if ordered.", "How long is it worn?": "Typically short-term."}'::JSONB,
  'Support for weakness or instability (physician-ordered only)'
);

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, faqs, clinical_indication
) VALUES (
  'Knee Bracing',
  'Post-Op Range of Motion Knee Brace',
  'Controlled motion support with compression pump and cold gel packs.',
  'Variable (physician-dependent)',
  'As ordered',
  'Coverage varies',
  '{"Is this routine?": "No. Used in specific cases.", "Can I remove it?": "Follow physician instructions."}'::JSONB,
  'Controlled motion and support (specific cases only)'
);

-- Neuromuscular Stimulation

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, faqs, clinical_indication
) VALUES (
  'Muscle Rehabilitation',
  'NMES Unit - Twin Stim IV',
  'Re-educates quadriceps muscles, improves strength and function, reduces muscle atrophy.',
  '2–8 weeks (variable)',
  'Typically 1–2 sessions per day',
  'Coverage varies',
  '{"How often should I use it?": "Typically 1–2 sessions per day.", "Does it hurt?": "No. Sensation should be comfortable.", "When do I stop using it?": "When quad strength improves or as directed by your physician or therapist."}'::JSONB,
  'Quadriceps muscle re-education and strength'
);

-- CPM Machine

INSERT INTO dme_items (
  category, name, description, common_use_duration, typical_frequency,
  insurance_coverage, cash_price_low, cash_price_high, pricing_unit, faqs, clinical_indication
) VALUES (
  'Motion Therapy',
  'Continuous Passive Motion (CPM) Machine',
  'Maintains joint motion and helps prevent stiffness. Typical protocol: 6–8 hours per day, 2 hours on/2 hours off. Start at 0° extension to 30° flexion, increase 5–10 degrees daily.',
  '21 days (surgeon-specific)',
  '6–8 hours per day (2 on / 2 off cycles)',
  'Coverage varies',
  375.00,
  375.00,
  'Per 2-week rental, $125 per additional week',
  '{"Is CPM required for all patients?": "No. Surgeon-specific.", "Can I sleep in it?": "Generally no, unless instructed.", "Is it covered by insurance?": "Coverage varies."}'::JSONB,
  'Joint motion and stiffness prevention (surgeon-specific)'
);

COMMIT;
```

---

## Part 3: Verify Data Loaded

```sql
-- Check all categories
SELECT DISTINCT category, COUNT(*) as item_count
FROM dme_items
GROUP BY category
ORDER BY category;

-- Should show:
-- Ambulatory Aids | 3
-- Bathroom Safety | 1
-- Cold Therapy | 3
-- DVT Prevention | 1
-- Knee Bracing | 2
-- Motion Therapy | 1
-- Muscle Rehabilitation | 1

-- Check total
SELECT COUNT(*) as total_dme_items FROM dme_items;
-- Expected: 13

-- Sample query (what patient sees when asking about equipment)
SELECT name, description, common_use_duration, faqs
FROM dme_items
WHERE category = 'Ambulatory Aids'
ORDER BY name;
```

---

## Part 4: Integrate with PT Assistant Chat (Future)

Once loaded, PT Assistant can reference DME when answering:

```
User: "What equipment will I need at home after my knee replacement?"
System: Queries dme_items table → Returns:
- Front-Wheel Walker (1–3 weeks) for safety and early mobility
- Bedside Commode for bathroom safety
- Cold therapy option ($270–$450) for pain/swelling
- DVT prevention device ($295) if physician orders
- CPM machine (rental) if surgeon recommends
```

Chat can say: "Based on typical TKA recovery, you'll likely need [list], though your surgeon may customize based on your situation."

---

## Part 5: Confirmation

After inserting data:

```bash
psql -U $(whoami) -d pt_assistant_dev -c "
SELECT COUNT(*) as dme_items_loaded FROM dme_items;
"

# Expected: 13
```

---

## Success Criteria

- [ ] dme_items table created with correct schema
- [ ] All 13 DME items inserted (3 ambulatory, 1 bathroom, 3 cold therapy, 1 DVT, 2 bracing, 1 motion, 1 muscle)
- [ ] Category index created
- [ ] FAQs stored as JSONB (searchable in chat)
- [ ] Pricing data populated for items with costs
- [ ] Verify query returns all categories with correct counts

**Result**: PT Assistant can now reference post-operative equipment recommendations during TKA recovery chat.