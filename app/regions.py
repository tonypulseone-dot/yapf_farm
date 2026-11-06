"""Validate region selections without changing existing keyword/region rows."""
import json
from fastapi import HTTPException


def parse_regions(raw=None, fallback='213'):
    try:
        values = json.loads(raw) if raw is not None else [fallback or '213']
        if not isinstance(values, list) or not values or len(values) > 20:
            raise ValueError()
        result = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError()
            code = str(value).strip()
            if not code.isascii() or not code.isdigit() or not 1 <= int(code) <= 9999999:
                raise ValueError()
            code = str(int(code))
            if code not in result:
                result.append(code)
        return result
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail='Выберите от 1 до 20 регионов с числовыми кодами LR')
