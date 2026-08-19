"""LLM Semantic Extractor using OpenAI, Hugging Face, or Ollama Chat Completions."""

import json
import os
from typing import Any

import httpx

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.storage import ExtensionStore


class MissingAPIKeyError(Exception):
    """Raised when the OpenAI API key is missing."""

    pass


SYSTEM_PROMPT = """You are an expert scientific manuscript analyzer.
Analyze the provided section text and output a JSON object containing the section's rhetorical role, purpose, key terms, atomic facts, and exclusions.

Rhetorical Role:
Classify the section into exactly one of these roles:
- background
- problem_definition
- related_work
- research_gap
- methodology
- dataset
- experimental_setup
- results
- discussion
- limitations
- conclusion
- appendix

Purpose:
A concise, 1-3 sentence statement. Do not just summarize. Instead, identify:
1. What the section accomplishes.
2. What key information it introduces.
3. What it postpones.
4. Its relation to other sections.

Key Terms:
Identify up to 5 highly specific technical terms, acronyms, or methods. Avoid generic words like 'analysis', 'method', or 'results'. Assign a confidence score (0.0 to 1.0) and specify where in the text it was found.

Atomic Facts:
Identify up to 5 must-preserve atomic facts (numbers, equations, parameter values, thresholds, sample sizes, versions). For each fact, provide the exact quote or value, the extraction type ('numeric_constant', 'experimental_condition', or 'semantic_claim'), and a confidence score.

Constraints:
Identify any logical boundaries or prohibited claims. For example, if it explicitly mentions limitations (e.g. 'we do not claim real-time latency'), extract it as a prohibited claim or rhetorical boundary.

You must respond with ONLY a valid JSON object matching the following structure:
{
  "rhetorical_role": "methodology",
  "purpose": "Describe the data sources, preprocessing operations, clustering procedure, and validation protocol required to reproduce the study.",
  "key_terms": [
    {"value": "crisis perception", "confidence": 0.94},
    {"value": "fuzzy inference", "confidence": 0.82}
  ],
  "facts": [
    {"value": "Gaussian kernel uses sigma = 250 m", "type": "numeric_constant", "confidence": 1.0},
    {"value": "Vulnerability and risk are precomputed", "type": "semantic_claim", "confidence": 0.91}
  ],
  "constraints": [
    {"value": "Do not interpret results in this subsection", "type": "rhetorical_boundary", "confidence": 0.76}
  ]
}
"""


def get_api_key(config: AppConfig) -> str | None:
    """Retrieve OpenAI API key from config, env, or SQLite cache."""
    if config.generator and config.generator.api_key and "openai" in config.generator.api_base:
        return config.generator.api_key
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key

    try:
        with ExtensionStore(config.cache.path) as store:
            return store.get_provider_token("openai_semantic")
    except Exception:
        return None


def get_openai_api_key(config: AppConfig) -> str | None:
    """Retrieve OpenAI API key (wrapper)."""
    return get_api_key(config)


def get_hf_api_token(config: AppConfig) -> str | None:
    """Retrieve Hugging Face API token from config, env, or SQLite cache."""
    if config.generator and config.generator.api_key and "huggingface" in config.generator.api_base:
        return config.generator.api_key
    token = os.environ.get("HF_TOKEN") or os.environ.get("HF_API_TOKEN")
    if token:
        return token

    try:
        with ExtensionStore(config.cache.path) as store:
            return store.get_provider_token("huggingface")
    except Exception:
        return None


def prepare_section_text(content: str) -> str:
    """Truncate section content if it is extremely long to conserve tokens."""
    if len(content) > 8000:
        return content[:4000] + "\n\n... [TRUNCATED] ...\n\n" + content[-4000:]
    return content


