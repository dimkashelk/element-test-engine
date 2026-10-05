"""Scoped task-44 acceptance with evidence isolated from historical receipts."""
import importlib
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO/'tests'),str(REPO)]
OUT = REPO/'result/result-resources-completion'

def main():
    phase=sys.argv[1]
    old=json.loads((REPO/'docs/query-stage-044-measurements.json').read_text())
    legacy=list(old['tests']['latestSuccessfulEvidence'])
    selected={
      'plan':['test_query_results_completion.CompletionPlanTest','test_query_results.ResultPlanTest']+
             [name for name in legacy if not name.startswith('test_query_results.') and name.split('.')[-2].endswith('PlanTest')],
      'docker':['test_query_results_completion.CompletionDockerTest','test_query_results.ResultDockerTest'],
      'legacy':[name for name in legacy if not name.startswith('test_query_results.') and ('DockerTest.' in name or 'DocumentedTest.' in name)],
      'public':['test_query_results_completion.CompletionPublicTest','test_query_results.ResultPublicTest'],
      'sql':['test_query_results_completion.CompletionSqlTest','test_query_results.ResultSqlTest'],
    }[phase]
    for name in selected:
        module=importlib.import_module(name.split('.')[0])
        if module.__name__!='test_query_results_completion' and hasattr(module,'EVIDENCE'):
            module.EVIDENCE=OUT/('fresh-'+module.__name__)
            module.EVIDENCE.mkdir(parents=True,exist_ok=True)
    suite=unittest.defaultTestLoader.loadTestsFromNames(selected)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())

if __name__=='__main__':main()
