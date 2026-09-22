"""Public JSON Schema mirrors the v1 contract; semantic references are checked by config-check."""
from .metrics import METRICS


def schema():
    def obj(properties, required=()):
        return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}
    def number(low=0, high=3600, default=None):
        result = {'type': 'number', 'minimum': low, 'maximum': high}
        if default is not None:
            result['default'] = default
        return result
    identity = {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.-]*$', 'maxLength': 80}
    text = {'type': 'string', 'minLength': 1, 'maxLength': 200}
    channel = {'type': 'integer', 'enum': [0, 1]}
    condition = {'oneOf': [
        obj({key: {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': {'$ref': '#/$defs/condition'}}}, [key])
        for key in ('all', 'any')
    ] + [obj({'not': {'$ref': '#/$defs/condition'}}, ['not']),
         obj({'metric': identity, 'op': {'enum': ['lt', 'le', 'gt', 'ge', 'eq', 'ne']},
              'value': number(-1e12, 1e12)}, ['metric', 'op', 'value'])]}
    metric_variants = []
    param_schemas = {}
    for kind, (_, _, params) in METRICS.items():
        param = obj({**{key: number(low, high, default) for key, (default, low, high) in params.items()},
                     'remove_dc': {'type': 'boolean', 'default': True}})
        param_schemas[kind] = param
        metric_variants.append(obj({'kind': {'const': kind}, 'channel': channel, 'params': param}, ['kind', 'channel']))
    window_variants = [obj({'kind': {'const': 'whole'}}, ['kind'])]
    for kind in ('sliding', 'after_activity'):
        props = {'kind': {'const': kind}, 'length_s': number(.02, 3600, 5), 'include_partial': {'type': 'boolean', 'default': False}}
        if kind == 'sliding':
            props['step_s'] = number(.02, 3600)
        else:
            props.update(channel=channel, delay_s=number(0, 120, 0), params=param_schemas['activity_total_s'])
        window_variants.append(obj(props, ['kind']))
    result = obj({
        'schema_version': {'const': '1.0'}, 'id': identity, 'version': identity, 'name': text,
        'description': {'type': 'string', 'minLength': 1, 'maxLength': 4000},
        'scope': obj({'start_s': number(), 'end_s': {'anyOf': [number(), {'type': 'null'}]},
                      'skip_first_s': number(), 'skip_last_s': number(),
                      'exclude': {'type': 'array', 'maxItems': 100, 'items': obj({'start_s': number(), 'end_s': number()}, ['start_s', 'end_s'])}}),
        'window': {'oneOf': window_variants},
        'metrics': {'type': 'object', 'minProperties': 1, 'maxProperties': 32, 'propertyNames': identity,
                    'additionalProperties': {'oneOf': metric_variants}},
        'rules': {'type': 'array', 'minItems': 1, 'maxItems': 32, 'items': obj({
            'id': identity, 'label': {'type': 'string', 'minLength': 1, 'maxLength': 100},
            'description': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
            'when': {'$ref': '#/$defs/condition'}, 'unless': {'$ref': '#/$defs/condition'}}, ['id', 'label', 'when'])}
    }, ['schema_version', 'id', 'version', 'name', 'metrics', 'rules'])
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'voice-tools detection definition 1.0',
            '$defs': {'condition': condition}, **result}