def extract_semantic_metadata(
    content: str, config: AppConfig, model: str = "gpt-4o-mini"
) -> dict[str, Any]:
    """Queries an LLM to extract structured section card properties, supporting fallbacks."""
    api_base = "https://api.openai.com/v1"
    api_key = None
    target_model = model

    # Determine if user has custom generator configuration
    has_custom_config = False
    if config.generator:
        if (
            config.generator.model != "gpt-4o-mini"
            or config.generator.api_base != "https://api.openai.com/v1"
        ):
            has_custom_config = True

    if has_custom_config:
        api_base = config.generator.api_base
        target_model = config.generator.model
        if config.generator.api_key:
            api_key = config.generator.api_key
        elif "huggingface" in api_base.lower():
            api_key = get_hf_api_token(config)
        else:
            api_key = get_openai_api_key(config)

        if not api_key and "localhost" not in api_base and "127.0.0.1" not in api_base:
            raise MissingAPIKeyError(
                f"API Key/Token required for custom endpoint '{api_base}'. Please set OPENAI_API_KEY, HF_TOKEN, or config api_key."
            )
    else:
        # Fallback Chain Detection
        # 1. Try OpenAI
        openai_key = get_openai_api_key(config)
        if openai_key:
            api_base = "https://api.openai.com/v1"
            api_key = openai_key
            target_model = "gpt-4o-mini"
        else:
            # 2. Try Hugging Face
            hf_token = get_hf_api_token(config)
            if hf_token:
                api_base = "https://api-inference.huggingface.co/v1"
                api_key = hf_token
                target_model = "Qwen/Qwen2.5-Coder-7B-Instruct"
            else:
                # 3. Try Local Ollama
                ollama_base = "http://localhost:11434"
                try:
                    resp = httpx.get(f"{ollama_base}/api/tags", timeout=1.0)
                    if resp.status_code == 200:
                        models_data = resp.json().get("models", [])
                        if models_data:
                            installed_names = [m.get("name", "") for m in models_data]
                            selected_model = None
                            priority = [
                                "qwen2.5-coder",
                                "qwen2.5",
                                "llama3",
                                "phi3",
                                "mistral",
                                "gemma",
                            ]
                            for p in priority:
                                for name in installed_names:
                                    if p in name.lower():
                                        selected_model = name
                                        break
                                if selected_model:
                                    break

                            if not selected_model:
                                selected_model = installed_names[0]

                            api_base = f"{ollama_base}/v1"
                            api_key = "ollama"
                            target_model = selected_model
                        else:
                            raise MissingAPIKeyError(
                                "Ollama is running, but no models have been pulled. Run 'ollama pull phi3' or configure keys."
                            )
                    else:
                        raise MissingAPIKeyError("Ollama returned non-200 response.")
                except (httpx.RequestError, MissingAPIKeyError) as err:
                    raise MissingAPIKeyError(
                        "OpenAI API key not found. No OpenAI API key or Hugging Face token found, and local Ollama is not running.\n"
                        "To use LLM card scaffolding, please configure one of the following:\n"
                        "1. OpenAI Key (export OPENAI_API_KEY='your-key')\n"
                        "2. Hugging Face Token (export HF_TOKEN='your-token')\n"
                        "3. Local Ollama Server (start Ollama and run 'ollama pull phi3')\n"
                        "Otherwise, the builder will fall back to a deterministic offline scan."
                    ) from err

    text = prepare_section_text(content)

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.1,
    }

    if "openai.com" in api_base.lower():
        payload["response_format"] = {"type": "json_object"}

    try:
        url = f"{api_base.rstrip('/')}/chat/completions"
        response = httpx.post(
            url,
            headers=headers,
            json=payload,
            timeout=60.0,
        )
        response.raise_for_status()
        result = response.json()
        response_text = result["choices"][0]["message"]["content"]

        cleaned = response_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        parsed_data: dict[str, Any] = json.loads(cleaned)
        return parsed_data
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            if "openai.com" in url.lower():
                raise MissingAPIKeyError(
                    "OpenAI API key is invalid (Unauthorized 401).\n"
                    "Please configure a valid key using: export OPENAI_API_KEY='your-key' or "
                    "writing-context-rtfm auth openai_semantic <your-key>"
                ) from e
            raise MissingAPIKeyError(
                f"Unauthorized (401) call to {url}. Please check your credentials/tokens."
            ) from e
        raise
    except Exception as e:
        raise RuntimeError(
            f"Semantic inference call failed on model '{target_model}' at '{api_base}': {e}"
        ) from e
