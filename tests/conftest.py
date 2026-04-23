import re

import pytest

REDACTED = "<REDACTED>"

# Cobrem query string (key=...), payload JSON e eventuais tokens em texto.
SENSITIVE_TEXT_PATTERNS = [
    re.compile(r"(?i)([?&](?:key|api[_-]?key|access_token|token)=)([^&#]+)"),
    re.compile(r'(?i)("(?:key|api[_-]?key|access_token|token|authorization)"\s*:\s*")([^"]+)(")'),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9._-]+)"),
]

SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-goog-api-key",
    "x-api-key",
    "api-key",
}


def _mask_sensitive_text(text: str) -> str:
    """Aplica mascaramento de segredos em strings gravadas pelo VCR."""
    masked = text
    for pattern in SENSITIVE_TEXT_PATTERNS:
        if pattern.groups >= 3:
            masked = pattern.sub(rf"\1{REDACTED}\3", masked)
        else:
            masked = pattern.sub(rf"\1{REDACTED}", masked)
    return masked


def _sanitize_vcr_request(request):
    """Censura dados sensíveis antes do request ser gravado em cassette."""
    # 1) Headers (lista de valores por header)
    for header_name, values in list(request.headers.items()):
        if header_name.lower() in SENSITIVE_HEADERS:
            request.headers[header_name] = [REDACTED]
            continue
        request.headers[header_name] = [_mask_sensitive_text(v) for v in values]

    # 2) URL completa
    if getattr(request, "uri", None):
        request.uri = _mask_sensitive_text(request.uri)

    # 3) Body (bytes ou str)
    body = getattr(request, "body", None)
    if body:
        if isinstance(body, bytes):
            decoded = body.decode("utf-8", errors="ignore")
            request.body = _mask_sensitive_text(decoded).encode("utf-8")
        elif isinstance(body, str):
            request.body = _mask_sensitive_text(body)

    return request


@pytest.fixture(scope="module")
def vcr_config():
    """
    Configuração global do VCR.

    Objetivo: impedir vazamento de chaves/tokens em `tests/cassettes/*.yaml`.
    """
    return {
        # Filtros nativos do VCR para casos comuns.
        "filter_headers": sorted(SENSITIVE_HEADERS),
        "filter_query_parameters": ["key", "api_key", "api-key", "access_token", "token"],
        # Sanitização extra para payload JSON/URI em formatos não cobertos pelos filtros nativos.
        "before_record_request": _sanitize_vcr_request,
    }