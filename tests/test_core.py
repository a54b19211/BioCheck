import json
from pathlib import Path
import unittest
from app.core import process

BASE = Path(__file__).resolve().parents[1]


def get_packet(name):
    return json.loads((BASE / 'examples' / name).read_text(encoding='utf-8'))


class FixedEvidenceTests(unittest.TestCase):
    def test_good_answer_passes_implemented_rules(self):
        result = process(get_packet('good.json'))
        self.assertEqual(result['verification']['status'], 'PASS')
        self.assertEqual(result['verification']['runtime_status'], 'OK')

    def test_bad_answer_flags_mismatch(self):
        result = process(get_packet('bad.json'))
        self.assertEqual(result['verification']['status'], 'FAIL')
        labels = {e['type'] for e in result['verification']['issues']}
        self.assertTrue({'OBJ', 'NUM', 'COVERAGE', 'COND'} & labels)

    def test_runtime_error_distinct_from_factual_error(self):
        result = process({'evidence':[{'evidence_id':'E1','metric':'ORR'}]})
        self.assertEqual(result['verification']['runtime_status'], 'ERROR')
        self.assertIsNone(result['verification']['status'])

    def test_answer_and_evidence_version_present(self):
        result = process(get_packet('good.json'))['verification']
        self.assertEqual(result['evidence_version'], 'frozen-2026-10-01')
        self.assertEqual(result['answer_version'], 'draft-001')
        self.assertTrue(result['evidence_hash'])

    def test_template_generation(self):
        result = process(get_packet('good.json'), use_supplied_answer=False)
        self.assertIn('[E-A]', result['answer'])
        self.assertIn('45.2%', result['answer'])
        self.assertEqual(result['verification']['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
