"""Validate model proposals before touching durable activity state."""
from __future__ import annotations

import math


def _minutes(value, *, default=0, minimum=0):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        raise ValueError('boolean_delay')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('non_finite_delay')
    return max(minimum, min(720, int(number)))


def normalize_plan(value):
    if not isinstance(value, dict):
        return None
    result = dict(value)
    try: result['wait_minutes'] = _minutes(value.get('wait_minutes'), default=30, minimum=5)
    except (TypeError, ValueError, OverflowError): return None
    for key in ('activities', 'pending_changes'):
        rows = value.get(key) or []
        if not isinstance(rows, list):
            return None
        validated = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row = dict(row)
            try:
                if key == 'activities':
                    row['start_after_minutes'] = _minutes(row.get('start_after_minutes'))
                elif row.get('decision') == 'defer':
                    row['delay_minutes'] = _minutes(row.get('delay_minutes'), default=30, minimum=5)
                else:
                    row['delay_minutes'] = 30
            except (TypeError, ValueError, OverflowError):
                continue
            validated.append(row)
        result[key] = validated
    return result


def next_reconsideration(now, *, wait_minutes, minimum_minutes, due):
    """A long voluntary wait must not hide newly executable capabilities."""
    floor=now+minimum_minutes*60
    proposed=now+max(wait_minutes,minimum_minutes)*60
    for capability in due:
        not_before=float(capability.metadata.get('not_before') or now)
        # Already-available work was part of the model's deliberate choice to
        # wait. Only a future eligibility boundary shortens that choice.
        if not_before > now:
            proposed=min(proposed,max(floor,not_before))
    return proposed
