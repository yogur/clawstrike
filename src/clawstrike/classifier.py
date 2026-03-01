"""Prompt injection classifier backed by Llama Prompt Guard 2 models."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from clawstrike.config import ClassifierModel

# HuggingFace model IDs for each ClassifierModel enum value.
_MODEL_IDS: dict[ClassifierModel, str] = {
    ClassifierModel.MULTILINGUAL: "meta-llama/Llama-Prompt-Guard-2-86M",
    ClassifierModel.ENGLISH_ONLY: "meta-llama/Llama-Prompt-Guard-2-22M",
}

# Maximum token window supported by the DeBERTa-based PromptGuard 2 model.
_MAX_TOKENS: int = 512


@dataclass
class ClassifierResult:
    """Result returned by any classifier implementation."""

    score: float  # Probability of MALICIOUS class (0.0–1.0)
    label: str  # "benign" | "injection" | "jailbreak"
    model: str  # HuggingFace model identifier or custom name
    latency_ms: float  # Wall-clock inference time in milliseconds


class BaseClassifier(ABC):
    """Extension point for custom classifiers."""

    @abstractmethod
    def classify(self, text: str) -> ClassifierResult:
        """Classify *text* and return a :class:`ClassifierResult`."""


class PromptGuardClassifier(BaseClassifier):
    """Classifier using a Llama Prompt Guard 2 sequence-classification model."""

    def __init__(self, model_id: str, device: str = "cpu") -> None:
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        self._model_id = model_id
        self._device = device
        self._tokenizer = AutoTokenizer.from_pretrained(model_id)
        self._model = AutoModelForSequenceClassification.from_pretrained(model_id)
        self._model.to(device)
        self._model.eval()

    def _classify_chunks(self, texts: list[str], temperature: float) -> float:
        """Batch-classify a list of text chunks; return the max injection score."""
        import torch
        from torch.nn.functional import softmax

        inputs = self._tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=_MAX_TOKENS,
        )
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with torch.no_grad():
            logits = self._model(**inputs).logits
        scaled_logits = logits / temperature
        probs = softmax(scaled_logits, dim=-1)
        return max(probs[:, 1].tolist())

    def classify(self, text: str, temperature: float = 1.0) -> ClassifierResult:
        start = time.monotonic()
        token_ids = self._tokenizer(text, truncation=False, return_tensors="pt")[
            "input_ids"
        ][0]
        if len(token_ids) <= _MAX_TOKENS:
            chunk_texts = [text]
        else:
            chunks = [
                token_ids[i : i + _MAX_TOKENS]
                for i in range(0, len(token_ids), _MAX_TOKENS)
            ]
            chunk_texts = [
                self._tokenizer.decode(chunk, skip_special_tokens=True)
                for chunk in chunks
            ]
        score: float = self._classify_chunks(chunk_texts, temperature)
        label = "injection" if score > 0.5 else "benign"
        latency_ms = (time.monotonic() - start) * 1000
        return ClassifierResult(
            score=score,
            label=label,
            model=self._model_id,
            latency_ms=latency_ms,
        )


def create_classifier(model: ClassifierModel) -> PromptGuardClassifier:
    """Instantiate and return a :class:`PromptGuardClassifier` for *model*.

    Raises:
        RuntimeError: If the model fails to load (e.g. not downloaded, missing
                      HF token, or corrupted cache).
    """
    model_id = _MODEL_IDS[model]
    try:
        return PromptGuardClassifier(model_id)
    except Exception as exc:
        raise RuntimeError(f"Failed to load classifier {model_id!r}: {exc}") from exc
