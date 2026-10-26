"""Immutable BPE tokenizer with pyperformance's original training algorithm.

Based on Shantanu Jain's bm_bpe_tokeniser, in turn based on tiktoken.
The published tokenizer owns read-only rank/decoder tables and a compiled
regex. Counters, evolving training words and encoding parts remain private
construction/scratch buffers. No generic dispatch or parallelism is needed.
"""

from collections import Counter
from pathlib import Path
import re
from types import MappingProxyType

import grail

PATTERN = r"""'s|'t|'re|'ve|'m|'ll|'d| ?[a-zA-Z]+| ?\d+| ?[^\sa-zA-Z\d]+|\s+(?!\S)|\s+"""
VOCAB_SIZE = 1024


@grail.record
class Tokenizer:
    pat_str: str
    # Ordinary fields: maps and compiled patterns have no native value plans.
    mergeable_ranks: MappingProxyType
    decoder: MappingProxyType
    pattern: re.Pattern


def make_tokenizer(*, pat_str, mergeable_ranks):
    # Copy caller-owned input: a proxy alone would not isolate later mutations.
    ranks = dict(mergeable_ranks)
    decoder = {token: token_bytes for token_bytes, token in ranks.items()}
    return Tokenizer(pat_str, MappingProxyType(ranks), MappingProxyType(decoder),
                     re.compile(pat_str))


def bpe_encode(mergeable_ranks, data):
    """Uncached quadratic ranked merging, retaining the upstream scratch list."""
    parts = [bytes([byte]) for byte in data]
    while True:
        min_idx = None
        min_rank = None
        for i, pair in enumerate(zip(parts[:-1], parts[1:])):
            rank = mergeable_ranks.get(pair[0] + pair[1])
            if rank is not None and (min_rank is None or rank < min_rank):
                min_idx = i
                min_rank = rank
        if min_rank is None:
            break
        assert min_idx is not None
        parts = parts[:min_idx] + [parts[min_idx] + parts[min_idx + 1]] + parts[min_idx + 2:]
    return [mergeable_ranks[part] for part in parts]


def encode(tokenizer, text):
    words = tokenizer.pattern.findall(text)
    tokens = []
    for word in words:
        tokens.extend(bpe_encode(tokenizer.mergeable_ranks, word.encode("utf-8")))
    # Publish a value, not a mutable output buffer attached to the tokenizer.
    return tuple(tokens)


def decode_bytes(tokenizer, tokens):
    return b"".join(tokenizer.decoder[token] for token in tokens)


def decode(tokenizer, tokens):
    return decode_bytes(tokenizer, tokens).decode("utf-8", errors="replace")


def bpe_train(data, vocab_size, pat_str):
    """Build a private ranks dictionary; make_tokenizer publishes a snapshot.

    Counter insertion order decides frequency ties. Count overlapping pairs,
    then merge occurrences left-to-right without overlap, exactly as upstream.
    """
    if vocab_size < 2**8:
        raise ValueError("vocab_size must be at least 256, so we can encode all bytes")
    ranks = {}
    for i in range(2**8):
        ranks[bytes([i])] = i
    words = [[bytes([byte]) for byte in word.encode("utf-8")]
             for word in re.findall(pat_str, data)]
    while len(ranks) < vocab_size:
        stats = Counter()
        for piece in words:
            for pair in zip(piece[:-1], piece[1:]):
                stats[pair] += 1
        # Preserve ValueError if the corpus cannot supply another pair.
        most_common_pair = max(stats, key=lambda pair: stats[pair])
        token_bytes = most_common_pair[0] + most_common_pair[1]
        ranks[token_bytes] = len(ranks)
        new_words = []
        for word in words:
            new_word = []
            i = 0
            while i < len(word) - 1:
                if (word[i], word[i + 1]) == most_common_pair:
                    new_word.append(token_bytes)
                    i += 2
                else:
                    new_word.append(word[i])
                    i += 1
            if i == len(word) - 1:
                new_word.append(word[i])
            new_words.append(new_word)
        words = new_words
    return ranks


def train(data, vocab_size=VOCAB_SIZE, pat_str=PATTERN):
    return make_tokenizer(pat_str=pat_str,
                          mergeable_ranks=bpe_train(data, vocab_size, pat_str))


def main(data, vocab_size=VOCAB_SIZE):
    """Timed workload: fresh training (1,024 by default), sanity check, full encode.

    Return the model and tokens for verification outside the timed interval.
    Reading the training corpus is deliberately the caller's responsibility.
    """
    tokenizer = train(data, vocab_size)
    tokens = encode(tokenizer, "hello world")
    assert decode(tokenizer, tokens) == "hello world"
    return tokenizer, encode(tokenizer, data)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, help="upstream data/frankenstein_intro.txt")
    args = parser.parse_args()
    model, tokens = main(args.corpus.read_text(encoding="utf-8"))
    print(f"{len(model.mergeable_ranks)} vocabulary entries; {len(tokens)} tokens")
