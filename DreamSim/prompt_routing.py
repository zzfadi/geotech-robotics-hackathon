"""Explicit table addressing without interpreting the robot's requested action."""
import re


def parse_target(prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('Enter a task')
    text = prompt.strip()
    if len(text) > 1200:
        raise ValueError('Task is too long')
    all_match = re.match(r'^all\s+(?:tables|robots)\b', text, re.I)
    single = re.match(r'^(?:table|robot)\s*#?\s*(\d+)\b', text, re.I)
    if all_match:
        route, tables, label, end = 'fleet', 'all', 'All 10 test tables (2–11)', all_match.end()
    elif single:
        number = int(single.group(1))
        if not 1 <= number <= 11:
            raise ValueError('Use a table number from 1 to 11')
        route = 'main' if number == 1 else 'fleet'
        tables = None if number == 1 else [number-1]
        label, end = 'Table %d' % number, single.end()
    else:
        if (re.match(r'^(?:table\b|robot\s*#?\s*[-+]?\d)', text, re.I)
                or re.search(r'\b(?:tables?|robots?)\s*#?\s*\d|\ball\s+(?:tables|robots)\b|\bT\d{2}\b', text, re.I)):
            raise ValueError('Start with “Table 3:” or “All tables:” to choose the target')
        if len(text) > 1000:
            raise ValueError('Task must be at most 1000 characters')
        return {'route': 'main', 'tables': None, 'prompt': text, 'explicit': False, 'label': 'Table 1 (main)'}
    task = text[end:].lstrip(' \t\r\n:,-–—')
    if not task:
        raise ValueError('Add a task after the table target')
    if re.match(r'^(?:and\b|&|/|\d+\b|(?:table|robot)s?\s*#?\s*\d)', task, re.I):
        raise ValueError('Choose one table or “All tables”; use the table checkboxes for a subset')
    if re.search(r'\b(?:tables?|robots?)\s*#?\s*\d|\ball\s+(?:tables|robots)\b|\bT\d{2}\b', task, re.I):
        raise ValueError('Multiple or conflicting table targets; specify one target at the start')
    if len(task) > 1000:
        raise ValueError('Task must be at most 1000 characters')
    return {'route': route, 'tables': tables, 'prompt': task, 'explicit': True, 'label': label}
