import os
import json
import logging
import requests
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default Configuration
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
DEFAULT_LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "AUTO")  # AUTO, OPENROUTER, OLLAMA, NONE


def is_ollama_online(host: str = "127.0.0.1", port: int = 11434) -> bool:
    """Fast < 1ms socket check to determine if local Ollama service is listening."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.05)
        res = sock.connect_ex((host, port))
        sock.close()
        return res == 0
    except Exception:
        return False


def get_installed_ollama_models(ollama_url: str = DEFAULT_OLLAMA_URL) -> List[str]:
    """Queries Ollama for currently installed local models."""
    try:
        base_url = ollama_url.rsplit("/api", 1)[0]
        res = requests.get(f"{base_url}/api/tags", timeout=1.0)
        if res.status_code == 200:
            models_data = res.json().get("models", [])
            return [m.get("name") for m in models_data if m.get("name")]
    except Exception:
        pass
    return []


class LLMClient:
    """
    Unified LLM Client supporting OpenRouter (free & commercial models) and local Ollama,
    with configurable fallback strategies.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        openrouter_api_key: Optional[str] = None,
        openrouter_model: Optional[str] = None,
        openrouter_url: Optional[str] = None,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
    ):
        self.provider = (provider or os.environ.get("LLM_PROVIDER", DEFAULT_LLM_PROVIDER)).upper()
        self.openrouter_api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.openrouter_model = openrouter_model or os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        self.openrouter_url = openrouter_url or os.environ.get("OPENROUTER_URL", DEFAULT_OPENROUTER_URL)
        self.ollama_url = ollama_url or os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL)
        self.ollama_model = ollama_model or os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self._installed_ollama_models: Optional[List[str]] = None

    def get_installed_models(self) -> List[str]:
        if self._installed_ollama_models is None:
            self._installed_ollama_models = get_installed_ollama_models(self.ollama_url)
        return self._installed_ollama_models

    def get_effective_provider(self) -> str:
        """Determines which provider should be attempted first."""
        if self.provider == "OPENROUTER":
            return "OPENROUTER"
        elif self.provider == "OLLAMA":
            return "OLLAMA"
        elif self.provider == "AUTO":
            if self.openrouter_api_key:
                return "OPENROUTER"
            elif is_ollama_online():
                return "OLLAMA"
        return "NONE"

    def is_available(self, provider_override: Optional[str] = None) -> bool:
        """Checks whether at least one LLM backend is configured and responsive."""
        prov = provider_override or self.provider
        if prov == "OPENROUTER":
            return bool(self.openrouter_api_key)
        elif prov == "OLLAMA":
            return is_ollama_online()
        if self.openrouter_api_key:
            return True
        return is_ollama_online()

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        timeout: float = 12.0,
        provider_override: Optional[str] = None,
    ) -> Optional[str]:
        """
        Executes text generation using active provider with automatic fallback.
        """
        primary = provider_override or self.get_effective_provider()

        if primary == "OPENROUTER":
            res = self._generate_openrouter(
                prompt=prompt,
                system_prompt=system_prompt,
                json_mode=json_mode,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if res:
                return res
            # Fallback to Ollama if OpenRouter failed
            if is_ollama_online():
                logger.info("OpenRouter request failed/unavailable. Falling back to local Ollama...")
                return self._generate_ollama(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    json_mode=json_mode,
                    temperature=temperature,
                    timeout=timeout,
                )

        elif primary == "OLLAMA" or is_ollama_online():
            res = self._generate_ollama(
                prompt=prompt,
                system_prompt=system_prompt,
                json_mode=json_mode,
                temperature=temperature,
                timeout=timeout,
            )
            if res:
                return res
            # Fallback to OpenRouter if configured
            if self.openrouter_api_key:
                logger.info("Ollama request failed. Falling back to OpenRouter...")
                return self._generate_openrouter(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    json_mode=json_mode,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )

        return None

    def _generate_openrouter(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        timeout: float = 12.0,
    ) -> Optional[str]:
        """Call OpenRouter OpenAI-compatible chat completions API."""
        if not self.openrouter_api_key:
            return None

        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Krishna4/LifeTrace-AI",
            "X-Title": "LifeTrace AI",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": self.openrouter_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            res = requests.post(
                self.openrouter_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=timeout,
            )
            if res.status_code == 200:
                data = res.json()
                choices = data.get("choices", [])
                if choices and "message" in choices[0]:
                    return choices[0]["message"].get("content", "").strip()
            else:
                logger.warning(
                    f"OpenRouter API returned HTTP {res.status_code}: {res.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"OpenRouter API error: {e}")

        return None

    def _generate_ollama(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        timeout: float = 12.0,
    ) -> Optional[str]:
        """Call local Ollama generate API with automatic model discovery."""
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        
        # Discover model if configured model fails
        models_to_try = [self.ollama_model]
        installed = self.get_installed_models()
        for m in installed:
            if m not in models_to_try:
                models_to_try.append(m)

        for model_name in models_to_try:
            payload: Dict[str, Any] = {
                "model": model_name,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": temperature},
            }
            if json_mode:
                payload["format"] = "json"

            try:
                res = requests.post(self.ollama_url, json=payload, timeout=timeout)
                if res.status_code == 200:
                    self.ollama_model = model_name
                    return res.json().get("response", "").strip()
            except Exception as e:
                logger.debug(f"Ollama generate error with model '{model_name}': {e}")

        return None


# Global singleton instance helper
_global_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _global_client
    if _global_client is None:
        _global_client = LLMClient()
    return _global_client


def configure_llm_client(
    provider: Optional[str] = None,
    openrouter_api_key: Optional[str] = None,
    openrouter_model: Optional[str] = None,
    ollama_model: Optional[str] = None,
) -> LLMClient:
    global _global_client
    _global_client = LLMClient(
        provider=provider,
        openrouter_api_key=openrouter_api_key,
        openrouter_model=openrouter_model,
        ollama_model=ollama_model,
    )
    return _global_client
