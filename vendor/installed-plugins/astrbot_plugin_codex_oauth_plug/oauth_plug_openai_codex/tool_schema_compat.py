"""Adapt the operation-discriminated memory query contract for Codex.

The executable arguments and the Memory Companion validator are unchanged.
The wire format uses plain JSON objects; its complete per-operation argument
contract remains visible in descriptions instead of nested conditional schema.
"""
from copy import deepcopy
import json


def memory_query_schema(schema):
    original = deepcopy(schema)
    branches = original.get("oneOf") or []
    if not branches:
        return original
    contracts = {}
    for branch in branches:
        fields = branch.get("properties", {})
        operation = fields.get("operation", {}).get("const")
        if operation:
            contract = fields.get("parameters", {})
            if operation not in contracts:
                contracts[operation] = contract
            elif contracts[operation] != contract:
                previous = contracts[operation]
                variants = previous.get("anyOf", [previous])
                if contract not in variants:
                    contracts[operation] = {"anyOf": variants + [contract]}
    if not contracts:
        return original
    operation_schema = deepcopy(original.get("properties", {}).get("operation") or
                                {"type": "string", "enum": list(contracts)})
    contract_text = json.dumps(contracts, ensure_ascii=False, separators=(",", ":"))
    note_schema = original.get("properties", {}).get("query_note", {})
    return {
        "type": "object",
        "properties": {
            "operation": operation_schema,
            "parameters": {
                "type": "object", "properties": {}, "additionalProperties": True,
                "description": "Use the exact JSON argument object for operation. "
                    "Omit irrelevant fields. These per-operation schemas are enforced by "
                    "the local tool, including ACL, evidence references, dates and budgets: "
                    + contract_text,
            },
            "query_note": {
                "type": "object", "properties": {}, "additionalProperties": True,
                "description": "Optional; omit unless recording a short source-linked "
                    "interpretation. The local validator enforces this contract: "
                    + json.dumps(note_schema, ensure_ascii=False, separators=(",", ":")),
            },
        },
        "required": ["operation"],
        "additionalProperties": False,
    }
