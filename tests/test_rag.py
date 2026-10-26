"""RAG 청킹 오프라인 테스트."""
import unittest

from kic_data import rag


class RagTest(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(rag.chunk_text("짧은 글"), ["짧은 글"])
        self.assertEqual(rag.chunk_text("   "), [])

    def test_long_text_overlap(self):
        text = "가" * 2000
        chunks = rag.chunk_text(text, size=800, overlap=100)
        self.assertEqual(len(chunks[0]), 800)
        self.assertGreaterEqual(len(chunks), 3)
        # 겹침 확인: 두 번째 청크 시작이 첫 청크의 700번째부터
        self.assertEqual(chunks[1][:100], text[700:800])

    def test_build_chunks_prefixes_title(self):
        out = rag.build_chunks("마이크론 파업", "대만 노조가 22일 파업한다.")
        self.assertEqual(out, ["마이크론 파업\n대만 노조가 22일 파업한다."])
        self.assertEqual(rag.build_chunks("제목만", None), ["제목만"])
        self.assertEqual(rag.build_chunks(None, None), [])

    def test_vec_literal(self):
        self.assertEqual(rag.vec_literal([0.5, 1, -0.25]), "[0.500000,1.000000,-0.250000]")

    def test_unknown_model(self):
        with self.assertRaises(RuntimeError):
            rag.load_embedder("없는모델")


if __name__ == "__main__":
    unittest.main()
