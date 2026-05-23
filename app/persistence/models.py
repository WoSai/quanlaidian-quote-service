from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Quote:
    id: str
    org: str
    form_hash: str
    form_json: str
    config_json: str
    factor: float
    total_list: int
    total_final: int
    pricing_version: str
    created_at: str
    idempotency_key: Optional[str] = None


@dataclass
class QuoteRender:
    id: str
    quote_id: str
    format: str  # 'pdf' | 'xlsx' | 'json'
    file_token: str
    filename: str
    created_at: str
    expires_at: str
    short_id: Optional[str] = None


@dataclass
class ApiToken:
    token_id: str
    token_hash: str
    org: str
    created_at: str
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    last_used_on: Optional[str] = None


@dataclass
class Approval:
    id: str
    quote_id: str
    required: bool
    reasons: list[str] = field(default_factory=list)
    state: str = "not_required"  # 'not_required' | 'pending' | 'approved' | 'rejected'
    requested_by: Optional[str] = None
    requested_at: str = ""
    decided_by: Optional[str] = None
    decision_reason: Optional[str] = None
    decided_at: Optional[str] = None
