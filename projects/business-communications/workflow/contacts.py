"""Resolve contacts from reviewed identity links and live provider snapshots."""

import re
import unicodedata


def name_key(value):
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())


def resolve_destination(contacts, people, line_links, *, channel, environment, value,
                        bot_id=None, case_contacts=None):
    """Return ambiguity, absence, or an explicit confirmed destination."""
    if channel not in {'line', 'email', 'sms'} or environment not in {'test', 'production'} or not value:
        raise ValueError('invalid lookup')
    if case_contacts is not None:
        ids = set(case_contacts.get(value, ()))
    elif value in contacts:
        ids = {value}
    else:
        needle = name_key(value).removesuffix('さん')
        ids = {
            identifier for identifier, contact in contacts.items()
            if any(name_key(name) == needle for name in [
                *contact.get('aliases', ()), people.get(contact.get('people_id'), {}).get('name', '')
            ])
        } if needle else set()
    if len(ids) != 1:
        return {'status': 'review' if ids else 'not_found', 'contact_ids': sorted(ids)}
    identifier = next(iter(ids))
    contact = contacts.get(identifier)
    if not contact or not contact.get('active') or (environment == 'test' and not contact.get('test_owner')):
        return {'status': 'review', 'contact_ids': [identifier]}
    if channel == 'line':
        matches = [link['line_id'] for link in line_links if link.get('contact_id') == identifier
                   and link.get('bot_id') == bot_id and bot_id and link.get('environment') == environment
                   and link.get('confirmed') is True and link.get('active') is True
                   and re.fullmatch(r'U[0-9a-f]{32}', link.get('line_id', ''))]
        for link in line_links:
            if (link.get('line_id') in matches and link.get('contact_id') != identifier
                    and link.get('environment') == environment and link.get('bot_id') == bot_id
                    and link.get('active') is True and link.get('confirmed') is True):
                return {'status': 'review', 'contact_ids': [identifier, link['contact_id']]}
    else:
        current = people.get(contact.get('people_id'), {})
        destinations = current.get('emails' if channel == 'email' else 'phones', [])
        matches = [item['value'] for item in destinations if item.get('value') and item.get('primary')]
        if not matches:
            matches = [item['value'] for item in destinations if item.get('value')]
    confirmed = set(matches)
    if len(confirmed) != 1:
        return {'status': 'review', 'contact_ids': [identifier]}
    return {'status': 'resolved', 'contact_id': identifier, 'destination': next(iter(confirmed))}
