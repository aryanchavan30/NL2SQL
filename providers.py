from __future__ import annotations
from typing import TYPE_CHECKING
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

if TYPE_CHECKING:
    from config import Settings


def create_llm(settings: Settings) -> BaseChatModel:
    """Create an LLM instance based on ``settings.llm_provider``."""
    provider = settings.llm_provider

    if provider == "ollama":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            base_url=f"{settings.ollama_base_url}/v1",
            api_key="ollama",
            model=settings.ollama_llm_model,
            temperature=0,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            temperature=0,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=0,
        )

    if provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            azure_deployment=settings.azure_openai_llm_deployment,
            api_version=settings.azure_openai_api_version,
            temperature=0,
        )

    raise ValueError(f"Unknown llm_provider: {provider!r}")


def create_embeddings(settings: Settings) -> Embeddings:
    """Create an Embeddings instance based on ``settings.embedding_provider``."""
    provider = settings.embedding_provider

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
        )

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
        )

    if provider == "azure_openai":
        from langchain_openai import AzureOpenAIEmbeddings

        return AzureOpenAIEmbeddings(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            azure_deployment=settings.azure_openai_embedding_deployment,
            api_version=settings.azure_openai_api_version,
        )

    raise ValueError(f"Unknown embedding_provider: {provider!r}")
