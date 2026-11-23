"""Checks data against this building's Switchboard manifest.

The manifest (manifest.py) is a promise about what actions answer and what events carry;
Switchboard notes every answer and event that breaks it. The tests use this to hold our own
actions and events to that promise, so a field can't quietly change type.

The rules are the protocol's: a declared field that is present must be of its declared type;
one that is missing or null is fine (a lookup that finds nothing answers only `found`). A `list`
field is an array of objects, each held to the field's `items`.
"""
import datetime

TYPES = ('string', 'int', 'number', 'bool', 'datetime', 'money', 'list')
LIST_LIMIT = 200


def type_error(value, type_):
    """'' when `value` is of a manifest type, else what it should have been."""
    if type_ == 'string':
        return '' if isinstance(value, str) else 'should be text'
    if type_ == 'bool':
        return '' if isinstance(value, bool) else 'should be true or false'
    if type_ == 'int':
        return '' if isinstance(value, int) and not isinstance(value, bool) else 'should be a whole number'
    if type_ in ('number', 'money'):
        return '' if isinstance(value, (int, float)) and not isinstance(value, bool) else 'should be a number'
    if type_ == 'datetime':
        if isinstance(value, str):
            try:
                if datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None:
                    return ''
            except ValueError:
                pass
        return 'should be a date and time with an offset, like 2026-11-19T16:20:04-08:00'
    if type_ == 'list':
        return '' if isinstance(value, list) and all(isinstance(item, dict) for item in value) else 'should be a list of objects'
    return f'has a type the protocol does not know: {type_}'


def dig(data, path):
    for part in path.split('.'):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _nest(path, value):
    for part in reversed(path.split('.')):
        value = {part: value}
    return value


def mismatches(fields, data):
    """Where `data` disagrees with the declared `fields` (a list of manifest field dicts)."""
    if not isinstance(data, dict):
        return ['is not an object']
    found = []
    for field in fields:
        value = dig(data, field['key'])
        if value is None:
            continue
        wrong = type_error(value, field['type'])
        if wrong:
            found.append(f'"{field["key"]}" {wrong}, but was {value!r}')
        elif field['type'] == 'list':
            if len(value) > LIST_LIMIT:
                found.append(f'"{field["key"]}" has {len(value)} items; a list carries at most {LIST_LIMIT}')
            for index, item in enumerate(value):
                found += [f'"{field["key"]}" item {index + 1}: {wrong}' for wrong in mismatches(field.get('items', []), item)]
                found += [f'"{field["key"]}" item {index + 1}: "{key}" is not in the manifest'
                          for key in undeclared(field.get('items', []), item)]
    return found


def undeclared(fields, data, prefix=''):
    """Keys in `data` that the manifest doesn't mention: Switchboard can't offer them to anyone."""
    known = {field['key'] for field in fields}
    found = []
    for key, value in data.items():
        path = f'{prefix}{key}'
        if isinstance(value, dict) and any(k.startswith(path + '.') for k in known):
            found += undeclared(fields, value, path + '.')
        elif path not in known:
            found.append(path)
    return found


def manifest_problems(triggers, actions):
    """Slips in the manifest itself: unknown types, and examples or defaults of the wrong type."""
    found = []
    for where, fields in ([(f'event {t["type"]}', t.get('fields', [])) for t in triggers]
                          + [(f'action {a["name"]} output', a.get('output', [])) for a in actions]
                          + [(f'action {a["name"]} params', a.get('params', [])) for a in actions]):
        for field in fields:
            if field.get('type') not in TYPES:
                found.append(f'{where}: "{field.get("key")}" has no known type')
                continue
            if field['type'] == 'list':
                if 'params' in where:
                    found.append(f'{where}: "{field["key"]}" is a list; params stay single values')
                if not field.get('items'):
                    found.append(f'{where}: the list "{field["key"]}" does not say what its items hold')
                elif any(item.get('type') == 'list' for item in field['items']):
                    found.append(f'{where}: the list "{field["key"]}" has a list inside it')
                else:
                    found += manifest_problems([{'type': f'{where[6:] if where.startswith("event ") else where} {field["key"]}', 'fields': field['items']}], [])
                if not isinstance(field.get('example'), list) or not field['example']:
                    found.append(f'{where}: the list "{field["key"]}" needs an example with at least one item')
                elif mismatches([field], {field['key'].split('.')[-1]: field['example']} if '.' not in field['key'] else _nest(field['key'], field['example'])):
                    found.append(f'{where}: the example for "{field["key"]}" does not match its items')
                continue
            if 'options' in field:
                from .options import SOURCES  # a param's dropdown must be a list the options endpoint serves
                if 'params' not in where or field['options'] not in SOURCES:
                    found.append(f'{where}: "{field["key"]}" names options "{field["options"]}", which Pavilion doesn\'t serve')
            for name in ('example', 'default'):
                if field.get(name) is not None and type_error(field[name], field['type']):
                    found.append(f'{where}: the {name} for "{field["key"]}" {type_error(field[name], field["type"])}')
            if 'params' not in where and 'example' not in field:
                found.append(f'{where}: "{field["key"]}" has no example')
    return found
