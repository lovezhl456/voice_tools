"""Observed media changes and their uncertainty; not a claim of packet ownership."""
import json


def timeline(observations):
    stages, active = [], {}
    for row in observations:
        data, when, host = row['data'], row['epoch'], row['host']
        if when is None:
            continue
        terminal = data.get('event') in ('CHANNEL_HANGUP_COMPLETE', 'CHANNEL_DESTROY') or data.get('method') in ('BYE', 'CANCEL')
        if terminal:
            for key, stage in list(active.items()):
                if key[0] != host:
                    continue
                if data.get('uuid'):
                    matches = stage.get('uuid') == data['uuid']
                else:
                    tags = {tag for tag in (data.get('from_tag'), data.get('to_tag')) if tag}
                    observed = {tag for tag in stage.get('dialog', []) if tag}
                    matches = bool(tags and observed and observed.issubset(tags))
                    if data.get('method') == 'CANCEL' and len(observed) == 2:
                        matches = False
                if matches:
                    stage['until_epoch'] = min(when, stage['until_epoch']) if stage['until_epoch'] is not None else when
                    stage['ended_by'] = data.get('event') or data.get('method')
                    active.pop(key)
            continue
        descriptions = data.get('media', [])
        for index, media in enumerate(descriptions):
            # Keep branches separate, and close an old offer when the same sender
            # updates its media section. A re-INVITE offer is still only advertised.
            # From/To tags swap when the other endpoint initiates a re-INVITE.
            # Sender address plus unordered dialog tags identifies its media leg.
            tags = tuple(sorted(t for t in (data.get('from_tag'), data.get('to_tag')) if t))
            key = (host, 'sdp', data.get('src'), tags, index)
            if len(tags) == 2:
                for early in list(active):
                    if (len(early) == 5 and early[:3] == key[:3] and early[4] == index
                            and len(early[3]) == 1 and set(early[3]).issubset(tags)):
                        active.pop(early)['until_epoch'] = when
            if key in active:
                old = active[key]
                if old['media'] == media:
                    continue
                old['until_epoch'] = when
            if not media.get('port') or media.get('direction') == 'inactive':
                active.pop(key, None); continue
            stage = {'host': host, 'basis': 'sdp_advertised', 'from_epoch': when, 'until_epoch': None,
                     'media': media, 'dialog': [data.get('from_tag'), data.get('to_tag')],
                     'cseq': data.get('cseq'), 'negotiation': 'answer' if data.get('status_code') else 'offer',
                     'warning': 'SDP observation does not prove accepted negotiation or NAT wire address'}
            stages.append(stage); active[key] = stage
        if data.get('flow'):
            key = (host, 'fs', data.get('uuid'))
            window = data.get('window_seconds', 0 if data.get('evidence') == 'fs_event' else 10)
            start, until = when - window, when + window if window else None
            previous = active.get(key)
            same = previous and previous['flow'] == data['flow'] and previous.get('codecs') == data.get('codecs', {})
            if same and (previous['until_epoch'] is None or start <= previous['until_epoch']):
                if previous['until_epoch'] is not None:
                    previous['until_epoch'] = until
                previous['last_observed_epoch'] = when
                continue
            if previous and (previous['until_epoch'] is None or previous['until_epoch'] > when):
                previous['until_epoch'] = when
            stage = {'host': host, 'uuid': data.get('uuid'), 'basis': data.get('evidence', 'fs_snapshot'),
                     'from_epoch': start, 'until_epoch': until, 'last_observed_epoch': when,
                     'flow': data['flow'], 'codecs': data.get('codecs', {}),
                     'warning': 'Sampled endpoint or event mapping; packet association remains a candidate'}
            stages.append(stage); active[key] = stage
    # Stable ordering is useful for comparing repeated offline analyses.
    return sorted(stages, key=lambda s: (s['from_epoch'], s['host'], json.dumps(s.get('flow', s.get('media')), sort_keys=True)))
