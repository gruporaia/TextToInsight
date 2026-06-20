import os
import re
from dataclasses import dataclass

import pytest
from dotenv import load_dotenv

load_dotenv()

REDACTED = "<REDACTED>"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
TEST_PROVIDER_ENV = "TEXT_TO_INSIGHT_TEST_PROVIDER"
TEST_MODEL_ENV = "TEXT_TO_INSIGHT_TEST_MODEL"

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


@dataclass(frozen=True)
class TestLLMProfile:
    provider: str
    model_name: str
    api_key: str
    api_key_env: str
    cassette_suffix: str


def _mask_sensitive_text(text: str) -> str:
    """Aplica mascaramento de segredos em strings gravadas pelo VCR."""
    masked = text
    for pattern in SENSITIVE_TEXT_PATTERNS:
        if pattern.groups >= 3:
            masked = pattern.sub(rf"\1{REDACTED}\3", masked)
        else:
            masked = pattern.sub(rf"\1{REDACTED}", masked)
    return masked


def _slugify_for_filename(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")
    return slug or "default"


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized in {"openai", "gpt"}:
        return "openai"
    if normalized in {"google", "gemini"}:
        return "google"
    pytest.skip(f"Provider de teste '{provider}' inválido. Use 'google' ou 'openai'.")


def _profile_for_provider(provider: str, model_name: str) -> TestLLMProfile:
    api_key_env = "OPENAI_API_KEY" if provider == "openai" else "GOOGLE_API_KEY"
    api_key = os.getenv(api_key_env)
    if not api_key:
        pytest.skip(
            f"{api_key_env} não encontrada. Defina a chave para executar os testes com provider {provider}."
        )

    cassette_suffix = ""
    if not (provider == "google" and model_name == DEFAULT_GEMINI_MODEL):
        cassette_suffix = f"__{provider}-{_slugify_for_filename(model_name)}"

    return TestLLMProfile(
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        api_key_env=api_key_env,
        cassette_suffix=cassette_suffix,
    )


def resolve_test_llm_profile() -> TestLLMProfile:
    explicit_provider = os.getenv(TEST_PROVIDER_ENV)
    explicit_model = os.getenv(TEST_MODEL_ENV)

    if explicit_model:
        model_name = explicit_model.strip()
        lowered = model_name.lower()

        if explicit_provider:
            provider = _normalize_provider(explicit_provider)
        elif "gpt" in lowered or "openai" in lowered:
            provider = "openai"
        elif "gemini" in lowered:
            provider = "google"
        else:
            pytest.skip(
                f"Não foi possível inferir o provider a partir de {TEST_MODEL_ENV}={model_name!r}. Defina também {TEST_PROVIDER_ENV}."
            )

        return _profile_for_provider(provider, model_name)

    if explicit_provider:
        provider = _normalize_provider(explicit_provider)
        model_name = DEFAULT_OPENAI_MODEL if provider == "openai" else DEFAULT_GEMINI_MODEL
        return _profile_for_provider(provider, model_name)

    google_key = os.getenv("GOOGLE_API_KEY")
    if google_key:
        return TestLLMProfile(
            provider="google",
            model_name=DEFAULT_GEMINI_MODEL,
            api_key=google_key,
            api_key_env="GOOGLE_API_KEY",
            cassette_suffix="",
        )

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        return _profile_for_provider("openai", DEFAULT_OPENAI_MODEL)

    pytest.skip("Defina GOOGLE_API_KEY ou OPENAI_API_KEY para executar os testes de API real.")


def _default_cassette_name(test_class, test_name: str) -> str:
    if test_class:
        cassette_name = f"{test_class.__name__}.{test_name}"
    else:
        cassette_name = test_name

    for ch in ["<", ">", "?", "%", "*", ":", "|", '"', "'", "/", "\\"]:
        cassette_name = cassette_name.replace(ch, "-")

    return cassette_name


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

SENSITIVE_RESPONSE_HEADERS = {
    "openai-organization",
    "openai-project",
    "x-request-id",
    "cf-ray",                  # Cloudflare Ray ID (fingerprint da requisição)
    "x-ratelimit-remaining-requests",  # opcional: não vaza key mas revela uso
    "x-ratelimit-remaining-tokens",
}

def _sanitize_vcr_response(response):
    """Censura headers sensíveis da resposta antes de gravar no cassette."""
    headers = response.get("headers", {})
    for header_name in list(headers.keys()):
        if header_name.lower() in SENSITIVE_RESPONSE_HEADERS:
            headers[header_name] = [REDACTED]
    return response


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
        "before_record_response": _sanitize_vcr_response,
    }


@pytest.fixture(scope="session")
def llm_profile():
    return resolve_test_llm_profile()


@pytest.fixture(scope="session")
def llm(llm_profile):
    from text_to_insight.model_selection import get_model

    return get_model(llm_profile.model_name, llm_profile.api_key)


@pytest.fixture
def default_cassette_name(request, llm_profile):
    marker = request.node.get_closest_marker("default_cassette")
    if marker is not None:
        assert marker.args, (
            "You should pass the cassette name as an argument to the `pytest.mark.default_cassette` marker"
        )
        base_name = marker.args[0]
    else:
        base_name = _default_cassette_name(request.cls, request.node.name)

    return f"{base_name}{llm_profile.cassette_suffix}"