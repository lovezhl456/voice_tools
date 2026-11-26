"""Select current recording evidence while preserving immutable batches and reviews."""

# Physical-channel rules remain valid when the operator changes speaker roles.
# Older batches did not record roles, so they cannot validate a role-dependent rule.
EVALUATIONS = '''
    SELECT e.*, e.rowid AS sequence, d.id AS rule, d.name, d.version,
           m.ai_channel, m.batch_id IS NOT NULL AS mapping_recorded,
           CASE WHEN json_extract(d.config, '$.kind') IN ('response_wait', 'output_pause')
                THEN m.batch_id IS NOT NULL AND m.ai_channel IS :channel
                ELSE 1 END AS applicable
    FROM evaluations e JOIN definitions d ON d.hash=e.config_hash
    LEFT JOIN recording_workbench_batch_channels m ON m.batch_id=e.batch_id
    WHERE e.recording_id=:digest
'''

# A rerun can change or remove evidence. Take only payloads detected by the latest
# applicable evaluation, then prefer the most recent human decision on identical
# original evidence. Independent manual additions always retain their own identity.
CURRENT = f'''
    WITH history AS ({EVALUATIONS}),
    eligible AS (SELECT * FROM history WHERE applicable),
    latest AS (SELECT config_hash, MAX(sequence) AS sequence FROM eligible GROUP BY config_hash),
    hits AS (
        SELECT f.id, f.config_hash, f.payload, e.sequence,
               (SELECT r.created_at FROM reviews r WHERE r.finding_id=f.id
                ORDER BY r.revision DESC LIMIT 1) AS reviewed
        FROM eligible e JOIN findings f USING(batch_id, recording_id, config_hash)
        WHERE f.origin='auto'
    ),
    current_evidence AS (
        SELECT DISTINCT h.config_hash,h.payload
        FROM hits h JOIN latest l ON l.config_hash=h.config_hash AND l.sequence=h.sequence
    ),
    ranked AS (
        SELECT h.id, ROW_NUMBER() OVER (
            PARTITION BY h.config_hash,h.payload ORDER BY h.reviewed DESC,h.sequence DESC,h.id
        ) AS rank
        FROM hits h JOIN current_evidence USING(config_hash,payload)
    )
    SELECT id FROM ranked WHERE rank=1
    UNION ALL SELECT id FROM findings WHERE recording_id=:digest AND origin='manual'
'''


def current_ids(db, digest, channel):
    return {row[0] for row in db.execute(CURRENT, {'digest': digest, 'channel': channel})}


def evaluations(db, digest, channel):
    rows = [dict(row) for row in db.execute(EVALUATIONS + ' ORDER BY e.rowid DESC',
                                            {'digest': digest, 'channel': channel})]
    current = set()
    for row in rows:
        key = row['config_hash']
        row['current'] = bool(row['applicable'] and key not in current)
        if row['current']:
            current.add(key)
        del row['sequence']
    return rows
