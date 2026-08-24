"""
Minimal all-MiniLM-L6-v2 encoder (torch + tokenizers, no sklearn/transformers).

Model weights are downloaded from HuggingFace hub on first call and cached
in ~/.cache/huggingface. Subsequent calls are instantaneous.

Public API:
    encode(texts, batch_size=128) -> torch.Tensor  shape (N, 384), L2-normalised
    load_model()                  -> (model, tokenizer, device)
"""

import math
from pathlib import Path

import torch
import torch.nn.functional as F
from torch import nn
from tokenizers import Tokenizer
from huggingface_hub import hf_hub_download

_REPO   = "sentence-transformers/all-MiniLM-L6-v2"
_H      = 384   # hidden size
_LAYERS = 6
_HEADS  = 12
_FF     = 1536
_VOCAB  = 30522
MAX_LEN = 256   # tokenizer truncation


# ── model ─────────────────────────────────────────────────────────────────────

class _BertLayer(nn.Module):
    def __init__(self):
        super().__init__()
        d = _H // _HEADS
        self._d  = d
        self.q   = nn.Linear(_H, _H)
        self.k   = nn.Linear(_H, _H)
        self.v   = nn.Linear(_H, _H)
        self.o   = nn.Linear(_H, _H)
        self.n1  = nn.LayerNorm(_H)
        self.ff1 = nn.Linear(_H, _FF)
        self.ff2 = nn.Linear(_FF, _H)
        self.n2  = nn.LayerNorm(_H)

    def forward(self, x: torch.Tensor, ext_mask: torch.Tensor) -> torch.Tensor:
        B, T, H = x.shape

        def split(w, t):
            return w(t).view(B, T, _HEADS, self._d).transpose(1, 2)

        a = (split(self.q, x) @ split(self.k, x).transpose(-2, -1)) / math.sqrt(self._d)
        a = F.softmax(a + ext_mask, dim=-1) @ split(self.v, x)
        x = self.n1(x + self.o(a.transpose(1, 2).contiguous().view(B, T, H)))
        return self.n2(x + self.ff2(F.gelu(self.ff1(x))))


class _MiniLM(nn.Module):
    def __init__(self):
        super().__init__()
        self.we     = nn.Embedding(_VOCAB, _H, padding_idx=0)
        self.pe     = nn.Embedding(512, _H)
        self.te     = nn.Embedding(2, _H)
        self.ln     = nn.LayerNorm(_H)
        self.layers = nn.ModuleList([_BertLayer() for _ in range(_LAYERS)])
        self.register_buffer("pid", torch.arange(512).unsqueeze(0))

    def forward(self, ids: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        x = (self.we(ids)
             + self.pe(self.pid[:, : ids.size(1)])
             + self.te(torch.zeros_like(ids)))
        x = self.ln(x)
        ext = (1.0 - mask.float().unsqueeze(1).unsqueeze(2)) * -10000.0
        for layer in self.layers:
            x = layer(x, ext)
        m = mask.float().unsqueeze(-1)
        return F.normalize((x * m).sum(1) / m.sum(1).clamp(min=1e-9), dim=-1)


def _remap(state: dict) -> dict:
    m: dict[str, str] = {
        "embeddings.word_embeddings.weight":       "we.weight",
        "embeddings.position_embeddings.weight":   "pe.weight",
        "embeddings.token_type_embeddings.weight": "te.weight",
        "embeddings.LayerNorm.weight":             "ln.weight",
        "embeddings.LayerNorm.bias":               "ln.bias",
    }
    for i in range(_LAYERS):
        src, dst = f"encoder.layer.{i}", f"layers.{i}"
        m.update({
            f"{src}.attention.self.query.weight":         f"{dst}.q.weight",
            f"{src}.attention.self.query.bias":           f"{dst}.q.bias",
            f"{src}.attention.self.key.weight":           f"{dst}.k.weight",
            f"{src}.attention.self.key.bias":             f"{dst}.k.bias",
            f"{src}.attention.self.value.weight":         f"{dst}.v.weight",
            f"{src}.attention.self.value.bias":           f"{dst}.v.bias",
            f"{src}.attention.output.dense.weight":       f"{dst}.o.weight",
            f"{src}.attention.output.dense.bias":         f"{dst}.o.bias",
            f"{src}.attention.output.LayerNorm.weight":   f"{dst}.n1.weight",
            f"{src}.attention.output.LayerNorm.bias":     f"{dst}.n1.bias",
            f"{src}.intermediate.dense.weight":           f"{dst}.ff1.weight",
            f"{src}.intermediate.dense.bias":             f"{dst}.ff1.bias",
            f"{src}.output.dense.weight":                 f"{dst}.ff2.weight",
            f"{src}.output.dense.bias":                   f"{dst}.ff2.bias",
            f"{src}.output.LayerNorm.weight":             f"{dst}.n2.weight",
            f"{src}.output.LayerNorm.bias":               f"{dst}.n2.bias",
        })
    return {dst: state[src] for src, dst in m.items() if src in state}


# ── singleton ─────────────────────────────────────────────────────────────────

_model:     _MiniLM | None   = None
_tokenizer: Tokenizer | None = None
_device:    torch.device | None = None


def load_model(device: str | None = None):
    """Load model + tokenizer once; subsequent calls return the cached objects."""
    global _model, _tokenizer, _device

    if _model is not None:
        return _model, _tokenizer, _device

    if device is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"

    tok = Tokenizer.from_file(hf_hub_download(_REPO, "tokenizer.json"))
    tok.enable_padding(pad_id=0, pad_token="[PAD]")
    tok.enable_truncation(max_length=MAX_LEN)

    weights = torch.load(
        hf_hub_download(_REPO, "pytorch_model.bin"),
        map_location="cpu",
        weights_only=True,
    )
    model = _MiniLM()
    model.load_state_dict(_remap(weights), strict=False)
    model.eval().to(device)

    _model, _tokenizer, _device = model, tok, torch.device(device)
    return _model, _tokenizer, _device


# ── public API ────────────────────────────────────────────────────────────────

def encode(texts: list[str], batch_size: int = 128) -> torch.Tensor:
    """Encode texts → (N, 384) L2-normalised float32 tensor on CPU."""
    model, tok, device = load_model()
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            enc   = tok.encode_batch(batch)
            ids   = torch.tensor([e.ids            for e in enc], dtype=torch.long, device=device)
            mask  = torch.tensor([e.attention_mask for e in enc], dtype=torch.long, device=device)
            out.append(model(ids, mask).cpu())
    return torch.cat(out)


def to_pgvector(t: torch.Tensor) -> str:
    """Convert a 1-D float tensor to a pgvector literal: '[x,y,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in t.tolist()) + "]"
