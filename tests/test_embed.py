from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from tweetxvault.embed import EmbeddingEngine


def test_embed_batch_returns_l2_normalized_vectors() -> None:
    engine = object.__new__(EmbeddingEngine)
    engine.tokenizer = SimpleNamespace(
        encode_batch=lambda texts: [
            SimpleNamespace(ids=[1, 2], attention_mask=[1, 1]),
            SimpleNamespace(ids=[3, 4], attention_mask=[1, 1]),
        ]
    )
    engine.session = SimpleNamespace(
        run=lambda *_args, **_kwargs: [
            np.array(
                [
                    [[3.0, 4.0, 0.0], [3.0, 4.0, 0.0]],
                    [[0.0, 0.0, 2.0], [0.0, 0.0, 2.0]],
                ],
                dtype=np.float32,
            )
        ]
    )

    vectors = engine.embed_batch(["first", "second"])

    np.testing.assert_allclose(
        vectors,
        np.array(
            [
                [0.6, 0.8, 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        ),
    )
    np.testing.assert_allclose(
        np.linalg.norm(vectors, axis=1),
        np.array([1.0, 1.0], dtype=np.float32),
    )
