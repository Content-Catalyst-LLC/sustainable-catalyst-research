"""Bounded internal metadata diagnostics over an already-authorized snapshot.
These findings identify review work; they make no scientific interpretation.
"""
MAX_OBJECTS = 500


def inspect_metadata(records):
    rows=list(records)
    if len(rows)>MAX_OBJECTS: raise ValueError('Metadata snapshot exceeds limit')
    seen={}; conflicts=[]; missing=[]
    for row in rows:
        key=(row['authority'],row['source_id'],row['source_version'])
        if key in seen:
            for field in ('source_sha256','doi'):
                a,b=seen[key].get(field),row.get(field)
                if a and b and a!=b:
                    conflicts.append({'identity':key,'field':field,'status':'proposed'})
        else: seen[key]=row
        absent=[field for field in ('source_sha256','retrieved_at','license_ref') if not row.get(field)]
        if absent: missing.append({'identity':key,'missing_metadata':absent,'status':'proposed'})
    return {'conflicts':conflicts,'missing_metadata':missing,
            'interpretation':'Metadata review queue; missing metadata does not establish missing scientific evidence.'}


def revision_impact(changed, dependencies):
    """Transitive declared dependents only; no inferred topical evidence edges."""
    edges=list(dependencies)
    if len(edges)>2000: raise ValueError('Dependency snapshot exceeds limit')
    reached={changed}; pending=[changed]
    while pending:
        source=pending.pop()
        for dependent,upstream in edges:
            if upstream==source and dependent not in reached:
                reached.add(dependent); pending.append(dependent)
                if len(reached)>MAX_OBJECTS: raise ValueError('Impact snapshot exceeds limit')
    return {'review_candidates':sorted(reached-{changed}),'status':'proposed',
            'interpretation':'Declared dependency impact; does not invalidate scientific conclusions.'}
