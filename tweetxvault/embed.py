"""ONNX-based text embedding engine."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


class EmbeddingEngine:
    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from huggingface_hub import hf_hub_download

        tok_path = hf_hub_download(model_name, "tokenizer.json")
        model_path = hf_hub_download(model_name, "onnx/model.onnx")
        self._load(tok_path, model_path)

    def _load(self, tok_path: str, model_path: str) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(tok_path)
        self.tokenizer.enable_padding()
        self.tokenizer.enable_truncation(max_length=256)
        self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])

    def embed_batch(self, texts: list[str]) -> NDArray[np.float32]:
        """Embed a batch of texts, returning (N, EMBEDDING_DIM) float32 array."""
        if not texts:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        encoded = self.tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)
        outputs = self.session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )
        token_embeddings = outputs[0]
        mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
        summed = (token_embeddings * mask_expanded).sum(axis=1)
        counts = mask_expanded.sum(axis=1).clip(min=1e-9)
        return (summed / counts).astype(np.float32)
