"""Immutable BPE models, merge ordering and complete upstream comparisons."""

import random
import re
import unittest
from unittest.mock import patch

import grail
from bench_support import BENCHMARKS, load_benchmark, load_upstream

g = load_benchmark("bpe_tokeniser")


def base_ranks():
    return {bytes([i]): i for i in range(256)}


class BPETests(unittest.TestCase):
    def test_model_snapshot_and_retraining(self):
        ranks = base_ranks()
        ranks[b"ab"] = 256
        model = g.make_tokenizer(pat_str=r"(?s).+", mergeable_ranks=ranks)
        ranks[b"ab"] = 999
        del ranks[b"a"]
        self.assertEqual(g.encode(model, "ab"), (256,))
        self.assertEqual(g.decode(model, (256,)), "ab")
        with self.assertRaises(TypeError):
            model.mergeable_ranks[b"ab"] = 999
        with self.assertRaises(TypeError):
            model.decoder[256] = b"other"
        with self.assertRaises((AttributeError, TypeError)):
            model.pat_str = "changed"
        with self.assertRaises((AttributeError, TypeError)):
            model.pattern.pattern = "changed"
        old = g.train("aaaaa", 257, r".+")
        before = tuple(old.mergeable_ranks.items())
        new = g.train("aaaaa", 258, r".+")
        self.assertEqual(tuple(old.mergeable_ranks.items()), before)
        self.assertEqual(g.encode(old, "aaaaa"), (256, 256, 97))
        self.assertEqual(g.encode(new, "aaaaa"), (257, 97))
        self.assertEqual(g.decode(old, g.encode(old, "aaaaa")), "aaaaa")
        if grail.backend == "native-phase1":
            self.assertFalse(grail.is_value_record(g.Tokenizer))

    def test_training_ties_overlap_and_nonoverlap(self):
        for text, token in (("za ab", b"za"), ("ab za", b"ab"),
                            ("aaaa ab ab ab", b"aa")):
            model = g.train(text, 257, r"\S+")
            self.assertEqual(list(model.mergeable_ranks.items())[256:], [(token, 256)])
        model = g.train("aaaaa", 258, r".+")
        self.assertEqual(list(model.mergeable_ranks.items())[256:],
                         [(b"aa", 256), (b"aaaa", 257)])

    def test_encoder_rank_and_leftmost_ties(self):
        for additions, expected in (({b"ab": 0, b"bc": 0}, [0, 99]),
                                     ({b"ab": 300, b"bc": 299}, [97, 299])):
            ranks = base_ranks() | additions
            self.assertEqual(g.bpe_encode(ranks, b"abc"), expected)
        model = g.make_tokenizer(pat_str=r"(?s).+", mergeable_ranks=base_ranks() | {b"aa": 256})
        self.assertEqual(g.encode(model, "aaaaa"), (256, 256, 97))
        self.assertIsInstance(g.encode(model, "a"), tuple)

    def test_bytes_unicode_and_decode_errors(self):
        model = g.train("", 256)
        raw = bytes(range(256))
        self.assertEqual(g.decode_bytes(model, g.bpe_encode(model.mergeable_ranks, raw)), raw)
        self.assertEqual(g.decode(model, (255,)), "\ufffd")
        for text in ("", "hello world", "can't we've I'll", "123 456\n\t ",
                     "café 漢字🙂\x00\r\n", "e\u0301 — кириллица"):
            self.assertEqual(g.decode(model, g.encode(model, text)), text)
        with self.assertRaises(KeyError):
            g.decode_bytes(model, (9999,))
        with self.assertRaises(UnicodeEncodeError):
            g.encode(model, "\ud800")
        # Duplicate rank IDs retain upstream's last-insertion-wins decoder.
        model = g.make_tokenizer(pat_str=r".+", mergeable_ranks={b"a": 1, b"b": 1})
        self.assertEqual(g.decode_bytes(model, (1,)), b"b")
        with self.assertRaises(KeyError):
            g.encode(model, "c")

    def test_regex_boundaries_and_training_errors(self):
        ranks = base_ranks() | {b"ab": 256}
        model = g.make_tokenizer(pat_str=r".", mergeable_ranks=ranks)
        self.assertEqual(g.encode(model, "ab"), (97, 98))
        model = g.make_tokenizer(pat_str=r"[a-z]+", mergeable_ranks=ranks)
        self.assertEqual(g.decode(model, g.encode(model, "ab 123!")), "ab")
        for size in (0, 255):
            with self.assertRaisesRegex(ValueError, "vocab_size must be at least 256"):
                g.train("anything", size)
        for text, pattern in (("", g.PATTERN), ("a b c", r"\w"), ("ab", r".")):
            with self.assertRaises(ValueError):
                g.train(text, 257, pattern)
        with self.assertRaises(re.error):
            g.train("ab", 256, "[")


class BPEUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = BENCHMARKS / "bpe_tokeniser"
        cls.original = load_upstream("bpe_tokeniser")

    def test_training_sequence_and_random_corpora(self):
        rng = random.Random(614)
        cases = ["", "a", "aaaaa", "za ab", "aaaa ab ab ab", "🙂🙂 漢字 café café"]
        for _ in range(35):
            cases.append("".join(rng.choice("aabcde 123\n!?é🙂") for _ in range(rng.randrange(100))))
        for data in cases:
            for size in (256, 257, 258, 262, 270):
                with self.subTest(data=data, size=size):
                    try:
                        expected = self.original.bpe_train(data, size, g.PATTERN)
                    except ValueError:
                        with self.assertRaises(ValueError):
                            g.bpe_train(data, size, g.PATTERN)
                    else:
                        actual = g.bpe_train(data, size, g.PATTERN)
                        self.assertEqual(list(actual.items()), list(expected.items()))
                        model = g.make_tokenizer(pat_str=g.PATTERN, mergeable_ranks=actual)
                        old = self.original.SimpleBytePairEncoding(
                            pat_str=g.PATTERN, mergeable_ranks=expected)
                        self.assertEqual(g.encode(model, data), tuple(old.encode(data)))
                        self.assertEqual(g.decode_bytes(model, g.encode(model, data)),
                                         old.decode_bytes(old.encode(data)))

    def test_ranked_encoding_and_custom_patterns(self):
        rng = random.Random(877)
        for _ in range(60):
            ranks = base_ranks()
            for _ in range(20):
                token = bytes(rng.choice(b"abcz \xff") for _ in range(rng.randrange(2, 5)))
                ranks[token] = rng.randrange(300)  # ties, including ranks below 256
            data = bytes(rng.choice(b"abcz \xff") for _ in range(rng.randrange(40)))
            self.assertEqual(g.bpe_encode(ranks, data), self.original.bpe_encode(ranks, data))
        for pattern in (r"[a-z]+", r".", r"(?s).+", r"", r"([ab]+)", g.PATTERN):
            model = g.make_tokenizer(pat_str=pattern, mergeable_ranks=base_ranks())
            old = self.original.SimpleBytePairEncoding(pat_str=pattern, mergeable_ranks=base_ranks())
            text = "ab123 abc\n🙂!"
            self.assertEqual(g.encode(model, text), tuple(old.encode(text)))
        for make in (g.make_tokenizer, self.original.SimpleBytePairEncoding):
            model = make(pat_str=r"(a)(b)", mergeable_ranks=base_ranks())
            with self.assertRaises(AttributeError):
                if type(model) is g.Tokenizer:
                    g.encode(model, "ab")
                else:
                    model.encode("ab")

    def test_upstream_workload_contract(self):
        # Check the original's local pattern and exact sequence without retraining.
        calls = []

        class Encoder:
            def encode(self, text):
                calls.append(("encode", text))
                return [1, 2]

            def decode(self, tokens):
                calls.append(("decode", tokens))
                return "hello world"

        with patch.object(self.original.SimpleBytePairEncoding, "train", return_value=Encoder()) as train:
            self.original.train("corpus")
        train.assert_called_once_with("corpus", vocab_size=1024, pat_str=g.PATTERN)
        self.assertEqual(calls, [("encode", "hello world"), ("decode", [1, 2]),
                                 ("encode", "corpus")])
        model = g.train("", 256)
        with patch.object(g, "train", return_value=model) as train, \
             patch.object(g, "encode", wraps=g.encode) as encode, \
             patch.object(g, "decode", wraps=g.decode) as decode:
            result = g.main("corpus")
        train.assert_called_once_with("corpus", 1024)
        self.assertEqual([call.args[1] for call in encode.call_args_list], ["hello world", "corpus"])
        decode.assert_called_once_with(model, g.encode(model, "hello world"))
        self.assertIs(result[0], model)
        self.assertEqual(result[1], g.encode(model, "corpus"))

    def test_full_corpus_model_and_tokens(self):
        source = self.directory / "data/frankenstein_intro.txt"
        data = source.read_text(encoding="utf-8")
        original = self.original.SimpleBytePairEncoding.train(
            data, vocab_size=1024, pat_str=g.PATTERN)
        model, tokens = g.main(data)
        self.assertEqual(len(model.mergeable_ranks), 1024)
        self.assertEqual(list(model.mergeable_ranks.items()), list(original.mergeable_ranks.items()))
        self.assertEqual(list(model.decoder.items()), list(original._decoder.items()))
        self.assertEqual(tokens, tuple(original.encode(data)))
        self.assertEqual(g.decode_bytes(model, tokens), data.encode("utf-8"))
        self.assertEqual(g.decode(model, tokens), data)
        self.assertEqual(g.encode(model, "hello world"), tuple(original.encode("hello world")))
        self.assertEqual(g.decode(model, g.encode(model, "hello world")), "hello world")


if __name__ == "__main__":
    unittest.main()
